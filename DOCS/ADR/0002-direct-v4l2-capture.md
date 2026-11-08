# ADR-0002 · Direct V4L2 capture with driver timestamps

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-11-09 |
| **Scope** | How frames are read from the color and infrared cameras |
| **Out of scope** | What is done with the frames |

## Context

The decision rules compare frames of two cameras in time: an infrared frame is matched to the color
pose around the same moment, and the emitter is seen as lit and unlit frames that alternate. OpenCV's
`VideoCapture` hides the driver's timestamps and sequence numbers, may convert every frame and owns its
own buffering, so neither the time of a frame nor a dropped frame can be known.

## Decision

1. Capture talks to V4L2 directly with `ioctl` through `ctypes`: format, frame rate, `mmap` buffers,
   `QBUF`/`DQBUF` (`SOURCE/head_turn_face_unlock/v4l2_capture.py`).
2. Each frame keeps the driver's `CLOCK_MONOTONIC` timestamp and sequence number
   (`v4l2_capture.py:318-329`). They are read **before** the buffer is queued again; reading them after
   gave the next frame's values (bug found and fixed before `7b20621`).
3. Both cameras are waited on with one `poll()` (`v4l2_capture.py:354-360`).
4. The color stream stays MJPEG and is decoded only when a frame is used: at half size for tracking
   (`IMREAD_REDUCED_COLOR_2`, `SOURCE/head_turn_face_unlock/vision.py:70`), at full size only for the
   frames chosen for identity.
5. The ioctl numbers are computed and checked against `<linux/videodev2.h>`
   (`TESTS/test_v4l2_and_cameras.py`).

## Consequences

- Exact timing and gap detection; the decision can refuse a turn with missing frames.
- Half-size decoding costs 0.9 ms against 3.3 ms for a full decode and resize (measured on the
  development machine, 1280×720 MJPEG).
- About 100 lines of `ctypes` structures that must match the kernel ABI (`v4l2_capture.py:54-150`); the
  test above guards them.
- Only cameras that offer MJPEG color and a grey infrared stream are supported.

## Options considered

- **OpenCV `VideoCapture`:** no driver timestamps, hidden conversion. Rejected.
- **GStreamer or libcamera bindings:** large dependencies for two plain UVC cameras. Rejected.

## Open questions

None.
