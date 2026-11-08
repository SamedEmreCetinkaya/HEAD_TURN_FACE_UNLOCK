# ADR-0008 · Naming standard

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-11-09 |
| **Scope** | Names of directories, files, settings, constants and installed things |
| **Out of scope** | Words inside messages and documents |

## Context

The owner's projects share one rule: English names, `UPPER_SNAKE_CASE` for the project, its directories,
configuration and keys, one clean way to do each thing. Installed commands and units must still look
like what the file system hierarchy and systemd expect.

## Decision

| Item | Format | Example |
| --- | --- | --- |
| Project, directories, configuration, scripts, settings keys, constants | `UPPER_SNAKE_CASE` | `SCRIPTS/INSTALL.sh`, `MATCH_THRESHOLD` |
| Installed commands and systemd units | `lower-kebab-case` | `head-turn-face-unlock.service` |
| Python modules, functions, variables | `lower_snake_case` | `pam_helper.py` |
| Python classes | `CapWords` | `FaceFinder` |
| Decision records | `NNNN-lower-kebab.md` | `0008-naming-standard.md` |
| Standard project files | Conventional | `README.md`, `Makefile`, `LICENSE`, `.githooks/pre-push` |

`TESTS/test_naming.py` checks every directory and file against this table, every module-level name in
the package, and every settings key.

## Consequences

- A misnamed file or setting fails `make ci` instead of waiting for review.
- A new kind of file needs a line in the test and in this table.

## Options considered

- **Python's usual lower-case directories:** breaks the owner's standard across projects. Rejected.
- **`UPPER_SNAKE_CASE` installed commands:** surprises every user of `sudo` and `systemctl`. Rejected.

## Open questions

None.
