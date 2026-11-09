"""The verification service: started by its socket, models loaded once, exits when idle.

Only root may connect (socket mode 0600, and the peer's uid is checked).
systemd decrypts the enrolled templates for the service (LoadCredentialEncrypted=),
so neither the service nor its clients handle the encryption key. The service
runs as an unprivileged dynamic user with no network and only the camera devices.
"""

from __future__ import annotations

import os
import select
import socket
import stat
import struct
import sys
import threading
import time
from typing import Callable

from . import VERSION, cameras, messages, protocol, vision
from .models import Models
from .config import Settings
from .session import Enrollment, SessionError, Verification, describe_template
from .protocol import TEMPLATE_PREFIX
from .template import Template, TemplateError, from_json, to_json

MODEL_DIRECTORY = "/usr/local/lib/head-turn-face-unlock/models"
REQUEST_SECONDS = 2.0
MODEL_WAIT_SECONDS = 30.0
ANALYSIS_THREADS = 2


def log(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


class Service:
    def __init__(self, settings: Settings, model_directory: str, credentials_directory: str | None,
                 allowed_uid: int = 0, discover: Callable[[Settings], cameras.CameraPair | None] = cameras.discover,
                 load_models: Callable[[str, int], Models] = Models) -> None:
        self.settings = settings
        self.credentials_directory = credentials_directory
        self.allowed_uid = allowed_uid
        self.discover = discover
        self._models: Models | None = None
        self._model_error = ""
        self._models_ready = threading.Event()

        def load() -> None:
            try:
                self._models = load_models(model_directory, ANALYSIS_THREADS)
            except Exception as error:  # reported to every client; the login falls back to the password
                self._model_error = str(error)
                log(f"MODELS unavailable: {error}")
            finally:
                self._models_ready.set()
        # Loading starts at once so it overlaps with opening the cameras for the first request.
        threading.Thread(target=load, daemon=True, name="models").start()

    def models(self) -> Models:
        if not self._models_ready.wait(MODEL_WAIT_SECONDS) or self._models is None:
            raise vision.ModelError(self._model_error or "models are still loading")
        return self._models

    def serve(self, listener: socket.socket) -> None:
        idle = self.settings.IDLE_EXIT_SECONDS
        # Started by its socket, a connection is already waiting; with no request for the idle
        # time the service exits and the socket starts it again on the next one.
        listener.settimeout(idle or None)
        while True:
            try:
                connection, _ = listener.accept()
            except socket.timeout:
                log("IDLE exiting")
                return
            except OSError as error:
                log(f"LISTENER closed: {error.strerror}")
                return
            with connection:
                self.handle(connection)
            if idle == 0:
                return

    def handle(self, connection: socket.socket) -> None:
        credentials = connection.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"))
        _, uid, _ = struct.unpack("3i", credentials)
        if uid != self.allowed_uid:
            log(f"REFUSED connection from uid {uid}")
            return
        try:
            request = protocol.parse_request(
                protocol.read_line(connection, protocol.REQUEST_LIMIT, time.monotonic() + REQUEST_SECONDS))
        except protocol.ProtocolError as error:
            log(f"REFUSED request: {error}")
            return
        connection.settimeout(None)
        replies = _Replies(connection, self.settings.LANGUAGE)
        try:
            if request.kind == "VERIFY":
                self._verify(request, replies)
            elif request.kind == "ENROLL":
                self._enroll(request, replies)
            else:
                replies.result(True, "STATUS", status=self.status())
        except (BrokenPipeError, ConnectionResetError):
            log(f"{request.kind} user={request.user} client went away")

    def _verify(self, request: protocol.Request, replies: "_Replies") -> None:
        started = time.monotonic()
        prefix = f"VERIFY user={request.user} service={request.service}"
        template = self._load_template(request.user)
        if template is None:
            replies.result(False, "NOT_ENROLLED")
            log(f"{prefix} result=NOT_ENROLLED")
            return
        pair = self.discover(self.settings)
        if pair is None:
            replies.result(False, "CAMERA_MISSING")
            log(f"{prefix} result=CAMERA_MISSING")
            return
        try:
            verification = Verification(self.models(), pair, template, self.settings, replies.say,
                                        cancelled=replies.cancelled)
            verdict = verification.run(lambda result: replies.result(result.accepted, result.reason, hint=result.hint))
        except vision.ModelError:
            replies.result(False, "UNAVAILABLE")
            log(f"{prefix} result=UNAVAILABLE models: {self._model_error}")
            return
        except SessionError as error:
            replies.result(False, error.args[0])
            log(f"{prefix} result={error.args[0]}")
            return
        log(f"{prefix} result={'ACCEPTED' if verdict.accepted else 'REJECTED'} reason={verdict.reason} "
            f"total={time.monotonic() - started:.1f}s {verification.statistics} | {verdict.details}")

    def _enroll(self, request: protocol.Request, replies: "_Replies") -> None:
        prefix = f"ENROLL user={request.user} mode={request.mode}"
        existing = self._load_template(request.user) if request.mode == "ADD" else None
        if request.mode == "ADD" and existing is None:
            replies.result(False, "NOT_ENROLLED")
            return
        pair = self.discover(self.settings)
        if pair is None:
            replies.result(False, "CAMERA_MISSING")
            return
        try:
            enrollment = Enrollment(self.models(), pair, self.settings, replies.say, replies.progress, existing,
                                    cancelled=replies.cancelled)
            template = enrollment.run()
        except vision.ModelError:
            replies.result(False, "UNAVAILABLE")
            return
        except (SessionError, TemplateError) as error:
            key = error.args[0] if isinstance(error, SessionError) else "ENROLL_DIRECTION_MISMATCH"
            replies.result(False, key)
            log(f"{prefix} result={key}")
            return
        replies.result(True, "ENROLL_DONE", template=to_json(template),
                       summary=describe_template(template), skipped=enrollment.skipped)
        log(f"{prefix} result=DONE {describe_template(template)}")

    def _load_template(self, user: str) -> Template | None:
        if not self.credentials_directory:
            return None
        path = os.path.join(self.credentials_directory, TEMPLATE_PREFIX + user)
        try:
            descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
        except OSError:
            return None
        try:
            if not stat.S_ISREG(os.fstat(descriptor).st_mode):
                return None
            with os.fdopen(descriptor, "rb", closefd=False) as handle:
                text = handle.read(1 << 21)
        finally:
            os.close(descriptor)
        try:
            return from_json(text)
        except TemplateError as error:
            log(f"TEMPLATE user={user} unreadable: {error}")
            return None

    def enrolled_users(self) -> list[str]:
        if not self.credentials_directory:
            return []
        try:
            names = os.listdir(self.credentials_directory)
        except OSError:
            return []
        return sorted(name[len(TEMPLATE_PREFIX):] for name in names if name.startswith(TEMPLATE_PREFIX))

    def status(self) -> dict:
        pair = self.discover(self.settings)
        self._models_ready.wait(MODEL_WAIT_SECONDS)
        return {
            "version": VERSION,
            "models": "ready" if self._models is not None else f"unavailable: {self._model_error}",
            "cameras": None if pair is None else {
                "color": f"{pair.color_path} {pair.color_mode.pixel_format} {pair.color_mode.width}x"
                         f"{pair.color_mode.height} {pair.color_mode.frames_per_second:g} fps",
                "infrared": f"{pair.infrared_path} {pair.infrared_mode.pixel_format} {pair.infrared_mode.width}x"
                            f"{pair.infrared_mode.height} {pair.infrared_mode.frames_per_second:g} fps"},
            "enrolled": self.enrolled_users(),
        }


class _Replies:
    def __init__(self, connection: socket.socket, language: str) -> None:
        self._connection = connection
        self._language = language
        self._poller = select.poll()
        self._poller.register(connection.fileno(), select.POLLIN | select.POLLHUP)
        self.delivered = False

    def _send(self, message: dict) -> None:
        self._connection.sendall(protocol.encode(message))

    def say(self, key: str) -> None:
        self._send({"type": "MESSAGE", "key": key, "text": messages.text(key, self._language)})

    def progress(self, percentages: list[int]) -> None:
        self._send({"type": "PROGRESS", "percentages": percentages})

    def result(self, accepted: bool, key: str, hint: str = "", **extra: object) -> None:
        """The verdict; a hint key adds its text after the reason's, for example what to do."""
        if self.delivered:
            return
        self.delivered = True
        text = messages.text(key, self._language)
        if hint:
            text += ": " + messages.text(hint, self._language)
        self._send({"type": "RESULT", "accepted": accepted, "key": key, "text": text, **extra})

    def cancelled(self) -> bool:
        """True once the client has hung up (for example sudo was interrupted)."""
        return bool(self._poller.poll(0))


def listener_from_systemd() -> socket.socket | None:
    if os.environ.get("LISTEN_PID") != str(os.getpid()) or os.environ.get("LISTEN_FDS") != "1":
        return None
    return socket.socket(fileno=3)


def main(argv: list[str]) -> int:
    from .config import ConfigError, load
    try:
        settings = load()
    except (ConfigError, OSError) as error:
        log(f"CONFIG invalid: {error}")
        return 1
    listener = listener_from_systemd()
    if listener is None:
        log("this service is started by head-turn-face-unlock.socket")
        return 1
    from .models import runtime_on_path
    runtime_on_path()
    Service(settings, MODEL_DIRECTORY, os.environ.get("CREDENTIALS_DIRECTORY")).serve(listener)
    return 0

