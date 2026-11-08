"""Finds faces and their 478 landmarks with the MediaPipe Face Landmarker models, unchanged.

Both models run in Google's LiteRT runtime exactly as published (no conversion):
  - BlazeFace short range: 128x128 input, 896 anchors, 0.55 ms
  - Face Mesh V2: 256x256 crop around the face, 478 points with depth, 1.9 ms
Pre- and post-processing follow MediaPipe's own graphs: the frame is letterboxed for
the detector, the crop is rotated so the eyes are level and is 1.5 times the face box.

Measured on the author's machine: BlazeFace found the face in 96% of color frames
(YuNet: 89%) and 84% of lit infrared frames (YuNet: 86%); the mesh held on 94% of the
infrared frames where the face was found.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass

import cv2
import numpy as np

from .geometry import POINT_COUNT

DETECTOR_SIZE = 128
MESH_SIZE = 256
DETECTION_SCORE = 0.5
DETECTION_OVERLAP = 0.3
ROI_SCALE = 1.5
PRESENCE = 0.5
# SSD anchors of face_detection_short_range: stride 8 with two anchors per cell, stride 16 with six.
ANCHOR_LAYERS = ((8, 2), (16, 6))


def _anchors() -> np.ndarray:
    centres = []
    for stride, per_cell in ANCHOR_LAYERS:
        cells = DETECTOR_SIZE // stride
        for row in range(cells):
            for column in range(cells):
                centres += [((column + 0.5) / cells, (row + 0.5) / cells)] * per_cell
    return np.array(centres, np.float32)


ANCHORS = _anchors()


@dataclass(frozen=True)
class Detection:
    score: float
    box: np.ndarray        # x0, y0, x1, y1 in image pixels
    keypoints: np.ndarray  # six (x, y): right eye, left eye, nose tip, mouth, right ear, left ear


@dataclass(frozen=True)
class Face:
    points: np.ndarray  # 478 x 3 in image pixels; z has the same scale as x
    presence: float


def letterbox(rgb: np.ndarray) -> tuple[np.ndarray, int, int, int]:
    """The detector's input in [-1, 1], the square side, and the left and top padding."""
    height, width = rgb.shape[:2]
    side = max(height, width)
    left, top = (side - width) // 2, (side - height) // 2
    square = cv2.copyMakeBorder(rgb, top, side - height - top, left, side - width - left, cv2.BORDER_CONSTANT, value=0)
    tensor = cv2.resize(square, (DETECTOR_SIZE, DETECTOR_SIZE), interpolation=cv2.INTER_AREA)
    return tensor.astype(np.float32) / 127.5 - 1.0, side, left, top


def decode(boxes: np.ndarray, logits: np.ndarray, side: int, left: int, top: int) -> list[Detection]:
    """Detections above the score, best first, without overlapping duplicates."""
    scores = 1.0 / (1.0 + np.exp(-np.clip(logits.astype(np.float64), -100.0, 100.0)))
    found: list[Detection] = []
    for index in np.argsort(-scores):
        if scores[index] < DETECTION_SCORE:
            break
        raw = boxes[index].astype(np.float64)
        anchor = ANCHORS[index]
        centre = raw[:2] / DETECTOR_SIZE + anchor
        size = raw[2:4] / DETECTOR_SIZE
        box = np.concatenate([centre - size / 2, centre + size / 2]) * side - (left, top, left, top)
        if any(_overlap(box, other.box) > DETECTION_OVERLAP for other in found):
            continue
        keypoints = (raw[4:16].reshape(6, 2) / DETECTOR_SIZE + anchor) * side - (left, top)
        found.append(Detection(float(scores[index]), box, keypoints))
    return found


def _overlap(first: np.ndarray, second: np.ndarray) -> float:
    width = min(first[2], second[2]) - max(first[0], second[0])
    height = min(first[3], second[3]) - max(first[1], second[1])
    if width <= 0 or height <= 0:
        return 0.0
    intersection = width * height
    union = (first[2] - first[0]) * (first[3] - first[1]) + (second[2] - second[0]) * (second[3] - second[1]) - intersection
    return float(intersection / union) if union > 0 else 0.0


