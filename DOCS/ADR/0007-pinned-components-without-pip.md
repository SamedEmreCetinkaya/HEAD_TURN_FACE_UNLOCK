# ADR-0007 · Pinned components, unpacked without pip

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-11-09 |
| **Scope** | How the models and LiteRT reach the machine |
| **Out of scope** | Distribution packages (OpenCV, NumPy come from the system) |

## Context

The networks and the runtime are not ours and are not stored in this repository. Whatever is
downloaded ends up in the authentication path, so it must be exactly the published file. `pip install`
resolves dependencies at install time: `ai-edge-litert` 2.3.0 declares seven (`numpy`, `flatbuffers`,
`protobuf`, `ml_dtypes`, `tqdm`, `typing-extensions`, `backports.strenum`, from its `METADATA`), each a
new unpinned download into the authentication path.

## Decision

1. Every download is pinned by URL, size and SHA-256 in one file
   (`SOURCE/head_turn_face_unlock/model_files.py:21-69`), and accepted only if both match
   (`SCRIPTS/FETCH_COMPONENTS.py:52-69`).
2. The two networks are extracted from the bundle and checked again by their own hashes
   (`model_files.py:28-31`); the service checks the installed files before loading them.
3. LiteRT 2.3.0 is installed by unpacking its wheel, without pip and without running anything from it:
   absolute paths, `..` and backslashes are refused, files are written `0644` into `runtime.new`, which
   replaces the old directory only when complete (`SCRIPTS/FETCH_COMPONENTS.py:119-159`).
4. Wheels are listed for Python 3.10–3.14 on x86_64 and aarch64; anything else stops the installer with
   a clear message.
5. `--components DIRECTORY` installs from local copies, checked the same way (offline machines).

## Consequences

- A changed file on a server or in transit stops the installation.
- A new LiteRT or model version needs a new pin and a commit; `TESTS/test_documentation.py` makes
  `THIRD_PARTY.md` list every pinned hash.
- The optional dependencies of `ai-edge-litert` are absent; only the interpreter is used, and only NumPy
  is needed (checked by loading both networks inside the service sandbox).

## Options considered

- **`pip install ai-edge-litert`:** six unpinned dependencies as root, and it competes with the
  distribution's packages. Rejected.
- **Copying the models into the repository:** 42 MB of third-party binaries in Git, and a second place
  to keep licences. Rejected.
- **Trusting HTTPS alone:** does not detect a replaced file on the server. Rejected.

## Open questions

None.
