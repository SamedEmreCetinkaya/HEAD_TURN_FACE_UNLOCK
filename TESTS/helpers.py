"""Shared builders for the tests: a synthetic 3D head, synthetic turns, templates and settings."""

import math
import os
import sys

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.realpath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "SOURCE"))
RUNTIME = os.environ.get("HEAD_TURN_FACE_UNLOCK_RUNTIME", "/usr/local/lib/head-turn-face-unlock/runtime")
if os.path.isdir(RUNTIME):
    sys.path.insert(0, RUNTIME)
MODELS = os.environ.get("HEAD_TURN_FACE_UNLOCK_MODELS", "/usr/local/lib/head-turn-face-unlock/models")

from head_turn_face_unlock import decision, geometry  # noqa: E402
from head_turn_face_unlock.decision import ColorSample, InfraredSample, Reference, Thresholds, Timing  # noqa: E402
from head_turn_face_unlock.template import Template, empty_bank  # noqa: E402
from head_turn_face_unlock.vision import EMBEDDING_SIZE  # noqa: E402

TIMING = Timing(1 / 30, 1 / 15)
THRESHOLDS = Thresholds(0.60, 0.65, 0.45, 0.80)


def runtime_available() -> bool:
    try:
        import ai_edge_litert.interpreter  # noqa: F401
    except ImportError:
        return False
    return True


def models_available() -> bool:
    from head_turn_face_unlock.model_files import INSTALLED
    return all(os.path.isfile(os.path.join(MODELS, name)) for name in INSTALLED)


def _head() -> np.ndarray:
    """A head in its own frame (x to the image right when frontal, y down, z away from the
    camera; units about a millimetre): the outline is set back, the nose stands out."""
    points = np.zeros((geometry.POINT_COUNT, 3))
    for k, index in enumerate(geometry.FACE_OVAL):
        angle = 2 * math.pi * k / len(geometry.FACE_OVAL)
        points[index] = (75 * math.sin(angle), -95 * math.cos(angle), 45)
    for k, index in enumerate(geometry.NOSE_RIDGE):
        points[index] = (0, -35 + 9 * k, -10 - 5 * k)
    named = {33: (-45, -30, -2), 133: (-15, -30, -8), 362: (15, -30, -8), 263: (45, -30, -2),
             468: (-30, -30, -8), 473: (30, -30, -8), 61: (-25, 45, -8), 291: (25, 45, -8),
             2: (0, 28, -22), 98: (-15, 25, -15), 327: (15, 25, -15), 105: (-30, -52, -8), 159: (-30, -36, -10),
             334: (30, -52, -8), 386: (30, -36, -10), 17: (0, 55, -10)}
    for index, value in named.items():
        points[index] = value
    return points


HEAD = _head()


def view(yaw: float = 0.0, pitch: float = 0.0, flat: bool = False, scale: float = 1.0, shift: float = 0.0) -> np.ndarray:
    """478 x 3 landmarks of the head (or of a flat picture of it) turned by yaw and pitch,
    seen by a camera 60 cm away, in pixels of a 640x360 frame."""
    head = HEAD.copy()
    if flat:
        head[:, 2] = 0.0
    turn, tilt = -math.radians(yaw), math.radians(pitch)
    around_y = np.array([[math.cos(turn), 0, -math.sin(turn)], [0, 1, 0], [math.sin(turn), 0, math.cos(turn)]])
    around_x = np.array([[1, 0, 0], [0, math.cos(tilt), math.sin(tilt)], [0, -math.sin(tilt), math.cos(tilt)]])
    rotated = head @ (around_x @ around_y).T * scale
    distance, focal = 600.0, 400.0
    points = np.empty_like(rotated)
    points[:, 0] = focal * rotated[:, 0] / (distance + rotated[:, 2]) + 320 + shift
    points[:, 1] = focal * rotated[:, 1] / (distance + rotated[:, 2]) + 180
    points[:, 2] = focal * rotated[:, 2] / distance
    return points.astype(np.float32)


def turn_yaws() -> list[float]:
    """Look ahead, turn to the right, sweep to the left, come back a little (degrees)."""
    return (list(np.linspace(0.0, 30.0, 10)) + list(np.linspace(30.0, -30.0, 24))[1:]
            + list(np.linspace(-30.0, 0.0, 6))[1:])


def sample_of(points: np.ndarray, moment: float, index: int = 0) -> ColorSample:
    yaw, pitch, _ = geometry.pose(points)
    centre_x, centre_y, width = geometry.face_box(points)
    return ColorSample(moment, yaw, pitch, centre_x, centre_y, width, 100.0 + index % 7, geometry.eye_distance(points),
                       geometry.rigid_points(points), geometry.shape(points))


def color_track(yaws: list[float] | None = None, start: float = 100.0, interval: float = 1 / 30,
                flat: bool = False) -> list[ColorSample]:
    yaws = turn_yaws() if yaws is None else yaws
    return [sample_of(view(yaw, flat=flat), start + index * interval, index) for index, yaw in enumerate(yaws)]


def infrared_track(samples: list[ColorSample], similarity: float = 0.9, level: float = 90.0) -> list[InfraredSample]:
    """Lit infrared frames every other frame of a 15 fps stream, angles from the color track."""
    times = np.arange(samples[0].timestamp, samples[-1].timestamp, 2 / 15)
    stamps = np.array([sample.timestamp for sample in samples])
    yaws = np.array([sample.yaw for sample in samples])
    return [InfraredSample(float(moment), float(np.interp(moment, stamps, yaws)), similarity, 50.0 + index, level)
            for index, moment in enumerate(times)]


def reference_for(samples: list[ColorSample] | None = None, level: float = 95.0) -> Reference:
    frontal = [sample.shape for sample in (samples or color_track()) if abs(sample.yaw) <= decision.FRONTAL_YAW]
    median = np.median(np.array(frontal), axis=0)
    return Reference(level, median, np.maximum(np.std(np.array(frontal), axis=0), 0.01 * median))


REFERENCE = reference_for()


def unit(vector: np.ndarray) -> np.ndarray:
    return (vector / np.linalg.norm(vector)).astype(np.float32)


def random_units(count: int, seed: int) -> np.ndarray:
    generator = np.random.default_rng(seed)
    return np.array([unit(row) for row in generator.normal(size=(count, EMBEDDING_SIZE))], np.float32)


def sample_template(per_bin: int = 3, seed: int = 1) -> Template:
    color, infrared = empty_bank(), empty_bank()
    for angle in range(decision.BIN_COUNT):
        color[angle] = random_units(per_bin, seed + angle)
        infrared[angle] = random_units(per_bin, seed + 100 + angle)
    return Template(1, color, infrared, REFERENCE, "2026-01-31 12:00", [])
