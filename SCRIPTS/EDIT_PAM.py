#!/usr/bin/python3 -IB
"""Adds or removes the face unlock line in PAM service files.

Usage: python3 -IB EDIT_PAM.py add|remove [--polkit] [--pam-directory DIR] [--backup-directory DIR]

Files that do not exist are skipped. Every change is written to a new file and
renamed into place, so a crash never leaves a half-written PAM file, and the
previous content is kept in the backup directory.
"""

import argparse
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "SOURCE"))
from head_turn_face_unlock import pam_config  # noqa: E402

SERVICES = ("sudo", "sudo-i", "gdm-password")
VENDOR_POLKIT = ("/usr/lib/pam.d/polkit-1", "/usr/share/pam.d/polkit-1")
POLKIT_ORIGINAL = "polkit-1.original"
POLKIT_FROM_VENDOR = "polkit-1.from-vendor"


def write(path: str, text: str, backup_directory: str) -> None:
    if os.path.exists(path):
        os.makedirs(backup_directory, mode=0o700, exist_ok=True)
        shutil.copy2(path, os.path.join(backup_directory, f"{os.path.basename(path)}.{time.strftime('%Y%m%d_%H%M%S')}"))
    staging = os.path.join(os.path.dirname(path), f".{os.path.basename(path)}.head-turn-face-unlock")
    with open(staging, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.chmod(staging, 0o644)
    os.replace(staging, path)


def add(pam_directory: str, backup_directory: str, polkit: bool) -> int:
    failures = 0
    for service in SERVICES:
        path = os.path.join(pam_directory, service)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        try:
            changed = pam_config.add(text)
        except pam_config.PamError as error:
            print(f"{path}: {error}", file=sys.stderr)
            failures += 1
            continue
        if changed != text:
            write(path, changed, backup_directory)
        print(f"{path}: face first, then the password")
    if polkit:
        failures += add_polkit(pam_directory, backup_directory)
    return 1 if failures else 0


def add_polkit(pam_directory: str, backup_directory: str) -> int:
    path = os.path.join(pam_directory, "polkit-1")
    vendor = next((candidate for candidate in VENDOR_POLKIT if os.path.isfile(candidate)), None)
    if os.path.isfile(path):
        source = path
    elif vendor:
        source = vendor
    else:
        print("polkit-1: no PAM file found; skipped", file=sys.stderr)
        return 1
    try:
        with open(os.path.join(pam_directory, "common-auth"), encoding="utf-8") as handle:
            common = handle.read()
        with open(source, encoding="utf-8") as handle:
            original = handle.read()
        text = pam_config.polkit(common, original)
    except (OSError, pam_config.PamError) as error:
        print(f"polkit-1: {error}; skipped", file=sys.stderr)
        return 1
    os.makedirs(backup_directory, mode=0o700, exist_ok=True)
    if not pam_config.present(original):
        if source == path:
            shutil.copy2(path, os.path.join(backup_directory, POLKIT_ORIGINAL))
        else:
            open(os.path.join(backup_directory, POLKIT_FROM_VENDOR), "w").close()
    write(path, text, backup_directory)
    print(f"{path}: typing only the letter y asks for the face")
    return 0


def remove(pam_directory: str, backup_directory: str) -> int:
    for service in SERVICES:
        path = os.path.join(pam_directory, service)
        if not os.path.isfile(path):
            continue
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        if pam_config.present(text) or pam_config.MARKER in text:
            write(path, pam_config.remove(text), backup_directory)
            print(f"{path}: line removed")
    path = os.path.join(pam_directory, "polkit-1")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
        if pam_config.present(text):
            original = os.path.join(backup_directory, POLKIT_ORIGINAL)
            if os.path.isfile(original):
                write(path, open(original, encoding="utf-8").read(), backup_directory)
                os.unlink(original)
                print(f"{path}: original restored")
            else:
                shutil.copy2(path, os.path.join(backup_directory, f"polkit-1.{time.strftime('%Y%m%d_%H%M%S')}"))
                os.unlink(path)
                marker = os.path.join(backup_directory, POLKIT_FROM_VENDOR)
                if os.path.exists(marker):
                    os.unlink(marker)
                print(f"{path}: removed, the distribution default applies again")
    return 0


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("add", "remove"))
    parser.add_argument("--polkit", action="store_true")
    parser.add_argument("--pam-directory", default="/etc/pam.d")
    parser.add_argument("--backup-directory", default="/var/backups/head-turn-face-unlock")
    arguments = parser.parse_args(argv)
    if arguments.action == "add":
        return add(arguments.pam_directory, arguments.backup_directory, arguments.polkit)
    return remove(arguments.pam_directory, arguments.backup_directory)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
