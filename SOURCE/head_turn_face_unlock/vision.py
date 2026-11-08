"""Face detection (YuNet), face embeddings (SFace) and the infrared measurements.

Measured on the author's machine (Core Ultra 7 255H, OpenCV 4.10):
  - Decoding the 1280x720 JPEG at half size inside libjpeg (DCT scaling) takes
    0.9 ms instead of 3.3 ms for a full decode plus resize, and moves the
    landmarks by 0.5 px; embeddings stay at cosine 0.996 against the old path.
  - Detecting in a crop around the last face was 5 times cheaper but changed
    the landmarks enough to drop embedding agreement to 0.88-0.95, so every
    frame is searched whole.
  - OpenCL on the integrated GPU was slower for detection (12 ms against
    6.6 ms) and needed 200 ms to set up, so the CPU is used.
"""

from __future__ import annotations

import hashlib
import os
import threading

import cv2
import numpy as np

from .model_files import DETECTOR as DETECTOR_FILE
from .model_files import FILES
from .model_files import RECOGNIZER as RECOGNIZER_FILE

DETECTION_SCORE = 0.8
DETECTION_OVERLAP = 0.3
DETECTION_MAXIMUM_FACES = 5
# Color frames are decoded at half size for detection; landmarks are scaled back.
DETECTION_REDUCTION = 2
# An infrared frame lit by the emitter is at least this much brighter than an unlit one.
LIT_MARGIN = 4.0
MINIMUM_INFRARED_FACE_PIXELS = 400


class ModelError(RuntimeError):
    pass


def verify_models(directory: str) -> None:
    for name, (_, _, expected) in FILES.items():
        path = os.path.join(directory, name)
        digest = hashlib.sha256()
        try:
            with open(path, "rb") as handle:
                for block in iter(lambda: handle.read(1 << 20), b""):
                    digest.update(block)
        except OSError as error:
            raise ModelError(f"cannot read {path}: {error.strerror}") from None
        if digest.hexdigest() != expected:
            raise ModelError(f"{path} does not match its published checksum")


class FaceModels:
    """Loads both networks once. Detectors keep per-input state, so every thread asks
    for its own; the recognizer is shared behind a lock."""

    def __init__(self, directory: str, threads: int) -> None:
        verify_models(directory)
        cv2.setNumThreads(threads)
        cv2.ocl.setUseOpenCL(False)
        self._detector_path = os.path.join(directory, DETECTOR_FILE)
        self._recognizer = cv2.FaceRecognizerSF.create(os.path.join(directory, RECOGNIZER_FILE), "")
        self._lock = threading.Lock()
        warm = np.zeros((112, 112, 3), np.uint8)
        self._recognizer.feature(warm)
        self.new_detector().detect(np.zeros((64, 64, 3), np.uint8))

    def new_detector(self) -> "Detector":
        return Detector(cv2.FaceDetectorYN.create(self._detector_path, "", (320, 320), DETECTION_SCORE,
                                                  DETECTION_OVERLAP, DETECTION_MAXIMUM_FACES))

    def embed(self, image: np.ndarray, face: np.ndarray) -> np.ndarray:
        """Unit-length 128-number embedding of the face aligned by its five landmarks."""
        with self._lock:
            aligned = self._recognizer.alignCrop(image, face)
            vector = self._recognizer.feature(aligned).flatten().astype(np.float32)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else vector


class Detector:
    def __init__(self, detector: "cv2.FaceDetectorYN") -> None:
        self._detector = detector
        self._size: tuple[int, int] = (0, 0)

    def detect(self, image: np.ndarray) -> np.ndarray:
        """Rows of 15 numbers: box x, y, w, h, five landmarks (x, y), score."""
        size = (image.shape[1], image.shape[0])
        if size != self._size:
            self._detector.setInputSize(size)
            self._size = size
        _, faces = self._detector.detect(image)
        return np.zeros((0, 15), np.float32) if faces is None else faces


