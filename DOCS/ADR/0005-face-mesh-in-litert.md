# ADR-0005 · 478-point face mesh run unchanged in LiteRT

| | |
| --- | --- |
| **Status** | Accepted |
| **Date** | 2026-11-09 |
| **Scope** | Which networks find the face and its landmarks, and what runs them |
| **Out of scope** | The rules built on the landmarks (ADR-0006) |

## Context

Version 1 (`7b20621`) found faces with YuNet, which gives five points. The owner judged that unusable
("en az 25 noktalı bir tarama gerekiyor"): five points cannot describe the face outline, nose, lips, chin
or brows, and cannot separate a turning head from a turning picture. MediaPipe's Face Mesh V2 gives 478
points in three dimensions, but it is published as TensorFlow Lite inside a `.task` bundle. The owner's
condition: run it losslessly; converting to ONNX only as a last resort.

## Decision

1. BlazeFace short range and Face Mesh V2 are taken out of the Face Landmarker bundle unchanged and run
   in Google's LiteRT 2.3.0 (`SOURCE/head_turn_face_unlock/landmarks.py:122-132`).
2. Detection and the crop follow MediaPipe's graph: 128×128 letterboxed input, 896 anchors, the box
   enlarged 1.5 times and rotated so the eyes are level, a 256×256 crop for the mesh
   (`landmarks.py:25-45`).
3. SFace stays the recognizer, in OpenCV, aligned with five points taken from the mesh: both iris
   centres, the nose tip and the mouth corners (`SOURCE/head_turn_face_unlock/geometry.py:68`).
4. Each thread owns its interpreters; LiteRT's XNNPACK delegate runs them on the CPU.

## Consequences

Measured on the development machine (Intel Meteor Lake, CPU only):

| Step | Time |
| --- | --- |
| BlazeFace with letterbox | 1.27 ms |
| Face Mesh V2 | 1.9 ms |
| SFace per face | 14.7 ms |

- BlazeFace found the face in 96% of color frames (YuNet 89%) and 84% of infrared frames (YuNet 86%).
- Landmark noise on a still face: 1.1% of the eye distance.
- Aligning SFace with the mesh raised the owner's infrared similarities from 0.80–0.83 to 0.87–0.96.
- LiteRT is a 21 MB wheel per Python version and processor; ADR-0007 covers how it is installed.
- No GPU or NPU: not needed at these times.

## Options considered

- **Convert to ONNX and run in OpenCV DNN:** a conversion step that can change results. The owner made
  it the last resort; not needed.
- **OpenCV's own TFLite importer:** Debian's OpenCV is built without FlatBuffers, so it cannot read
  `.tflite`. Rejected.
- **The `mediapipe` package:** pulls a large dependency tree and its own graph runtime. Rejected.
- **int8 models, OpenCL, region-of-interest tracking instead of detection:** each tried or weighed; no
  gain worth the accuracy or complexity.

## Open questions

None.
