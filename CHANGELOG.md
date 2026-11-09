# Changelog

Every change a user or an installer would notice. Versions follow [Semantic Versioning](https://semver.org/):
a template or settings change that needs a new enrollment is a major version. The newest version is on
top; `Unreleased` collects changes until the next version.

## Unreleased

### Changed

- A `TRACK_BROKEN` line in the journal now says which check broke the track (face not found, no frame,
  moved, size changed) and at what angle.
- The prompt asks for a slow turn, and the two refusals that a turn of less than half a second causes
  (`INFRARED_TOO_FEW`, `SHAPE_UNMEASURED`) say to turn more slowly.

## 2.0.0 · 2026-11-09

Enrollments made with version 1 cannot be read; enroll again after upgrading.

### Changed

- Faces are found and measured with MediaPipe's BlazeFace and the 478-point Face Mesh V2, run unchanged
  in LiteRT 2.3.0, instead of YuNet's five points
  ([ADR-0005](DOCS/ADR/0005-face-mesh-in-litert.md)).
- SFace is aligned with five points taken from the mesh.

### Added

- Three-dimensional motion check: the parallax of 47 rigid points must reach 15% of the eye distance
  ([ADR-0006](DOCS/ADR/0006-liveness-and-identity-rules.md)).
- Proportions check: seven ratios of the face against the enrolled ones.
- The infrared reflection is measured inside the face outline from the mesh.
- `SCRIPTS/SHOW_GEOMETRY.py`, a live window with the mesh, the regions, the pose and the measurements.
- Quality gates ([ADR-0009](DOCS/ADR/0009-quality-gates.md)): `make ci` runs lint (mypy and shellcheck
  required) and all tests with per-module coverage floors; `make hooks` runs it before every push;
  `make help` and `make doctor`.
- Tests for the naming standard, for the documents against the code, for thousands of malformed inputs
  and for leaks of files, memory and threads.
- Architecture decision records in `DOCS/ADR`, `THIRD_PARTY.md` and this changelog.
- Turkish README.

### Fixed

- From a root shell opened with `sudo -i`, the administration command now finds the person from the
  login account (`0dd9893`).
- `ProcSubset=pid` hid `/proc/cpuinfo` and switched off LiteRT's XNNPACK delegate; removed from the unit.
- Adding the polkit line twice changed the file again; it is now idempotent.
- A color similarity that could not be measured (NaN) counted differently depending on which frame of
  an angle bin it came from. It now always counts as no match.

## 1.0.0 · 2026-11-09

First release: head-turn verification with a color and an infrared camera, a sandboxed socket-activated
service, templates encrypted with `systemd-creds`, PAM for `sudo` and the lock screen with the password
as fallback (`7b20621`).
