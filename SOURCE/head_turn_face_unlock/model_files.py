"""The two networks from the OpenCV Zoo, pinned by SHA-256.

The checksums equal the Git LFS object ids published in the OpenCV Zoo
repository, so a download is accepted only if it is byte for byte the
published file. YuNet is MIT licensed, SFace Apache 2.0; neither is copied
into this repository.
"""

DETECTOR = "face_detection_yunet_2023mar.onnx"
RECOGNIZER = "face_recognition_sface_2021dec.onnx"
DOWNLOAD_BASE = "https://media.githubusercontent.com/media/opencv/opencv_zoo/main/models"
FILES = {
    DETECTOR: ("face_detection_yunet", 232589,
               "8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"),
    RECOGNIZER: ("face_recognition_sface", 38696353,
                 "0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"),
}
