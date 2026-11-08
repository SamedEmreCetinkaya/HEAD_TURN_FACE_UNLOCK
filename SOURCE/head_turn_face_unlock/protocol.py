"""Messages between the service and its root-only clients: one JSON object per line.

Requests are small and parsed strictly. Replies are a stream of MESSAGE and
PROGRESS lines ending with one RESULT line.
"""

from __future__ import annotations

import json
import re
import socket
import time
from dataclasses import dataclass

SOCKET_PATH = "/run/head-turn-face-unlock/verify.sock"
# systemd loads each template file as a credential named this prefix plus the user name.
TEMPLATE_PREFIX = "TEMPLATE_"
REQUEST_LIMIT = 1024
REPLY_LIMIT = 4 << 20
REQUESTS = ("VERIFY", "ENROLL", "STATUS")
MODES = ("NEW", "ADD")
USER_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_.-]{0,31}")
SERVICE_NAME = re.compile(r"[A-Za-z0-9_.-]{1,32}")


class ProtocolError(ValueError):
    pass


@dataclass(frozen=True)
class Request:
    kind: str
    user: str = ""
    service: str = ""
    mode: str = ""


def valid_user(name: str) -> bool:
    return bool(USER_NAME.fullmatch(name)) and name not in (".", "..")


def encode(message: dict) -> bytes:
    return json.dumps(message, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode() + b"\n"


def parse_request(line: bytes) -> Request:
    try:
        data = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        raise ProtocolError("not JSON") from None
    if not isinstance(data, dict) or not all(isinstance(value, str) for value in data.values()):
        raise ProtocolError("a request is an object of strings")
    kind = data.get("request", "")
    allowed = {"VERIFY": {"request", "user", "service"}, "ENROLL": {"request", "user", "mode"},
               "STATUS": {"request"}}.get(kind)
    if allowed is None or set(data) != allowed:
        raise ProtocolError("unknown request or fields")
    request = Request(kind, data.get("user", ""), data.get("service", ""), data.get("mode", ""))
    if kind != "STATUS" and not valid_user(request.user):
        raise ProtocolError("invalid user name")
    if kind == "VERIFY" and not SERVICE_NAME.fullmatch(request.service):
        raise ProtocolError("invalid service name")
    if kind == "ENROLL" and request.mode not in MODES:
        raise ProtocolError("invalid enrollment mode")
    return request


def read_line(connection: socket.socket, limit: int, deadline: float) -> bytes:
    """One line without its newline. Raises ProtocolError past the limit or the deadline."""
    data = bytearray()
    while b"\n" not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ProtocolError("timed out")
        connection.settimeout(remaining)
        try:
            chunk = connection.recv(min(65536, limit + 1 - len(data)))
        except socket.timeout:
            raise ProtocolError("timed out") from None
        except OSError as error:
            raise ProtocolError(f"connection failed: {error.strerror}") from None
        if not chunk:
            raise ProtocolError("connection closed")
        data += chunk
        if len(data) > limit and b"\n" not in data[:limit + 1]:
            raise ProtocolError("line too long")
    line, _, rest = bytes(data).partition(b"\n")
    if rest:
        raise ProtocolError("unexpected data after the line")
    return line


class Reader:
    """Reads reply lines, keeping bytes that arrived after the last complete line."""

    def __init__(self, connection: socket.socket, limit: int = REPLY_LIMIT) -> None:
        self._connection = connection
        self._limit = limit
        self._buffer = bytearray()

    def next(self, deadline: float) -> dict:
        while b"\n" not in self._buffer:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ProtocolError("timed out")
            self._connection.settimeout(remaining)
            try:
                chunk = self._connection.recv(65536)
            except socket.timeout:
                raise ProtocolError("timed out") from None
            except OSError as error:
                raise ProtocolError(f"connection failed: {error.strerror}") from None
            if not chunk:
                raise ProtocolError("connection closed")
            self._buffer += chunk
            if len(self._buffer) > self._limit:
                raise ProtocolError("reply too long")
        line, _, rest = bytes(self._buffer).partition(b"\n")
        self._buffer = bytearray(rest)
        try:
            message = json.loads(line.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            raise ProtocolError("reply is not JSON") from None
        if not isinstance(message, dict) or message.get("type") not in ("MESSAGE", "PROGRESS", "RESULT"):
            raise ProtocolError("unknown reply")
        return message


def printable(text: object, limit: int = 200) -> str:
    """Text safe to show on a terminal or login screen: no control characters."""
    return "".join(character for character in str(text) if character.isprintable())[:limit]