def crop_transform(detection: Detection) -> tuple[np.ndarray, float]:
    """Affine map from the image to the mesh input: centred on the face box, 1.5 times its
    longer side, rotated so the eyes are level. Returns the matrix and the crop side in pixels."""
    right_eye, left_eye = detection.keypoints[0], detection.keypoints[1]
    angle = math.degrees(math.atan2(left_eye[1] - right_eye[1], left_eye[0] - right_eye[0]))
    centre = ((detection.box[0] + detection.box[2]) / 2, (detection.box[1] + detection.box[3]) / 2)
    side = float(max(detection.box[2] - detection.box[0], detection.box[3] - detection.box[1])) * ROI_SCALE
    matrix = cv2.getRotationMatrix2D(centre, angle, MESH_SIZE / side)
    matrix[:, 2] += (MESH_SIZE / 2 - centre[0], MESH_SIZE / 2 - centre[1])
    return matrix, side


def to_image(local: np.ndarray, matrix: np.ndarray, side: float) -> np.ndarray:
    """Mesh output (crop pixels) back to image pixels; depth scaled like x and y."""
    inverse = cv2.invertAffineTransform(matrix)
    points = np.empty((len(local), 3), np.float32)
    points[:, :2] = local[:, :2] @ inverse[:, :2].T + inverse[:, 2]
    points[:, 2] = local[:, 2] * side / MESH_SIZE
    return points


class FaceFinder:
    """Detector and mesh for one thread (LiteRT interpreters are not shared between threads)."""

    def __init__(self, detector_path: str, mesh_path: str, threads: int) -> None:
        from ai_edge_litert.interpreter import Interpreter  # installed beside the package by INSTALL.sh

        self._detector = Interpreter(model_path=detector_path, num_threads=threads)
        self._detector.allocate_tensors()
        self._detector_input = self._detector.get_input_details()[0]["index"]
        outputs = {item["name"]: item["index"] for item in self._detector.get_output_details()}
        self._boxes, self._scores = outputs["regressors"], outputs["classificators"]
        self._mesh = Interpreter(model_path=mesh_path, num_threads=threads)
        self._mesh.allocate_tensors()
        self._mesh_input = self._mesh.get_input_details()[0]["index"]
        outputs = {item["name"]: item["index"] for item in self._mesh.get_output_details()}
        self._points, self._presence = outputs["Identity"], outputs["Identity_1"]

    def detect(self, rgb: np.ndarray) -> list[Detection]:
        tensor, side, left, top = letterbox(rgb)
        self._detector.set_tensor(self._detector_input, tensor[None])
        self._detector.invoke()
        return decode(self._detector.get_tensor(self._boxes)[0], self._detector.get_tensor(self._scores)[0, :, 0],
                      side, left, top)

    def mesh(self, rgb: np.ndarray, detection: Detection) -> Face:
        matrix, side = crop_transform(detection)
        crop = cv2.warpAffine(rgb, matrix, (MESH_SIZE, MESH_SIZE), flags=cv2.INTER_LINEAR)
        self._mesh.set_tensor(self._mesh_input, (crop.astype(np.float32) / 255.0)[None])
        self._mesh.invoke()
        local = self._mesh.get_tensor(self._points).reshape(POINT_COUNT, 3)
        logit = float(np.clip(self._mesh.get_tensor(self._presence).ravel()[0], -100.0, 100.0))
        return Face(to_image(local, matrix, side), 1.0 / (1.0 + math.exp(-logit)))

    def find(self, rgb: np.ndarray) -> tuple[int, Face | None]:
        """How many faces the detector sees, and the mesh of the face when there is exactly one."""
        detections = self.detect(rgb)
        if len(detections) != 1:
            return len(detections), None
        face = self.mesh(rgb, detections[0])
        return 1, face if face.presence >= PRESENCE else None


def model_paths(directory: str) -> tuple[str, str]:
    from .model_files import DETECTOR, MESH
    return os.path.join(directory, DETECTOR), os.path.join(directory, MESH)
