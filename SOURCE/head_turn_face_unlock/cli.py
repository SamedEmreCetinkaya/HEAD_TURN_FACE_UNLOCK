"""head-turn-face-unlock: enroll, add to, test, remove and inspect face enrollments (root only).

Templates are encrypted with systemd-creds (bound to the TPM2 chip when there
is one, and to this installation) and never touch the disk unencrypted: the
service hands the new template to this command over its root-only socket and
it goes straight into systemd-creds on a pipe.
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import time

from . import VERSION, protocol
from .protocol import TEMPLATE_PREFIX
from .config import CONFIG_PATH, ConfigError, load
from .template import TemplateError, from_json

TEMPLATE_DIRECTORY = "/etc/head-turn-face-unlock/templates"
SERVICE_UNIT = "head-turn-face-unlock.service"
SOCKET_UNIT = "head-turn-face-unlock.socket"


def _user(arguments: argparse.Namespace) -> str:
    user = arguments.user or os.environ.get("SUDO_USER", "")
    if not protocol.valid_user(user) or user == "root":
        raise SystemExit("Name the person with --user (run through sudo to default to yourself).")
    return user


def _request(message: dict, seconds: float) -> tuple[socket.socket, protocol.Reader, float]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(5)
    try:
        connection.connect(protocol.SOCKET_PATH)
    except OSError as error:
        connection.close()
        raise SystemExit(f"The service is not reachable ({error.strerror}). Is {SOCKET_UNIT} enabled?") from None
    connection.sendall(protocol.encode(message))
    return connection, protocol.Reader(connection), time.monotonic() + seconds


def _follow(reader: protocol.Reader, deadline: float) -> dict:
    progress_shown = False
    while True:
        message = reader.next(deadline)
        if message["type"] == "PROGRESS":
            percentages = [int(value) for value in message.get("percentages", [])][:16]
            total = sum(percentages) // max(1, len(percentages))
            bars = " ".join(f"{value:3d}%" for value in percentages)
            print(f"\r\033[K  left {bars} right | total {total}%", end="", flush=True)
            progress_shown = True
            continue
        if progress_shown:
            print()
            progress_shown = False
        if message["type"] == "MESSAGE":
            print(protocol.printable(message.get("text", "")), flush=True)
        else:
            return message


def _stop_service() -> None:
    """The service reads templates when it starts; stopping it makes the next request reload them."""
    subprocess.run(["systemctl", "stop", SERVICE_UNIT], check=False, stderr=subprocess.DEVNULL)


def _template_path(user: str) -> str:
    return os.path.join(TEMPLATE_DIRECTORY, user)


def _store(user: str, text: str) -> None:
    from_json(text)
    os.makedirs(TEMPLATE_DIRECTORY, mode=0o700, exist_ok=True)
    name = TEMPLATE_PREFIX + user
    # Written beside the directory first: systemd loads every file inside it.
    staging = os.path.join(os.path.dirname(TEMPLATE_DIRECTORY), f".{name}.new")
    old_mask = os.umask(0o077)
    try:
        subprocess.run(["systemd-creds", "encrypt", f"--name={name}", "-", staging],
                       input=text.encode(), check=True, stdout=subprocess.DEVNULL)
        check = subprocess.run(["systemd-creds", "decrypt", f"--name={name}", staging, "-"],
                               capture_output=True, check=True)
        if check.stdout.decode() != text:
            raise SystemExit("The encrypted template did not decrypt to the same content; nothing was changed.")
        os.chmod(staging, 0o600)
        os.replace(staging, _template_path(user))
    finally:
        os.umask(old_mask)
        if os.path.exists(staging):
            os.unlink(staging)


def enroll(arguments: argparse.Namespace, mode: str) -> int:
    user = _user(arguments)
    if mode == "ADD" and not os.path.exists(_template_path(user)):
        print(f"{user} has no enrollment yet; run: head-turn-face-unlock enroll", file=sys.stderr)
        return 1
    settings = load()
    connection, reader, deadline = _request({"request": "ENROLL", "user": user, "mode": mode},
                                            settings.ENROLL_SECONDS + 30)
    with connection:
        result = _follow(reader, deadline)
    if not result.get("accepted"):
        print(protocol.printable(result.get("text", "")), file=sys.stderr)
        return 1
    try:
        _store(user, str(result.get("template", "")))
    except (subprocess.CalledProcessError, TemplateError) as error:
        print(f"The template could not be stored: {error}", file=sys.stderr)
        return 1
    _stop_service()
    skipped = result.get("skipped") or {}
    if any(skipped.values()):
        print(f"Samples that did not look like the enrolled person were skipped: "
              f"color {skipped.get('color', 0)}, infrared {skipped.get('infrared', 0)}")
    print(f"{protocol.printable(result.get('text', ''))}: {protocol.printable(result.get('summary', ''))}")
    return 0


def test(arguments: argparse.Namespace) -> int:
    user = _user(arguments)
    settings = load()
    connection, reader, deadline = _request({"request": "VERIFY", "user": user, "service": "test"},
                                            settings.ATTEMPT_SECONDS + 10)
    with connection:
        result = _follow(reader, deadline)
    print(protocol.printable(result.get("text", "")))
    print("Details are in: journalctl -u head-turn-face-unlock.service -n 5")
    return 0 if result.get("accepted") else 1


def remove(arguments: argparse.Namespace) -> int:
    user = _user(arguments)
    try:
        os.unlink(_template_path(user))
    except FileNotFoundError:
        print(f"{user} has no enrollment.")
        return 0
    _stop_service()
    print(f"The enrollment of {user} was removed.")
    return 0


def status(_: argparse.Namespace) -> int:
    print(f"head-turn-face-unlock {VERSION}")
    try:
        load()
        print(f"settings      {CONFIG_PATH}: valid")
    except (ConfigError, OSError) as error:
        print(f"settings      {CONFIG_PATH}: INVALID ({error}); face unlock is off until it is fixed")
    for unit in (SOCKET_UNIT, SERVICE_UNIT):
        state = subprocess.run(["systemctl", "is-active", unit], capture_output=True, text=True).stdout.strip()
        print(f"{unit:34s}{state}")
    try:
        files = sorted(os.listdir(TEMPLATE_DIRECTORY))
    except OSError:
        files = []
    print(f"enrolled      {', '.join(files) if files else 'nobody'}")
    connection, reader, deadline = _request({"request": "STATUS"}, 60)
    with connection:
        result = _follow(reader, deadline)
    report = result.get("status") or {}
    loaded = [protocol.printable(name) for name in report.get("enrolled") or []]
    # Files that systemd could not decrypt are missing here although they exist above.
    print(f"loaded        {', '.join(loaded) if loaded else 'nobody'}")
    print(f"models        {protocol.printable(report.get('models'))}")
    found = report.get("cameras")
    if found:
        print(f"color         {protocol.printable(found.get('color'))}")
        print(f"infrared      {protocol.printable(found.get('infrared'))}")
    else:
        print("cameras       no color and infrared pair found")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="head-turn-face-unlock", description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name, text in (("enroll", "enroll a face, replacing any earlier enrollment"),
                       ("add", "add samples to an enrollment (glasses, other light)"),
                       ("test", "verify a face exactly as a login would"),
                       ("remove", "delete an enrollment")):
        command = commands.add_parser(name, help=text)
        command.add_argument("--user", help="whose face (default: the person who ran sudo)")
    commands.add_parser("status", help="show settings, service, cameras and enrollments")
    arguments = parser.parse_args(argv)
    if os.geteuid() != 0:
        print("Run as root, for example: sudo head-turn-face-unlock " + arguments.command, file=sys.stderr)
        return 1
    try:
        if arguments.command == "enroll":
            return enroll(arguments, "NEW")
        if arguments.command == "add":
            return enroll(arguments, "ADD")
        return {"test": test, "remove": remove, "status": status}[arguments.command](arguments)
    except protocol.ProtocolError as error:
        print(f"The service stopped answering ({error}).", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 130
