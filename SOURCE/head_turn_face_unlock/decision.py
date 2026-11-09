"""Decides whether a head turn proves identity and liveness.

Everything here is a pure function of measurements, so every rule can be tested without
a camera. Every check is written as "reject unless the good condition holds", so a value
that is not a number (NaN) can only lead to a rejection.

The person turns the head from their right to their left. The turn must
  - reach 20 degrees to each side, mostly in one direction, through at least 4 angle bins,
  - be one unbroken track: no frame without exactly one face, no jump in position or size
    between frames (two recordings cannot be spliced),
  - move like a three-dimensional head: between the two ends of the turn the nose and the
    visible outline shift against each other, which no flat picture can do,
  - match the enrolled face in color in every angle bin it crosses,
  - keep the enrolled face's proportions on the frames that look at the camera,
  - be seen by the infrared camera often enough, match the enrolled face there too, and
    reflect the emitter's light like the enrolled face.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np

from .geometry import parallax

# Angle bins in degrees of yaw; positive is the person's right.
BINS = np.linspace(-40.0, 40.0, 9)
BIN_COUNT = len(BINS) - 1
TURN_SIDE = 20.0
# Landmark noise: a step back of up to this many degrees still counts as one direction.
JITTER = 3.0
MONOTONIC_FRACTION = 0.7
MINIMUM_TURN_FRAMES = 6
MINIMUM_ANGLE_BINS = 4
# Continuity between two frames, scaled by how many frame intervals apart they are.
MAXIMUM_GAP_INTERVALS = 6
JUMP_FACE_WIDTHS_PER_INTERVAL = 0.35
SIZE_RATIO_RANGE = (0.7, 1.4)
# Identity evidence may also come from the unbroken track just before the turn.
EVIDENCE_SECONDS_BEFORE_TURN = 1.5
IDENTITY_FRAMES_PER_BIN = 2
MATCH_FRACTION = 0.8
INFRARED_WINDOW_INTERVALS = 2
MINIMUM_INFRARED_FRAMES = 4
INFRARED_LEVEL_FRACTION = 0.4
# Parallax as a fraction of the eye distance. A flat picture turned 10-30 degrees stays
# under 1.2% (simulated with the measured landmark noise); the author's real turns gave
# 44-176%, and 20-30 degree differences 13.8% at the 5th percentile.
PARALLAX_MINIMUM = 0.15
PARALLAX_PAIRS = 3
# Proportions are compared on frames that look at the camera.
FRONTAL_YAW = 10.0
FRONTAL_PITCH = 15.0
MINIMUM_FRONTAL_FRAMES = 3
SHAPE_SPREADS = 3.0
SHAPE_FLOOR = 0.03
# A refusal after a turn shorter than this also asks for a slower turn: at 30 frames per
# second such a turn leaves blurred frames and only one or two frames per angle (journal of
# 2026-11-09: refused turns of 0.25-0.56 s, accepted ones mostly 0.4-1.2 s).
QUICK_TURN_SECONDS = 0.5
# Refusals that happen only when a turn gives too few frames: they always ask for a slower turn.
_TOO_FEW_FRAMES = ("SHAPE_UNMEASURED", "INFRARED_TOO_FEW")


def bin_of(yaw: float) -> int | None:
    if not math.isfinite(yaw):
        return None
    index = int(np.searchsorted(BINS, yaw)) - 1
    return index if 0 <= index < BIN_COUNT else None


@dataclass(frozen=True, eq=False)
class ColorSample:
    """One color frame with exactly one face. Positions are in tracking pixels."""
    timestamp: float
    yaw: float
    pitch: float
    center_x: float
    center_y: float
    width: float
    sharpness: float
    eye_distance: float
    rigid: np.ndarray
    shape: np.ndarray


@dataclass(frozen=True)
class InfraredSample:
    timestamp: float
    yaw: float
    similarity: float
    sharpness: float
    level: float


@dataclass(frozen=True, eq=False)
class Reference:
    """Measurements taken at enrollment."""
    infrared_level: float
    shape_median: np.ndarray
    shape_spread: np.ndarray


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
    seconds: float = math.inf


@dataclass(frozen=True)
class Verdict:
    accepted: bool
    reason: str
    details: str = ""
    hint: str = ""  # a message key added to the reason's text, or ""


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
    details = (f"frames {len(segment)}, {last - first:.2f}s, {segment[0].yaw:+.0f} to {segment[-1].yaw:+.0f} deg, "
               f"one way {100 * one_way:.0f}%, angles {len(covered)}")
    if not (len(segment) >= MINIMUM_TURN_FRAMES and len(covered) >= MINIMUM_ANGLE_BINS):
        return Verdict(False, "TURN_TOO_FAST", details)
    if not one_way >= MONOTONIC_FRACTION:
        return Verdict(False, "TURN_NOT_ONE_WAY", details)
    lost = [gap for gap in gaps if first < gap < last]
    if lost:
        before = [sample for sample in segment if sample.timestamp < lost[0]][-1]
        after = next(sample for sample in segment if sample.timestamp > lost[0])
        return _refusal("TRACK_BROKEN", f"{details}, face not found in {len(lost)} frames, first between "
                                        f"{before.yaw:+.0f} and {after.yaw:+.0f} deg", last - first)
    for before, after in zip(segment, segment[1:]):
        broken = _discontinuity(before, after, timing.color_interval)
        if broken:
            return _refusal("TRACK_BROKEN", f"{details}, {broken} at {after.yaw:+.0f} deg", last - first)
    evidence_start = turn_start
    while evidence_start > 0:
        before, after = samples[evidence_start - 1], samples[evidence_start]
        if before.timestamp < first - EVIDENCE_SECONDS_BEFORE_TURN or not _continuous(before, after, timing.color_interval):
            break
        if any(before.timestamp < gap < after.timestamp for gap in gaps):
            break
        evidence_start -= 1
    return Turn(evidence_start, turn_start, end, details, last - first)


def identity_frames(samples: Sequence[ColorSample], turn: Turn) -> dict[int, list[int]]:
    """The sharpest frames of every angle bin in the evidence window: the only frames whose
    face embedding is needed."""
    by_bin: dict[int, list[int]] = {}
    for index in range(turn.evidence_start, turn.end + 1):
        angle = bin_of(samples[index].yaw)
        if angle is not None:
            by_bin.setdefault(angle, []).append(index)
    return {angle: sorted(indexes, key=lambda i: -samples[i].sharpness)[:IDENTITY_FRAMES_PER_BIN]
            for angle, indexes in sorted(by_bin.items())}


def three_dimensional_motion(samples: Sequence[ColorSample], turn: Turn) -> float:
    """Median parallax between the frames nearest the right end of the turn and the frames
    nearest its left end."""
    segment = list(samples[turn.turn_start:turn.end + 1])
    rightmost = sorted(segment, key=lambda sample: -sample.yaw)[:PARALLAX_PAIRS]
    leftmost = sorted(segment, key=lambda sample: sample.yaw)[:PARALLAX_PAIRS]
    values = [parallax(a.rigid, b.rigid, a.eye_distance, b.eye_distance) for a, b in zip(rightmost, leftmost)]
    return float(np.median(values)) if values else 0.0


def shape_deviation(samples: Sequence[ColorSample], turn: Turn, reference: Reference) -> tuple[float, int]:
    """Mean distance of the face's proportions from the enrolled ones, in tolerances
    (1 means on average at the edge), and how many frontal frames it rests on."""
    frontal = [sample.shape for sample in samples[turn.evidence_start:turn.end + 1]
               if abs(sample.yaw) <= FRONTAL_YAW and abs(sample.pitch) <= FRONTAL_PITCH]
    if len(frontal) < MINIMUM_FRONTAL_FRAMES:
        return math.inf, len(frontal)
    measured = np.median(np.array(frontal), axis=0)
    tolerance = np.maximum(SHAPE_SPREADS * reference.shape_spread, SHAPE_FLOOR * np.abs(reference.shape_median))
    return float(np.mean(np.abs(measured - reference.shape_median) / tolerance)), len(frontal)


def judge(samples: Sequence[ColorSample], turn: Turn, chosen: dict[int, list[int]],
          similarity: Callable[[int], float], infrared: Sequence[InfraredSample],
          reference: Reference, thresholds: Thresholds, timing: Timing) -> Verdict:
    details = turn.details
    per_bin = np.array([max(_measured(similarity(index)) for index in indexes) for indexes in chosen.values()],
                       dtype=float)
    if len(per_bin) == 0:
        return _refusal("COLOR_MISMATCH", details, turn.seconds)
    details += f", color bins {len(per_bin)} min {per_bin.min():.2f} median {np.median(per_bin):.2f}"
    if not ((per_bin >= thresholds.match).mean() >= MATCH_FRACTION
            and np.median(per_bin) >= thresholds.color_median
            and per_bin.min() >= thresholds.match_floor):
        return _refusal("COLOR_MISMATCH", details, turn.seconds)

    motion = three_dimensional_motion(samples, turn)
    details += f", parallax {100 * motion:.0f}%"
    if not motion >= PARALLAX_MINIMUM:
        return _refusal("NOT_THREE_DIMENSIONAL", details, turn.seconds)

    deviation, frontal = shape_deviation(samples, turn, reference)
    details += f", shape {deviation:.2f} over {frontal} frames"
    if frontal < MINIMUM_FRONTAL_FRAMES:
        return _refusal("SHAPE_UNMEASURED", details, turn.seconds)
    if not deviation <= 1.0:
        return _refusal("SHAPE_MISMATCH", details, turn.seconds)

    margin = INFRARED_WINDOW_INTERVALS * timing.infrared_interval
    window_start = samples[turn.evidence_start].timestamp - margin
    window_end = samples[turn.end].timestamp + margin
    seen = [sample for sample in infrared if window_start <= sample.timestamp <= window_end]
    sharpest: dict[int, InfraredSample] = {}
    for sample in seen:
        angle = bin_of(sample.yaw)
        if angle is not None and (angle not in sharpest or sample.sharpness > sharpest[angle].sharpness):
            sharpest[angle] = sample
    details += f", infrared frames {len(seen)} bins {len(sharpest)}"
    if not (len(seen) >= MINIMUM_INFRARED_FRAMES and sharpest):
        return _refusal("INFRARED_TOO_FEW", details, turn.seconds)
    infrared_median = statistics.median(sample.similarity for sample in sharpest.values())
    level = statistics.median(sample.level for sample in seen)
    details += f" median {infrared_median:.2f}, level {level:.0f}/{reference.infrared_level:.0f}"
    if not infrared_median >= thresholds.infrared:
        return _refusal("INFRARED_MISMATCH", details, turn.seconds)
    if not level >= INFRARED_LEVEL_FRACTION * reference.infrared_level:
        return _refusal("INFRARED_NOT_LIVE", details, turn.seconds)
    return Verdict(True, "ACCEPTED", details)


def _refusal(reason: str, details: str, seconds: float) -> Verdict:
    """A rejection; after a quick turn, or when too few frames were the cause, it asks for a
    slower turn."""
    slower = reason in _TOO_FEW_FRAMES or seconds < QUICK_TURN_SECONDS
    return Verdict(False, reason, details, "TURN_SLOWER" if slower else "")


def _measured(similarity: float) -> float:
    """A similarity that could not be measured counts as no match. (max() over a NaN would
    depend on where the NaN stands.)"""
    return similarity if math.isfinite(similarity) else -1.0


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
            if index > peak and yaw <= -TURN_SIDE:
                return peak, index
    return "LEFT_TURN_MISSING" if saw_right else "RIGHT_TURN_MISSING"


def _continuous(before: ColorSample, after: ColorSample, interval: float) -> bool:
    return not _discontinuity(before, after, interval)


def _discontinuity(before: ColorSample, after: ColorSample, interval: float) -> str:
    """Why the face may not be the same face from one frame to the next, or "" when it is.
    Every test is written so that a value that is not a number fails it."""
    elapsed = after.timestamp - before.timestamp
    if not 0 < elapsed <= MAXIMUM_GAP_INTERVALS * interval:
        return f"no frame for {elapsed:.2f} s"
    width = max(before.width, 1.0)
    jump = math.hypot(after.center_x - before.center_x, after.center_y - before.center_y) / width
    if not jump <= JUMP_FACE_WIDTHS_PER_INTERVAL * max(1.0, elapsed / interval):
        return f"moved {jump:.2f} face widths in {elapsed / interval:.0f} frames"
    ratio = after.width / width
    if not SIZE_RATIO_RANGE[0] < ratio < SIZE_RATIO_RANGE[1]:
        return f"size changed {ratio:.2f} times"
    return ""
