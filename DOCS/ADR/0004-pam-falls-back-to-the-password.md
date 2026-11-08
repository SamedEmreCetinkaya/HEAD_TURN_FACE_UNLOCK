# ADR-0004 · PAM: face first, every doubt falls back to the password

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-11-09 |
| **Scope** | The PAM line, which services get it, how it is installed and removed |
| **Out of scope** | How the face is judged (ADR-0006) |

## Context

A face check that can lock someone out, or that can be skipped into an accept, is worse than none. PAM
files differ between distributions, and the installer edits files that every login depends on.

## Decision

1. One line before the password module:
   `auth [success=done default=ignore] pam_exec.so quiet stdout seteuid <helper>`
   (`SOURCE/head_turn_face_unlock/pam_config.py:17`). Only a verified face ends the stack; every other
   result, including a crash or a timeout, is ignored and the password is asked.
2. The helper returns non-zero for anything it does not understand: wrong `PAM_TYPE`, an invalid user,
   no answer (`SOURCE/head_turn_face_unlock/pam_helper.py:105-122`).
3. Installed for `sudo`, `sudo-i` and the lock screen (`gdm-password`, only while a session is locked,
   so the boot login still unlocks the keyring with the password). polkit only with `--polkit`, only on
   the plain Debian `pam_unix` stack (`pam_config.py:18-20`, `pam_config.py:63-71`).
4. Every change is marked, saved and reversible: adding twice changes nothing, removing restores the
   file byte for byte (`TESTS/test_pam.py`; property-tested on random files in `TESTS/test_fuzz.py`).

## Consequences

- The worst case of any failure is the password prompt.
- Every attempt goes through the helper first. A person without a template is refused before any camera
  is opened (`SOURCE/head_turn_face_unlock/service.py:109-117`), then asked for the password.
- polkit support is limited to one well-understood stack.

## Options considered

- **`sufficient`:** nearly the same, but it also ends the stack on `new_authtok_reqd`, and the reader
  must know the shorthand. Rejected for the explicit `[success=done default=ignore]`, which names the
  one result that ends the stack.
- **A PAM module in C:** root code in every authenticating program. Rejected (ADR-0001).
- **Face for the first login after boot:** the keyring would stay locked. Rejected.

## Open questions

None.
