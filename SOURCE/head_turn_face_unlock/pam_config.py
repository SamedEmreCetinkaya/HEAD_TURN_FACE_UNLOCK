"""Adds and removes this project's line in PAM files, as pure text functions.

The line goes right before the point where a file hands authentication to the
system's shared stack (@include common-auth on Debian and Ubuntu,
"auth include|substack ..." on Fedora, Arch and openSUSE), so everything
above it, such as pam_nologin, still runs first. "[success=done
default=ignore]" means a verified face ends the auth stack and any other
outcome continues to the password exactly as before.
"""

from __future__ import annotations

import re

HELPER = "/usr/local/libexec/head-turn-face-unlock/pam-helper"
MARKER = "# HEAD_TURN_FACE_UNLOCK: face first, then the password (remove with SCRIPTS/UNINSTALL.sh)"
LINE = f"auth\t[success=done default=ignore]\tpam_exec.so quiet stdout seteuid {HELPER}"
POLKIT_LINE = f"auth\t[success=done default=ignore]\tpam_exec.so quiet stdout seteuid expose_authtok {HELPER}"
_HANDS_OVER = re.compile(r"^\s*(@include\s+common-auth\b|auth\s+(include|substack)\s+\S+)")
_DEBIAN_UNIX = re.compile(r"^auth\s+\[success=1 default=ignore\]\s+pam_unix\.so(?P<options>(\s+[a-z_]+)*)\s*$")


class PamError(ValueError):
    pass


def _lines(text: str) -> list[str]:
    return text.splitlines()


def remove(text: str) -> str:
    kept = [line for line in _lines(text) if line != MARKER and HELPER not in line]
    return "\n".join(kept) + ("\n" if text.endswith("\n") or kept else "")


def add(text: str, line: str = LINE) -> str:
    lines = _lines(remove(text))
    for index, current in enumerate(lines):
        if _HANDS_OVER.match(current):
            lines[index:index] = [MARKER, line]
            return "\n".join(lines) + "\n"
    raise PamError("no line hands authentication to the system stack; add the line by hand")


def present(text: str) -> bool:
    return any(HELPER in line for line in _lines(text))


def polkit(common_auth: str, distribution_polkit: str) -> str:
    """A polkit-1 file where typing only the letter y asks for the face.

    pam_exec reads what was typed (expose_authtok). The password module must then
    reuse that answer (try_first_pass), or a real password would be asked for twice,
    so the shared stack is written out here. That is only done when it is the plain
    Debian stack of pam_unix, pam_deny and pam_permit; anything else (fingerprint,
    SSSD, Kerberos) is left alone."""
    auth = [line.strip() for line in _lines(common_auth)
            if line.strip() and not line.lstrip().startswith("#") and line.split()[0] == "auth"]
    normalized = [re.sub(r"\s+", " ", line) for line in auth]
    if len(auth) != 3 or not _DEBIAN_UNIX.match(normalized[0]) \
            or normalized[1:] != ["auth requisite pam_deny.so", "auth required pam_permit.so"]:
        raise PamError("common-auth is not the plain Debian stack; polkit is left unchanged")
    options = _DEBIAN_UNIX.match(normalized[0]).group("options").split()  # type: ignore[union-attr]
    if "try_first_pass" not in options:
        options.append("try_first_pass")
    if not any(re.match(r"^\s*@include\s+common-auth\b", line) for line in _lines(distribution_polkit)):
        raise PamError("the polkit-1 file does not include common-auth")
    body = []
    for line in _lines(remove(distribution_polkit)):
        if re.match(r"^\s*@include\s+common-auth\b", line):
            body += [MARKER, POLKIT_LINE, f"auth\t[success=1 default=ignore]\tpam_unix.so {' '.join(options)}",
                     "auth\trequisite\t\t\tpam_deny.so", "auth\trequired\t\t\tpam_permit.so"]
        else:
            body.append(line)
    return "\n".join(body) + "\n"
