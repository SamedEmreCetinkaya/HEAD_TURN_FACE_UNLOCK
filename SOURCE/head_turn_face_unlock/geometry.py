"""Face geometry from the 478-point MediaPipe face mesh. Pure numpy; no camera, no model.

Point numbers follow the MediaPipe face mesh. "Right" and "left" are the person's
own sides; on an unmirrored camera image the person's right eye is on the left.

Measured on the author's machine from a 23-second recording of head turns:
  - landmark noise between consecutive frames: 1.1% of the eye distance
  - parallax (below) of a flat picture turned 10-30 degrees, simulated with that
    noise: at most 1.2%; of the real head over a 20-30 degree difference: median
    19%, 5th percentile 13.8%; over a full right-to-left turn: 44-176%
  - yaw from the mesh against the five-point yaw index: correlation 0.996
"""

from __future__ import annotations

import math

import cv2
import numpy as np

POINT_COUNT = 478
FACE_OVAL = (10, 338, 297, 332, 284, 251, 389, 356, 454, 323, 361, 288, 397, 365, 379, 378, 400, 377, 152, 148,
             176, 149, 150, 136, 172, 58, 132, 93, 234, 127, 162, 21, 54, 103, 67, 109)
NOSE_RIDGE = (168, 6, 197, 195, 5, 4, 1)
EYE_CORNERS = (33, 133, 362, 263)
# Points that move with the skull, not with expressions: lips, eyelids and brows are left out.
RIGID = FACE_OVAL + NOSE_RIDGE + EYE_CORNERS
RIGHT_IRIS, LEFT_IRIS = 468, 473
NOSE_TIP = 1
RIGHT_MOUTH, LEFT_MOUTH = 61, 291
RIGHT_CHEEK, LEFT_CHEEK = 234, 454
FOREHEAD, CHIN = 10, 152
RIGHT_EYE_OUTER, LEFT_EYE_OUTER = 33, 263
SHAPE_NAMES = ("face height / face width", "nose length / face height", "nose width / eye distance",
               "mouth width / eye distance", "brow to eye / eye distance", "jaw width / face width",
               "chin height / face height")


def _unit(vector: np.ndarray) -> np.ndarray:
    norm = float(np.linalg.norm(vector))
    return vector / norm if norm > 0 else vector


def pose(points: np.ndarray) -> tuple[float, float, float]:
    """Yaw, pitch and roll in degrees from the mesh's own 3D shape.

    The face's x axis runs from its right to its left side (cheeks, outer eye corners and
    mouth corners averaged), the y axis from chin to forehead; their cross product is the
    direction the face looks. On an unmirrored camera image, yaw is positive when the
    person turns to their right, pitch when they look up, roll when the head tilts clockwise.
    """
    p = np.asarray(points, np.float64)
    x_axis = _unit(np.mean([p[LEFT_CHEEK] - p[RIGHT_CHEEK], p[LEFT_EYE_OUTER] - p[RIGHT_EYE_OUTER],
                            p[LEFT_MOUTH] - p[RIGHT_MOUTH]], axis=0))
    y_axis = p[FOREHEAD] - p[CHIN]
    y_axis = _unit(y_axis - x_axis * float(y_axis @ x_axis))
    facing = np.cross(x_axis, y_axis)
    yaw = math.degrees(math.atan2(-facing[0], -facing[2]))
    pitch = math.degrees(math.atan2(-facing[1], -facing[2]))
    roll = math.degrees(math.atan2(x_axis[1], x_axis[0]))
    return yaw, pitch, roll


def eye_distance(points: np.ndarray) -> float:
    return float(np.linalg.norm(points[RIGHT_IRIS, :2] - points[LEFT_IRIS, :2]))


def alignment_points(points: np.ndarray) -> np.ndarray:
    """The five points the face recognizer aligns by, in the order its template expects:
    the eye and the mouth corner on the left of the image first, then nose tip between."""
    eyes = sorted((points[RIGHT_IRIS, :2], points[LEFT_IRIS, :2]), key=lambda point: point[0])
    mouth = sorted((points[RIGHT_MOUTH, :2], points[LEFT_MOUTH, :2]), key=lambda point: point[0])
    return np.array([eyes[0], eyes[1], points[NOSE_TIP, :2], mouth[0], mouth[1]], np.float32)


def face_box(points: np.ndarray) -> tuple[float, float, float]:
    """Centre x, centre y and width of the face outline."""
    oval = points[list(FACE_OVAL), :2]
    low, high = oval.min(axis=0), oval.max(axis=0)
    return float((low[0] + high[0]) / 2), float((low[1] + high[1]) / 2), float(high[0] - low[0])


def rigid_points(points: np.ndarray) -> np.ndarray:
    return np.ascontiguousarray(points[list(RIGID), :2], np.float32)


def parallax(first: np.ndarray, second: np.ndarray, first_eyes: float, second_eyes: float) -> float:
    """How far two views of the rigid points are from being one flat surface seen twice.

    A flat picture, however it is turned, maps onto itself by a homography; a real head does
    not, because the nose and the visible outline shift against each other. The result is
    the root-mean-square distance left after the best homography, over the mean eye distance.
    """
    if not (np.isfinite(first).all() and np.isfinite(second).all()):
        return 0.0
    homography, _ = cv2.findHomography(first, second, 0)
    if homography is None:
        return 0.0
    mapped = cv2.perspectiveTransform(first.reshape(-1, 1, 2), homography).reshape(-1, 2)
    scale = (first_eyes + second_eyes) / 2
    if not scale > 0:
        return 0.0
    return float(np.sqrt(np.mean(np.sum((mapped - second) ** 2, axis=1)))) / scale


def shape(points: np.ndarray) -> np.ndarray:
    """Proportions of the face, meaningful only on frames that look at the camera."""
    p = points[:, :2].astype(np.float64)

    def distance(a: int, b: int) -> float:
        return float(np.linalg.norm(p[a] - p[b]))
    eyes = distance(RIGHT_IRIS, LEFT_IRIS)
    height = distance(FOREHEAD, CHIN)
    width = distance(RIGHT_CHEEK, LEFT_CHEEK)
    if not (eyes > 0 and height > 0 and width > 0):
        return np.full(len(SHAPE_NAMES), np.nan)
    return np.array([height / width, distance(168, 2) / height, distance(98, 327) / eyes,
                     distance(RIGHT_MOUTH, LEFT_MOUTH) / eyes, (distance(105, 159) + distance(334, 386)) / 2 / eyes,
                     distance(172, 397) / width, distance(17, CHIN) / height])


def face_mask(points: np.ndarray, size: tuple[int, int]) -> np.ndarray:
    """1 inside the face outline, 0 elsewhere; size is (height, width)."""
    mask = np.zeros(size, np.uint8)
    cv2.fillPoly(mask, [np.round(points[list(FACE_OVAL), :2]).astype(np.int32)], 1)
    return mask
