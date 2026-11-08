"""Runs from pam_exec (as root, with seteuid) for every authentication it is listed for.

It first decides whether the face may be tried at all, then asks the service.
Exit status 0 means the face was verified; anything else, including every
error and timeout, lets PAM continue to the password.

  gdm-password   only while the user's graphical session is locked, so the login
                 screen after boot always asks for the password
  polkit-1       only when the person typed the single letter y as the password
  sudo, sudo-i   only when face-sudo asked for it in the last few seconds
"""

from __future__ import annotations

import os
import pwd
import re
import signal
import socket
import stat
import subprocess
import sys
import time
from typing import Callable, TextIO

from . import protocol
from .config import ConfigError, load

SUDO_MARKER = "head-turn-face-unlock-sudo"
MARKER_SECONDS = 10
CLOCK_SKEW_SECONDS = 1
CONNECT_SECONDS = 2
# Beyond the attempt itself: a cold service loads models while the cameras start.
EXTRA_SECONDS = 5
GRAPHICAL_TYPES = ("wayland", "x11")
QUIET_RESULTS = ("ACCEPTED",)


def locked_graphical_session(user: str, run: Callable = subprocess.run) -> bool:
    def show(*arguments: str) -> str:
        result = run(["loginctl", *arguments], capture_output=True, text=True, timeout=2, check=False)
        return result.stdout if result.returncode == 0 else ""
    for session in show("show-user", user, "--property=Sessions", "--value").split():
        if not re.fullmatch(r"[A-Za-z0-9]+", session):
            continue
        values = dict(line.split("=", 1) for line in show(
            "show-session", session, "--property=Type", "--property=LockedHint").splitlines() if "=" in line)
        if values.get("Type") in GRAPHICAL_TYPES and values.get("LockedHint") == "yes":
            return True
    return False


def take_sudo_marker(uid: int, runtime_directory: str = "/run/user", now: Callable[[], float] = time.time) -> bool:
    """The marker must be a fresh regular file owned by the user. It is used up either way."""
    path = os.path.join(runtime_directory, str(uid), SUDO_MARKER)
    try:
        info = os.lstat(path)
    except OSError:
        return False
    try:
        os.unlink(path)
    except OSError:
        return False
    age = now() - info.st_mtime
    return stat.S_ISREG(info.st_mode) and info.st_uid == uid and -CLOCK_SKEW_SECONDS <= age <= MARKER_SECONDS


def asked_with_letter_y(stdin: "os.PathLike | object") -> bool:
    token = stdin.read(64)  # type: ignore[attr-defined]
    return token.replace(b"\0", b"").strip() in (b"y", b"Y")


def ask_service(user: str, service: str, output: TextIO, deadline: float,
                socket_path: str = protocol.SOCKET_PATH) -> bool:
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(CONNECT_SECONDS)
            connection.connect(socket_path)
            connection.sendall(protocol.encode({"request": "VERIFY", "user": user, "service": service}))
            reader = protocol.Reader(connection, protocol.REQUEST_LIMIT * 64)
            while True:
                message = reader.next(deadline)
                if message["type"] == "MESSAGE":
                    print(protocol.printable(message.get("text", "")), file=output, flush=True)
                elif message["type"] == "RESULT":
                    accepted = message.get("accepted") is True
                    if not accepted and message.get("key") not in QUIET_RESULTS:
                        print(protocol.printable(message.get("text", "")), file=output, flush=True)
                    return accepted
    except (OSError, protocol.ProtocolError):
        return False


def message_output() -> TextIO:
    """The terminal when there is one (sudo), so prompts appear at once; otherwise standard
    output, which pam_exec passes to the login screen."""
    try:
        return open("/dev/tty", "w", encoding="utf-8")
    except OSError:
        return sys.stdout


def main(environ: dict[str, str] | None = None) -> int:
    environ = dict(os.environ if environ is None else environ)
    service, user = environ.get("PAM_SERVICE", ""), environ.get("PAM_USER", "")
    if environ.get("PAM_TYPE") != "auth" or not protocol.valid_user(user):
        return 1
    try:
        account = pwd.getpwnam(user)
        settings = load()
    except (KeyError, ConfigError, OSError):
        return 1
    if service == "gdm-password":
        allowed = locked_graphical_session(user)
    elif service == "polkit-1":
        allowed = asked_with_letter_y(sys.stdin.buffer)
    elif service in ("sudo", "sudo-i"):
        allowed = take_sudo_marker(account.pw_uid)
    else:
        allowed = False
    if not allowed:
        return 1
    limit = settings.ATTEMPT_SECONDS + EXTRA_SECONDS
    signal.signal(signal.SIGALRM, lambda *_: os._exit(1))
    signal.alarm(int(limit) + 2)
    return 0 if ask_service(user, service, message_output(), time.monotonic() + limit) else 1


def run() -> None:
    try:
        status = main()
    except BaseException:  # never let an error turn into anything but "use the password"
        status = 1
    sys.exit(status)
