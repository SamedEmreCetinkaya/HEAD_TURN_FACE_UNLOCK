# Third-party components

Nothing in this table is stored in this repository. `SCRIPTS/FETCH_COMPONENTS.py` downloads each file
at installation and accepts it only if its size and SHA-256 are exactly the ones below (decision:
[ADR-0007](DOCS/ADR/0007-pinned-components-without-pip.md)). `TESTS/test_documentation.py` fails when
this file and `SOURCE/head_turn_face_unlock/model_files.py` disagree.

## Policy

- Only components whose licence allows redistribution and commercial use are accepted: Apache 2.0, MIT,
  BSD. The licence is the one stated by the publisher at the pinned URL (repository licence or model
  card).
- A new version is a new pin: URL, size and SHA-256 change together in `model_files.py`, in this file
  and in `CHANGELOG.md`, in one commit.
- Distribution packages (Python, OpenCV, NumPy, systemd) come from the operating system and follow its
  updates; they are not pinned here.

## Networks

| Component | Licence | File | Bytes | SHA-256 |
| --- | --- | --- | --- | --- |
| SFace face recognizer, OpenCV Zoo | Apache 2.0 | `face_recognition_sface_2021dec.onnx` | 38696353 | `0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79` |
| MediaPipe Face Landmarker bundle, float16, version 1 | Apache 2.0 (model cards) | `face_landmarker.task` | 3758596 | `64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff` |
| BlazeFace short range, taken out of the bundle unchanged | Apache 2.0 (model card) | `face_detector.tflite` | 229746 | `b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f` |
| Face Mesh V2, taken out of the bundle unchanged | Apache 2.0 (model card) | `face_landmarks_detector.tflite` | 2553590 | `c7d54204ce0448474c7f3fa9af494787c0965cbdd6f20fc72867e43046bd43d5` |

Sources:

- SFace: <https://github.com/opencv/opencv_zoo/tree/main/models/face_recognition_sface>; the checksum
  is the Git LFS object id published in that repository.
- Face Landmarker: <https://ai.google.dev/edge/mediapipe/solutions/vision/face_landmarker>; model cards
  for BlazeFace short range and Face Mesh V2 are linked from that page.

## Runtime

LiteRT 2.3.0 (`ai-edge-litert`, Google, Apache 2.0), <https://ai.google.dev/edge/litert>. Only the
interpreter is used; the wheel is unpacked without pip and without its declared dependencies (NumPy
comes from the distribution). One wheel per Python version and processor:

| Python | Processor | Bytes | SHA-256 |
| --- | --- | --- | --- |
| 3.10 | x86_64 | 21776509 | `e104683ee0dead1243002a218637cc435c36fdb01103799335027252314e727b` |
| 3.11 | x86_64 | 21778233 | `1f15078a6725d2357d9de9221664c29afa0545d807559c5f7199ce5e40b96b23` |
| 3.12 | x86_64 | 21781762 | `dc9f56c23e0dcf182b6c35f23fbb102ed048296588882b806d5eff7654c04c88` |
| 3.13 | x86_64 | 21781769 | `985ac3823fe1d5d6a04cf5f613cc2adf98a9c8a456af0b5efa8d9ac6b118ea14` |
| 3.14 | x86_64 | 21781596 | `695f5164b66ebdb0a3bbb4edc4d7024e87c65a0e417d48d0a2db2503bc01b78d` |
| 3.10 | aarch64 | 14377617 | `a72fc35d66c1d8b17f52b195bfb39b89aa839d39ea6e0d57fb88d77ac5aeb8c9` |
| 3.11 | aarch64 | 14379387 | `853fc608f96da87bd5f3458465dfb9abbf18d5ae6b94eceacead407146f175ae` |
| 3.12 | aarch64 | 14380093 | `9891611b23a87cc2ace60e4b5ee9a2ee6f0fb7f395f009470961ae6e6310e1b8` |
| 3.13 | aarch64 | 14380166 | `2ab71e4f5dfa65b3882634b42755f455f3e7415630d720200bd792733e15e257` |
| 3.14 | aarch64 | 14380641 | `899b41423b30f3cef1ce8a361443092fdeefc4d536132511634fa21548347b0a` |

The download addresses are in `SOURCE/head_turn_face_unlock/model_files.py`.
