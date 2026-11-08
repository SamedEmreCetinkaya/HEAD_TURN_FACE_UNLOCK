# ADR-0003 · Face templates encrypted with systemd-creds

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-11-09 |
| **Scope** | How a person's face template is stored and reaches the service |
| **Out of scope** | What the template contains (ADR-0006) |

## Context

A face template is biometric data: it cannot be changed like a password once it leaks. The service
runs as a dynamic user (ADR-0001) and must not be able to read or write files in `/etc`.

## Decision

1. Templates are encrypted with `systemd-creds` (TPM2 and the host key when a TPM exists), one file per
   person in `/etc/head-turn-face-unlock/templates`, mode `0700` directory
   (`SOURCE/head_turn_face_unlock/cli.py:24`, `cli.py:98-106`).
2. The credential name is bound into the encryption: `--name=TEMPLATE_<user>`, so a file renamed to
   another person does not decrypt (`cli.py:99-106`).
3. A new template never touches the disk unencrypted: the service sends it over the socket to the root
   command, which pipes it into `systemd-creds` and decrypts the result once to check it (`cli.py:104-106`).
4. systemd decrypts the templates for the service with `LoadCredentialEncrypted`
   (`SYSTEMD/head-turn-face-unlock.service:12`); the service reads only `$CREDENTIALS_DIRECTORY`.
5. The template parser is strict: size limit, known keys only, no repeated keys, finite numbers, unit
   vectors (`SOURCE/head_turn_face_unlock/template.py:27-32`, `template.py:103-112`).

## Consequences

- A copied disk or a backup does not reveal the face on another machine.
- A corrupt template stops the service from starting; logins continue with the password (checked in a
  virtual machine before `7b20621`, see README "Tests").
- Changing the templates requires root and the password every time (sudoers `timestamp_timeout=0`,
  README "Security").

## Options considered

- **Plain files readable by the service user:** no protection at rest. Rejected.
- **The service writes templates itself:** needs a writable path and the encryption key inside the
  sandbox. Rejected.

## Open questions

None.
