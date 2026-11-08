"""Shared builders for the tests: synthetic head turns, templates and settings."""

import os
import sys

import numpy as np

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))), "SOURCE"))

from head_turn_face_unlock import decision  # noqa: E402
from head_turn_face_unlock.decision import ColorSample, InfraredSample, Reference, Thresholds, Timing  # noqa: E402
from head_turn_face_unlock.template import EMBEDDING_SIZE, Template, empty_bank  # noqa: E402

TIMING = Timing(1 / 30, 1 / 15)
THRESHOLDS = Thresholds(0.60, 0.65, 0.45, 0.80)
REFERENCE = Reference(face_level=95.0, nose_low=1.1, nose_high=1.3, centroid_slope=0.06)


def turn_yaws() -> list[float]:
    """Look ahead, turn to the right, sweep to the left, come back a little."""
    return (list(np.linspace(0.0, 0.45, 10)) + list(np.linspace(0.45, -0.45, 24))[1:]
            + list(np.linspace(-0.45, 0.0, 6))[1:])


def color_track(yaws: list[float] | None = None, start: float = 100.0, interval: float = 1 / 30,
                width: float = 120.0) -> list[ColorSample]:
    yaws = turn_yaws() if yaws is None else yaws
    return [ColorSample(start + index * interval, yaw, 320 - 100 * yaw, 180.0, width, 100.0 + index % 7)
            for index, yaw in enumerate(yaws)]


def infrared_track(samples: list[ColorSample], similarity: float = 0.9, face_level: float = 90.0,
                   nose_ratio: float = 1.2, slope: float = 0.05) -> list[InfraredSample]:
    """Lit infrared frames every other frame of a 15 fps stream, angles from the color track."""
    times = np.arange(samples[0].timestamp, samples[-1].timestamp, 2 / 15)
    stamps = np.array([sample.timestamp for sample in samples])
    yaws = np.array([sample.yaw for sample in samples])
    result = []
    for index, moment in enumerate(times):
        yaw = float(np.interp(moment, stamps, yaws))
        result.append(InfraredSample(float(moment), yaw, similarity, 50.0 + index, face_level, nose_ratio,
                                     0.5 + slope * yaw))
    return result


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