def decode_for_detection(jpeg: bytes) -> np.ndarray | None:
    flag = {1: cv2.IMREAD_COLOR, 2: cv2.IMREAD_REDUCED_COLOR_2, 4: cv2.IMREAD_REDUCED_COLOR_4}[DETECTION_REDUCTION]
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), flag)


def decode_full(jpeg: bytes) -> np.ndarray | None:
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def to_full_scale(face: np.ndarray) -> np.ndarray:
    scaled = face.copy()
    scaled[:14] *= DETECTION_REDUCTION
    return scaled


def yaw_index(face: np.ndarray) -> float:
    """Nose offset from the face axis divided by face size: roughly sin(yaw).
    Landmarks: right eye, left eye, nose, right mouth corner, left mouth corner."""
    points = face[4:14].reshape(5, 2).astype(np.float64)
    eyes = (points[0] + points[1]) / 2
    mouth = (points[3] + points[4]) / 2
    axis_x = (eyes[0] + mouth[0]) / 2
    eye_distance = np.linalg.norm(points[0] - points[1]) + 1e-6
    face_height = np.linalg.norm(eyes - mouth) + 1e-6
    return float((points[2][0] - axis_x) / (0.5 * eye_distance + 0.5 * face_height))


def sharpness(image: np.ndarray, face: np.ndarray) -> float:
    """Variance of the Laplacian inside the face box: higher is sharper."""
    x, y, w, h = (int(v) for v in face[:4])
    crop = image[max(y, 0):max(y + h, 0), max(x, 0):max(x + w, 0)]
    if crop.shape[0] * crop.shape[1] <= 64:
        return 0.0
    if crop.ndim == 3:
        crop = cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(np.ascontiguousarray(crop), cv2.CV_32F).var())


class InfraredPairer:
    """The emitter lights every other frame. Each lit frame is paired once, with the unlit
    frame just before it; their difference is the light the emitter added, which a flat
    screen does not reflect the way skin does."""

    def __init__(self) -> None:
        self._previous: tuple[float, np.ndarray] | None = None

    def push(self, frame: np.ndarray) -> tuple[np.ndarray, np.ndarray] | None:
        level = float(frame.mean())
        pair = None
        if self._previous is not None:
            previous_level, previous = self._previous
            if level - previous_level > LIT_MARGIN:
                pair = (frame, cv2.subtract(frame, previous))
        self._previous = (level, frame)
        return pair


def infrared_profile(difference: np.ndarray, face: np.ndarray) -> tuple[float, float, float] | None:
    """Reflection measurements inside the face box: mean level, nose region over the face
    mean, and the horizontal brightness centroid (it shifts as a real head turns).
    None when the face or the nose region falls outside the picture."""
    height, width = difference.shape[:2]
    x, y, w, h = (int(v) for v in face[:4])
    left, top, right, bottom = max(x, 0), max(y, 0), min(x + w, width), min(y + h, height)
    if right <= left or bottom <= top:
        return None
    region = difference[top:bottom, left:right].astype(np.float32)
    if region.size < MINIMUM_INFRARED_FACE_PIXELS:
        return None
    nose_x, nose_y = float(face[8]) - left, float(face[9]) - top
    radius = max(3, int(0.12 * w))
    nose_left, nose_right = int(max(nose_x - radius, 0)), int(min(nose_x + radius, region.shape[1]))
    nose_top, nose_bottom = int(max(nose_y - radius, 0)), int(min(nose_y + radius, region.shape[0]))
    if nose_right <= nose_left or nose_bottom <= nose_top:
        return None
    face_level = float(region.mean()) + 1e-3
    nose_ratio = float(region[nose_top:nose_bottom, nose_left:nose_right].mean()) / face_level
    columns = region.mean(axis=0)
    centroid = float((columns * np.arange(len(columns))).sum() / (columns.sum() + 1e-3) / len(columns))
    return face_level, nose_ratio, centroid
