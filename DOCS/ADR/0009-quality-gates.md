# ADR-0009 · Quality gates: one Makefile, coverage ratchet, checked documents

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-11-09 |
| **Scope** | What must pass before a push, and how it is run |
| **Out of scope** | Tests that need root, cameras or a virtual machine (README "Tests") |

## Context

Until `d899984` the gate was `make check` (lint and tests) run by hand, with every lint tool optional
(`git show d899984:Makefile`). Nothing stopped a push
with a failing test, coverage could fall without anyone noticing, and the README's tables (settings,
tests, downloads) could drift from the code. The owner asked for the test and file system of the other
two projects (ADAPTIVE_RESOURCE_MANAGER and a mobile application) to be applied here.

## Decision

1. **One source of truth:** the `Makefile`. `make ci` = lint + all tests with coverage. `make help` is
   generated from the Makefile's header, and `TESTS/test_documentation.py` checks that every target is
   listed there.
2. **pre-push hook:** `.githooks/pre-push` only calls `make ci`; `make hooks` turns it on once per clone.
3. **Lint:** every Python file compiles, `mypy` on the package and `shellcheck` on every shell script
   are required; `ruff` runs when installed.
4. **Coverage ratchet** (`TOOLS/CI/COVERAGE.py`): line coverage per module and in total, rounded down to
   0.1%. Below its floor in `TOOLS/CI/COVERAGE_FLOOR.txt` fails; above raises the floor in the file; a new
   module enters at its measurement. `make coverage-floor` lowers floors deliberately, with the reason in
   the commit. The tests that need the installed networks are skipped while measuring, so a clone
   without them measures exactly the same; `make test` still runs them where they are installed.
5. **Checked documents** (`TESTS/test_documentation.py`): both READMEs list every setting with its
   default and every test file; `THIRD_PARTY.md` lists every pinned hash; both languages have the same
   messages and every message key the code uses exists; decision records are numbered without gaps,
   indexed and have the template's sections.
6. **Bad input and leaks:** `TESTS/test_fuzz.py` feeds thousands of seeded mutations to every parser and
   broken measurements to the decision; `TESTS/test_leak.py` checks open files, memory and threads over
   hundreds of service requests and repeated sessions.
7. **Naming:** ADR-0008, checked by `TESTS/test_naming.py`.

## Consequences

- The first fuzz run found that `max()` over a NaN similarity depended on where the NaN stood; fixed in
  `decision.py:253` together with this record, and the test fails without the fix.
- `make ci` needs `python3-coverage`, `mypy` and `shellcheck`; `make doctor` says how to get them.
  `make test` still needs nothing.
- A legitimate refactor that removes well-tested lines can turn coverage red; then the floor is lowered
  on purpose.
- The first floors (total 79.1%) show where tests are thin: `cli` 24.7%, `models` 45.4%,
  `v4l2_capture` 59.4%, `service` 70.7%. They can only rise from here.
- `make ci` takes 25 seconds on the development machine (`make test` 10–15, coverage 20, lint 3 with a
  warm mypy cache).

## Options considered

- **A hosted CI service only:** the owner's projects run the gate locally before the push; a hosted
  runner can call the same target later. Not needed now.
- **One total coverage number:** a weak module hides in the average. Rejected for per-module floors.
- **A fixed coverage number raised by hand:** gains are forgotten and lost. Rejected for the ratchet.

## Open questions

None.
