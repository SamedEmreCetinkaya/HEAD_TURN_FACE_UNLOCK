"""Decides whether a head turn proves identity and liveness.

Everything here is a pure function of measurements, so the rules can be tested
without a camera. Every check is written as "reject unless the good condition
holds", so a value that is not a number (NaN) can only lead to a rejection.

The person turns the head from their right to their left. The turn must
  - sweep at least TURN_SIDE to each side and TURN_TOTAL in all, in one direction,
  - be one unbroken track: no frame without the face or with a second face, no
    jump in position or size between frames (two recordings cannot be spliced),
  - match the enrolled face in color in every angle bin it crosses,
  - be seen by the infrared camera, match the enrolled face there too, show a
    skin-like reflection and move the infrared brightness centroid the way a
    three-dimensional head does.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

# Yaw index: nose offset from the face axis divided by face size, roughly sin(yaw).
BINS = np.linspace(-0.6, 0.6, 9)
BIN_COUNT = len(BINS) - 1
TURN_SIDE = 0.30
TURN_TOTAL = 0.60
# Landmark noise: a step back of up to this much still counts as moving in one direction.
JITTER = 0.03
MONOTONIC_FRACTION = 0.7
MINIMUM_TURN_FRAMES = 6
MINIMUM_ANGLE_BINS = 4
# Continuity between two frames, scaled by how many frame intervals apart they are.
MAXIMUM_GAP_INTERVALS = 6
JUMP_FACE_WIDTHS_PER_INTERVAL = 0.35
SIZE_RATIO_RANGE = (0.7, 1.4)
# Identity evidence may also come from the unbroken track just before the turn,
# where people slow down at their right and frames are sharp.
EVIDENCE_SECONDS_BEFORE_TURN = 1.5
IDENTITY_FRAMES_PER_BIN = 2
MATCH_FRACTION = 0.8
INFRARED_WINDOW_INTERVALS = 2
MINIMUM_INFRARED_FRAMES = 4
FACE_LEVEL_FRACTION = 0.4
NOSE_RATIO_MARGIN = 0.15
DEPTH_SLOPE_FRACTION = 0.4
DEPTH_MINIMUM_YAW_SPAN = 0.2


def bin_of(yaw: float) -> int | None:
    if not math.isfinite(yaw):
        return None
    index = int(np.searchsorted(BINS, yaw)) - 1
    return index if 0 <= index < BIN_COUNT else None


@dataclass(frozen=True)
class ColorSample:
    """One color frame with exactly one face. Positions are in detection pixels."""
    timestamp: float
    yaw: float
    center_x: float
    center_y: float
    width: float
    sharpness: float


@dataclass(frozen=True)
class InfraredSample:
    timestamp: float
    yaw: float
    similarity: float
    sharpness: float
    face_level: float
    nose_ratio: float
    centroid: float


@dataclass(frozen=True)
class Reference:
    """Infrared measurements taken at enrollment."""
    face_level: float
    nose_low: float
    nose_high: float
    centroid_slope: float


@dataclass(frozen=True)
class Thresholds:
    match: float
    color_median: float
    match_floor: float
    infrared: float


@dataclass(frozen=True)
class Timing:
    color_interval: float
    infrared_interval: float


@dataclass(frozen=True)
class Turn:
    evidence_start: int
    turn_start: int
    end: int
    details: str


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    reason: str
    details: str = ""


def find_turn(samples: Sequence[ColorSample], gaps: Sequence[float], timing: Timing,
              evaluated: set[tuple[float, float]], final: bool) -> Turn | Verdict | None:
    """The latest complete right-to-left turn, a rejection, or None while still waiting.
    A turn is judged once; evaluated remembers the ones already judged."""
    if len(samples) < MINIMUM_TURN_FRAMES:
        return Verdict(False, "NO_FACE") if final else None
    sweep = _find_sweep([sample.yaw for sample in samples])
    if isinstance(sweep, str):
        return Verdict(False, sweep) if final else None
    turn_start, end = sweep
    first, last = samples[turn_start].timestamp, samples[end].timestamp
    if (first, last) in evaluated:
        return None
    evaluated.add((first, last))
    segment = samples[turn_start:end + 1]
    steps = np.diff([sample.yaw for sample in segment])
    one_way = float((steps <= JITTER).mean()) if len(steps) else 0.0
    covered = {bin_of(sample.yaw) for sample in segment} - {None}
    details = f"frames {len(segment)}, {last - first:.2f}s, one way {100 * one_way:.0f}%, angles {len(covered)}"
    if not (len(segment) >= MINIMUM_TURN_FRAMES and len(covered) >= MINIMUM_ANGLE_BINS):
        return Verdict(False, "TURN_TOO_FAST", details)
    if not one_way >= MONOTONIC_FRACTION:
        return Verdict(False, "TURN_NOT_ONE_WAY", details)
    if any(first < gap < last for gap in gaps):
        return Verdict(False, "TRACK_BROKEN", details)
    for before, after in zip(segment, segment[1:]):
        if not _continuous(before, after, timing.color_interval):
            return Verdict(False, "TRACK_BROKEN", details)
    evidence_start = turn_start
    while evidence_start > 0:
        before, after = samples[evidence_start - 1], samples[evidence_start]
        if before.timestamp < first - EVIDENCE_SECONDS_BEFORE_TURN:
            break
        if not _continuous(before, after, timing.color_interval):
            break
        if any(before.timestamp < gap < after.timestamp for gap in gaps):
            break
        evidence_start -= 1
    return Turn(evidence_start, turn_start, end, details)


def identity_frames(samples: Sequence[ColorSample], turn: Turn) -> dict[int, list[int]]:
    """The sharpest frames of every angle bin in the evidence window, which are the only
    ones whose face embedding is needed."""
    by_bin: dict[int, list[int]] = {}
    for index in range(turn.evidence_start, turn.end + 1):
        angle = bin_of(samples[index].yaw)
        if angle is not None:
            by_bin.setdefault(angle, []).append(index)
    return {angle: sorted(indexes, key=lambda i: -samples[i].sharpness)[:IDENTITY_FRAMES_PER_BIN]
            for angle, indexes in sorted(by_bin.items())}


def judge(samples: Sequence[ColorSample], turn: Turn, chosen: dict[int, list[int]],
          similarity: Callable[[int], float], infrared: Sequence[InfraredSample],
          reference: Reference, thresholds: Thresholds, timing: Timing) -> Verdict:
    details = turn.details
    per_bin = np.array([max(similarity(index) for index in indexes) for indexes in chosen.values()], dtype=float)
    if len(per_bin) == 0:
        return Verdict(False, "COLOR_MISMATCH", details)
    details += (f", color bins {len(per_bin)} min {per_bin.min():.2f} median {np.median(per_bin):.2f}")
    if not ((per_bin >= thresholds.match).mean() >= MATCH_FRACTION
            and np.median(per_bin) >= thresholds.color_median
            and per_bin.min() >= thresholds.match_floor):
        return Verdict(False, "COLOR_MISMATCH", details)

    margin = INFRARED_WINDOW_INTERVALS * timing.infrared_interval
    window_start = samples[turn.evidence_start].timestamp - margin
    window_end = samples[turn.end].timestamp + margin
    seen = [sample for sample in infrared if window_start <= sample.timestamp <= window_end]
    infrared_bins: dict[int, InfraredSample] = {}
    for sample in seen:
        angle = bin_of(sample.yaw)
        if angle is not None and (angle not in infrared_bins or sample.sharpness > infrared_bins[angle].sharpness):
            infrared_bins[angle] = sample
    details += f", infrared frames {len(seen)} bins {len(infrared_bins)}"
    if not (len(seen) >= MINIMUM_INFRARED_FRAMES and infrared_bins):
        return Verdict(False, "INFRARED_TOO_FEW", details)
    infrared_median = statistics.median(sample.similarity for sample in infrared_bins.values())
    face_level = statistics.median(sample.face_level for sample in seen)
    nose_ratio = statistics.median(sample.nose_ratio for sample in seen)
    yaws = np.array([sample.yaw for sample in seen])
    centroids = np.array([sample.centroid for sample in seen])
    slope = float(np.polyfit(yaws, centroids, 1)[0]) if np.ptp(yaws) > DEPTH_MINIMUM_YAW_SPAN else 0.0
    details += (f" median {infrared_median:.2f}, level {face_level:.1f}/{reference.face_level:.1f}, "
                f"nose {nose_ratio:.2f}, slope {slope:+.3f}/{reference.centroid_slope:+.3f}")
    if not infrared_median >= thresholds.infrared:
        return Verdict(False, "INFRARED_MISMATCH", details)
    if not (face_level >= FACE_LEVEL_FRACTION * reference.face_level
            and reference.nose_low - NOSE_RATIO_MARGIN <= nose_ratio <= reference.nose_high + NOSE_RATIO_MARGIN):
        return Verdict(False, "INFRARED_NOT_LIVE", details)
    if not (reference.centroid_slope * slope > 0
            and abs(slope) >= DEPTH_SLOPE_FRACTION * abs(reference.centroid_slope)):
        return Verdict(False, "DEPTH_CUE_FAILED", details)
    return Verdict(True, "ACCEPTED", details)


def _find_sweep(yaws: Sequence[float]) -> tuple[int, int] | str:
    """Index of the rightmost point so far and of the first later point far enough to the left."""
    peak = -1
    saw_right = False
    for index, yaw in enumerate(yaws):
        if not math.isfinite(yaw):
            continue
        if peak < 0 or yaw > yaws[peak]:
            peak = index
        if yaws[peak] >= TURN_SIDE:
            saw_right = True
            if index > peak and yaw <= -TURN_SIDE and yaws[peak] - yaw >= TURN_TOTAL:
                return peak, index
    return "LEFT_TURN_MISSING" if saw_right else "RIGHT_TURN_MISSING"


def _continuous(before: ColorSample, after: ColorSample, interval: float) -> bool:
    elapsed = after.timestamp - before.timestamp
    width = max(before.width, 1.0)
    jump = math.hypot(after.center_x - before.center_x, after.center_y - before.center_y) / width
    ratio = after.width / width
    return (0 < elapsed <= MAXIMUM_GAP_INTERVALS * interval
            and jump <= JUMP_FACE_WIDTHS_PER_INTERVAL * max(1.0, elapsed / interval)
            and SIZE_RATIO_RANGE[0] < ratio < SIZE_RATIO_RANGE[1])
