# Architecture decision records

This directory records **why** the system is built the way it is. Each file is one decision, or a
set of decisions that belong together: what was known that day, the path chosen, the options
rejected and what the decision costs.

## Why records and not a description

A text that says "the system works like this" starts to lie on the day the code changes, and nobody
notices. A decision record does not have that problem, because it describes **a moment**: "on this
day we knew this and chose that." The code may change later; the record stays true, and what changes
is its **status**. The description of the system as it is today lives in the README, and
`TESTS/test_documentation.py` holds the README's tables to the code.

## Rules

1. **An accepted record is not edited.** Only typing errors are fixed. If the decision changes, a new
   record is written and the old one's status becomes `Superseded by ADR-NNNN`.
2. **Numbers are given in order, without gaps, and never reused.** File name: `NNNN-short-title.md`.
   `TESTS/test_documentation.py` checks the numbering, this index and the sections.
3. **Every claim has evidence.** A sentence about the code carries `file:line` or a commit id. What was
   not measured is not claimed; a measurement says on what and how.
4. **Diagrams are Mermaid.** They are versioned as text and GitHub draws them. No image files.
5. **Open questions are written in the record.** A record whose questions belong to the owner (product
   or security decisions) does not become `Accepted` before they are answered.
6. **A record that needs a check is accepted only after the check passes.**

New record: copy [`0000-template.md`](0000-template.md).

## Statuses

| Status | Meaning |
| --- | --- |
| Proposed | Written, waiting for answers or approval. Not applied yet. |
| Accepted | May be applied. The content no longer changes. |
| Rejected | Considered and not chosen. Why it was not chosen stays on record. |
| Superseded by ADR-NNNN | Replaced entirely by a newer decision. |
| Partly superseded by ADR-NNNN | Some sections were changed by a newer record; where they disagree, the newer one holds. |

## Index

| No | Title | Status | Date |
| --- | --- | --- | --- |
| [0001](0001-sandboxed-socket-service.md) | A sandboxed service owns the cameras; PAM only asks it | Accepted | 2026-11-09 |
| [0002](0002-direct-v4l2-capture.md) | Direct V4L2 capture with driver timestamps | Accepted | 2026-11-09 |
| [0003](0003-encrypted-templates.md) | Face templates encrypted with systemd-creds | Accepted | 2026-11-09 |
| [0004](0004-pam-falls-back-to-the-password.md) | PAM: face first, every doubt falls back to the password | Accepted | 2026-11-09 |
| [0005](0005-face-mesh-in-litert.md) | 478-point face mesh run unchanged in LiteRT | Accepted | 2026-11-09 |
| [0006](0006-liveness-and-identity-rules.md) | Liveness and identity rules, from measurements | Accepted | 2026-11-09 |
| [0007](0007-pinned-components-without-pip.md) | Pinned components, unpacked without pip | Accepted | 2026-11-09 |
| [0008](0008-naming-standard.md) | Naming standard | Accepted | 2026-11-09 |
| [0009](0009-quality-gates.md) | Quality gates: one Makefile, coverage ratchet, checked documents | Accepted | 2026-11-09 |
