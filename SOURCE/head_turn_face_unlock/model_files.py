"""Everything downloaded at installation, pinned by size and SHA-256.

A download is accepted only if it is byte for byte the published file. None of these
files is copied into this repository.

  - SFace face recognizer (OpenCV Zoo, Apache 2.0): the checksum is the Git LFS object id
    published in the OpenCV Zoo repository.
  - MediaPipe Face Landmarker bundle, version 1 (Google, Apache 2.0 per its model cards):
    BlazeFace short range and Face Mesh V2 are taken out of the bundle unchanged, and each
    is checked again.
  - LiteRT, Google's runtime for those models (Apache 2.0): the published wheel for the
    running Python version and processor, unpacked without its optional dependencies
    (only NumPy is needed, which the distribution provides).
"""

RECOGNIZER = "face_recognition_sface_2021dec.onnx"
BUNDLE = "face_landmarker.task"
DETECTOR = "face_detector.tflite"
MESH = "face_landmarks_detector.tflite"

DOWNLOADS = {
    RECOGNIZER: ("https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models/face_recognition_sface/"
                 "face_recognition_sface_2021dec.onnx", 38696353,
                 "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
    BUNDLE: ("https://storage.googleapis.com/mediapipe-models/face_landmarker/face_landmarker/float16/1/"
             "face_landmarker.task", 3758596, "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff"),
}
BUNDLE_MEMBERS = {
    DETECTOR: (229746, "b4578f35940bf5a1a655214a1cce5cab13eba73c1297cd78e1a04c2380b0152f"),
    MESH: (2553590, "c7d54204ce0448474c7f3fa9af494787c0965cbdd6f20fc72867e43046bd43d5"),
}
# The files the service loads, with their checksums.
INSTALLED = {RECOGNIZER: DOWNLOADS[RECOGNIZER][2], DETECTOR: BUNDLE_MEMBERS[DETECTOR][1],
             MESH: BUNDLE_MEMBERS[MESH][1]}

RUNTIME_VERSION = "2.3.0"
_PYPI = "https://files.pythonhosted.org/packages"
RUNTIME_WHEELS = {
    ("cp310", "x86_64"): (f"{_PYPI}/50/8f/185ceea9c2634f5ba64244b33bbb210df6813ab2ed07558f63b4ad22f445/"
                          "ai_edge_litert-2.3.0-cp310-cp310-manylinux_2_27_x86_64.whl", 21776509,
                          "e104683ee0dead1243002a218637cc435c36fdb01103799335027252314e727b"),
    ("cp311", "x86_64"): (f"{_PYPI}/65/da/254ec1df4f28ae0d03c410921053b570b116bd917dd64c296bdcc4b5192b/"
                          "ai_edge_litert-2.3.0-cp311-cp311-manylinux_2_27_x86_64.whl", 21778233,
                          "1f15078a6725d2357d9de9221664c29afa0545d807559c5f7199ce5e40b96b23"),
    ("cp312", "x86_64"): (f"{_PYPI}/94/db/defacfb96eb7ddf6145dc6bc0b0c64bc221364e413662ef8216bae8258e8/"
                          "ai_edge_litert-2.3.0-cp312-cp312-manylinux_2_27_x86_64.whl", 21781762,
                          "dc9f56c23e0dcf182b6c35f23fbb102ed048296588882b806d5eff7654c04c88"),
    ("cp313", "x86_64"): (f"{_PYPI}/81/5f/24ee5e5f52c2fbf02801b557c4a29043b0070eb75cb32569ffe1d4bbb48a/"
                          "ai_edge_litert-2.3.0-cp313-cp313-manylinux_2_27_x86_64.whl", 21781769,
                          "985ac3823fe1d5d6a04cf5f613cc2adf98a9c8a456af0b5efa8d9ac6b118ea14"),
    ("cp314", "x86_64"): (f"{_PYPI}/38/58/087131ec133c1b69eaf393fdcedc11a128296c94d7d31f7638edfe9ef76d/"
                          "ai_edge_litert-2.3.0-cp314-cp314-manylinux_2_27_x86_64.whl", 21781596,
                          "695f5164b66ebdb0a3bbb4edc4d7024e87c65a0e417d48d0a2db2503bc01b78d"),
    ("cp310", "aarch64"): (f"{_PYPI}/62/20/ed5f97923f35d96a186f3ce1a2f0817427bbab111b74b4b7af045fac9c10/"
                           "ai_edge_litert-2.3.0-cp310-cp310-manylinux_2_27_aarch64.whl", 14377617,
                           "a72fc35d66c1d8b17f52b195bfb39b89aa839d39ea6e0d57fb88d77ac5aeb8c9"),
    ("cp311", "aarch64"): (f"{_PYPI}/33/68/446c2685f5752eef7891dc8a43ca27bd393461ea0caac7c413587c2f4535/"
                           "ai_edge_litert-2.3.0-cp311-cp311-manylinux_2_27_aarch64.whl", 14379387,
                           "853fc608f96da87bd5f3458465dfb9abbf18d5ae6b94eceacead407146f175ae"),
    ("cp312", "aarch64"): (f"{_PYPI}/77/89/2bdb0260c4182358c0948316109a3850c37aa15b87665f4b12fb51ea706c/"
                           "ai_edge_litert-2.3.0-cp312-cp312-manylinux_2_27_aarch64.whl", 14380093,
                           "9891611b23a87cc2ace60e4b5ee9a2ee6f0fb7f395f009470961ae6e6310e1b8"),
    ("cp313", "aarch64"): (f"{_PYPI}/b2/ad/234674ab4781ff1822c26794488b5640512bc61011a6b3490bfecdc46aa1/"
                           "ai_edge_litert-2.3.0-cp313-cp313-manylinux_2_27_aarch64.whl", 14380166,
                           "2ab71e4f5dfa65b3882634b42755f455f3e7415630d720200bd792733e15e257"),
    ("cp314", "aarch64"): (f"{_PYPI}/3b/16/49b1997efd47ac188c8eb23473c328ad9e2ddb4dd1f14bb44628074aa169/"
                           "ai_edge_litert-2.3.0-cp314-cp314-manylinux_2_27_aarch64.whl", 14380641,
                           "899b41423b30f3cef1ce8a361443092fdeefc4d536132511634fa21548347b0a"),
}
