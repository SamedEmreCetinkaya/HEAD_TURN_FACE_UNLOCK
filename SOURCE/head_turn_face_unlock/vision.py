"""Pixels: JPEG decoding, the SFace face recognizer, sharpness and the infrared lit/unlit pairing.

Measured on the author's machine (Core Ultra 7 255H, OpenCV 4.10):
  - Decoding a 1280x720 JPEG at half size inside libjpeg (DCT scaling) takes 0.9 ms
    instead of 3.3 ms for a full decode plus resize. Tracking uses the half-size frame;
    only the frames chosen for recognition are decoded at full size.
  - SFace takes 14.7 ms per face with two threads, so it runs only on the frames the
    decision needs. The integrated GPU through OpenCL was not faster once its 200 ms set-up
    is counted, so it is switched off.
"""

from __future__ import annotations

import hashlib
import os
import threading

import cv2
import numpy as np

from . import geometry
from .model_files import INSTALLED, RECOGNIZER

DETECTION_REDUCTION = 2
# An infrared frame lit by the emitter is at least this much brighter than an unlit one.
LIT_MARGIN = 4.0
EMBEDDING_SIZE = 128


class ModelError(RuntimeError):
    pass


def verify_models(directory: str) -> None:
    for name, expected in INSTALLED.items():
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


class Recognizer:
    """SFace, shared by every thread behind a lock."""

    def __init__(self, directory: str, threads: int) -> None:
        cv2.setNumThreads(threads)
        cv2.ocl.setUseOpenCL(False)
        self._model = cv2.FaceRecognizerSF.create(os.path.join(directory, RECOGNIZER), "")
        self._lock = threading.Lock()
        self._model.feature(np.zeros((112, 112, 3), np.uint8))

    def embed(self, image: np.ndarray, five_points: np.ndarray) -> np.ndarray:
        """Unit-length embedding of the face aligned by its five points (geometry.alignment_points)."""
        row = np.zeros(15, np.float32)
        row[4:14] = np.asarray(five_points, np.float32).ravel()
        with self._lock:
            aligned = self._model.alignCrop(image, row)
            vector = self._model.feature(aligned).flatten().astype(np.float32)
        norm = float(np.linalg.norm(vector))
        return vector / norm if norm > 0 else vector


def decode_half(jpeg: bytes) -> np.ndarray | None:
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_REDUCED_COLOR_2)


def decode_full(jpeg: bytes) -> np.ndarray | None:
    return cv2.imdecode(np.frombuffer(jpeg, np.uint8), cv2.IMREAD_COLOR)


def sharpness(image: np.ndarray, centre_x: float, centre_y: float, width: float) -> float:
    """Variance of the Laplacian in a square around the face: higher is sharper."""
    half = width / 2
    top, bottom = int(max(centre_y - half, 0)), int(max(centre_y + half, 0))
    left, right = int(max(centre_x - half, 0)), int(max(centre_x + half, 0))
    crop = image[top:bottom, left:right]
    if crop.shape[0] * crop.shape[1] <= 64:
        return 0.0
    if crop.ndim == 3:
        crop = cv2.cvtColor(np.ascontiguousarray(crop), cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(np.ascontiguousarray(crop), cv2.CV_32F).var())


class InfraredPairer:
    """The emitter lights every other frame. Each lit frame is paired once, with the unlit
    frame just before it; their difference is the light the emitter added."""

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


def infrared_level(difference: np.ndarray, points: np.ndarray) -> float | None:
    """Mean light the emitter added inside the face outline; None if the outline is off the picture."""
    mask = geometry.face_mask(points, difference.shape[:2])
    if not mask.any():
        return None
    return float(cv2.mean(difference, mask=mask)[0])
