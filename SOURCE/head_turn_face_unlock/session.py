"""One verification or one enrollment: both cameras stream, frames are analysed on two threads.

The capture thread reads both devices through one poll() call, decodes each color frame
at half size and finds the face and its 478 landmarks (about 4 ms of a 33 ms frame).
Face embeddings (about 15 ms each) and the infrared frames run on a second thread, and
embeddings only for frames that can matter: the two sharpest frames of each angle bin
and the lit infrared frames. Frames are never written anywhere; compressed color frames
stay in memory for a few seconds so a chosen frame can be decoded at full size later.
"""

from __future__ import annotations

import errno
import math
import queue
import statistics
import threading
import time
from dataclasses import dataclass
from typing import Callable, Protocol

import cv2
import numpy as np

from . import decision, geometry, vision
from . import v4l2_capture as v4l2
from .cameras import CameraPair
from .config import Settings
from .decision import ColorSample, InfraredSample, Reference, Timing, Turn, Verdict
from .landmarks import FaceFinder
from .template import Template, best_similarity, empty_bank, merge

Say = Callable[[str], None]
# Embed early when a frame is among the sharpest of its angle bin over this window.
CANDIDATE_WINDOW_SECONDS = decision.EVIDENCE_SECONDS_BEFORE_TURN + 1.0
KEEP_FRAMES_SECONDS = CANDIDATE_WINDOW_SECONDS + 2.0
INFRARED_HINT_SECONDS = 1.0
JUDGE_GRACE_SECONDS = 0.5
# Enrollment, in degrees of yaw.
STRAIGHT_YAW = 5.0
RIGHT_TURN_YAW = 25.0
REQUIRED_BINS = range(1, 7)
COLOR_TARGET_PER_BIN = 8
INFRARED_TARGET_PER_BIN = 3
SHAPE_TARGET = 15
COLOR_CAP_PER_BIN = 20
INFRARED_CAP_PER_BIN = 10
DUPLICATE_SIMILARITY = 0.97
ENROLL_IDENTITY = 0.50
ENROLL_INFRARED_IDENTITY = 0.60
OWNER_CONFIRMATION_SECONDS = 0.5
SHAPE_SPREAD_FLOOR = 0.01


class SessionError(Exception):
    """Ends a session; the argument is a message key."""


class ModelSet(Protocol):
    """What a session needs from models.Models; tests pass their own."""
    recognizer: vision.Recognizer

    def new_finder(self) -> FaceFinder: ...  # pragma: no cover - interface only


@dataclass
class _Color:
    sample: ColorSample
    jpeg: bytes
    five: np.ndarray  # alignment points in tracking pixels


@dataclass(frozen=True)
class _InfraredMeasure:
    timestamp: float
    yaw: float  # as the camera sees it, before the enrolled direction is applied
    embedding: np.ndarray
    sharpness: float
    level: float


class _Streams:
    def __init__(self, pair: CameraPair) -> None:
        self.color = v4l2.Camera(pair.color_path, pair.color_mode)
        self.infrared = v4l2.Camera(pair.infrared_path, pair.infrared_mode)

    def __enter__(self) -> "_Streams":
        try:
            for camera in (self.color, self.infrared):
                camera.open()
            for camera in (self.color, self.infrared):
                camera.start()
        except OSError as error:
            self.close()
            raise SessionError("CAMERA_BUSY" if error.errno == errno.EBUSY else "CAMERA_MISSING") from error
        return self

    def read(self, timeout: float) -> list[tuple[v4l2.Camera, v4l2.Frame]]:
        return v4l2.wait_for_frames([self.color, self.infrared], timeout)

    def close(self) -> None:
        self.color.close()
        self.infrared.close()

    def __exit__(self, *_: object) -> None:
        self.close()


class _Worker(threading.Thread):
    """Embeds chosen color frames and analyses lit infrared frames off the capture thread."""

    def __init__(self, models: ModelSet) -> None:
        super().__init__(daemon=True, name="embedding")
        self._recognizer = models.recognizer
        self._finder = models.new_finder()
        self._jobs: queue.Queue = queue.Queue()
        self.results: queue.Queue = queue.Queue()
        self._outstanding = 0
        self._lock = threading.Lock()

    def outstanding(self) -> int:
        with self._lock:
            return self._outstanding

    def submit(self, job: tuple) -> None:
        with self._lock:
            self._outstanding += 1
        self._jobs.put(job)

    def stop(self) -> None:
        self._jobs.put(None)
        self.join(timeout=1.0)

    def run(self) -> None:
        while (job := self._jobs.get()) is not None:
            try:
                self.results.put(self._color(job) if job[0] == "color" else self._infrared(job))
            except Exception:  # one bad frame must not end the session
                self.results.put((job[0], job[1], None))
            finally:
                with self._lock:
                    self._outstanding -= 1

    def _color(self, job: tuple) -> tuple:
        _, key, jpeg, five = job
        image = vision.decode_full(jpeg)
        if image is None:
            return ("color", key, None)
        return ("color", key, self._recognizer.embed(image, five * vision.DETECTION_REDUCTION))

    def _infrared(self, job: tuple) -> tuple:
        _, timestamp, lit, difference = job
        image = np.repeat(lit[:, :, None], 3, axis=2)
        _, face = self._finder.find(image)
        if face is None:
            return ("infrared", timestamp, None)
        level = vision.infrared_level(difference, face.points)
        if level is None:
            return ("infrared", timestamp, None)
        yaw, _, _ = geometry.pose(face.points)
        centre_x, centre_y, width = geometry.face_box(face.points)
        embedding = self._recognizer.embed(image, geometry.alignment_points(face.points))
        return ("infrared", timestamp, _InfraredMeasure(timestamp, yaw, embedding,
                                                        vision.sharpness(lit, centre_x, centre_y, width), level))


def _brightness(image: np.ndarray) -> tuple[float, float]:
    """Mean brightness of the whole frame and of its middle third, 0-255."""
    height, width = image.shape[:2]
    middle = image[height // 3:height - height // 3, width // 3:width - width // 3]
    return sum(cv2.mean(image)[:3]) / 3, sum(cv2.mean(middle)[:3]) / 3


class _Capture:
    """Frame handling shared by verification and enrollment."""

    def __init__(self, models: ModelSet, pair: CameraPair, direction: int, clock: Callable[[], float]) -> None:
        self.models = models
        self.pair = pair
        self.direction = direction
        self.clock = clock
        self.timing = Timing(1 / pair.color_mode.frames_per_second, 1 / pair.infrared_mode.frames_per_second)
        self.finder = models.new_finder()
        self.pairer = vision.InfraredPairer()
        self.colors: list[_Color] = []
        self.gaps: list[float] = []
        self.last_infrared_frame = -math.inf
        self.infrared_faces = 0
        self.infrared_empty = 0
        self.tracked_frames = 0
        self.tracking_seconds = 0.0
        # Why color frames had no usable face, for the journal: nothing else reads these.
        self.unreadable = 0
        self.detections_seen: dict[str, int] = {"none": 0, "several": 0, "landmarks rejected": 0}
        self.brightness: list[tuple[float, float]] = []

    def color_frame(self, frame: v4l2.Frame) -> _Color | None:
        started = time.perf_counter()
        try:
            return self._color_frame(frame)
        finally:
            self.tracked_frames += 1
            self.tracking_seconds += time.perf_counter() - started

    def _color_frame(self, frame: v4l2.Frame) -> _Color | None:
        image = vision.decode_half(frame.data)
        if image is None:
            self.unreadable += 1
            return None
        self.brightness.append(_brightness(image))
        count, face = self.finder.find(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        if face is None:
            self.gaps.append(frame.timestamp)
            self.detections_seen["none" if count == 0 else "several" if count > 1 else "landmarks rejected"] += 1
            return None
        yaw, pitch, _ = geometry.pose(face.points)
        centre_x, centre_y, width = geometry.face_box(face.points)
        sample = ColorSample(frame.timestamp, yaw * self.direction, pitch, centre_x, centre_y, width,
                             vision.sharpness(image, centre_x, centre_y, width), geometry.eye_distance(face.points),
                             geometry.rigid_points(face.points), geometry.shape(face.points))
        color = _Color(sample, frame.data, geometry.alignment_points(face.points))
        self.colors.append(color)
        return color

    def infrared_frame(self, frame: v4l2.Frame) -> tuple[np.ndarray, np.ndarray] | None:
        self.last_infrared_frame = frame.timestamp
        if len(frame.data) < frame.stride * frame.mode.height:
            return None
        return self.pairer.push(np.ascontiguousarray(frame.grey()))

    def color_diagnosis(self) -> str:
        """Frames without a usable face by cause, and the median brightness of the whole frame
        and of its middle (0-255): a dark middle in a bright frame means light from behind."""
        causes = ", ".join(f"{cause} {count}" for cause, count in self.detections_seen.items())
        if self.brightness:
            whole, middle = np.median(np.array(self.brightness), axis=0)
            light = f", brightness {whole:.0f} middle {middle:.0f}"
        else:
            light = ""
        return f"{causes}, unreadable {self.unreadable}{light}"

    def forget_old(self) -> None:
        """Drops the compressed data of old frames; their measurements stay."""
        cutoff = self.clock() - KEEP_FRAMES_SECONDS
        for color in self.colors:
            if color.sample.timestamp >= cutoff:
                break
            color.jpeg = b""


class Verification:
    def __init__(self, models: ModelSet, pair: CameraPair, template: Template, settings: Settings, say: Say,
                 cancelled: Callable[[], bool] = lambda: False, clock: Callable[[], float] = time.monotonic) -> None:
        self.cancelled = cancelled
        self.template = template
        self.settings = settings
        self.say = say
        self.capture = _Capture(models, pair, template.direction, clock)
        self.thresholds = decision.Thresholds(settings.MATCH_THRESHOLD, settings.COLOR_MEDIAN_THRESHOLD,
                                              settings.MATCH_FLOOR, settings.INFRARED_THRESHOLD)
        self.similarities: dict[int, float] = {}
        self.requested: set[int] = set()
        self.infrared: list[InfraredSample] = []
        self.statistics = ""

    def run(self, deliver: Callable[[Verdict], None]) -> Verdict:
        """Delivers the verdict before the cameras are closed, so the login does not wait."""
        with _Streams(self.capture.pair) as streams:
            worker = _Worker(self.capture.models)
            worker.start()
            try:
                verdict = self._loop(streams, worker)
                deliver(verdict)
            finally:
                worker.stop()
        return verdict

    def _loop(self, streams: _Streams, worker: _Worker) -> Verdict:
        capture, clock = self.capture, self.capture.clock
        started = clock()
        deadline = started + self.settings.ATTEMPT_SECONDS
        grace = deadline + JUDGE_GRACE_SECONDS
        evaluated: set[tuple[float, float]] = set()
        last_rejection: Verdict | None = None
        pending: tuple[Turn, dict[int, list[int]]] | None = None
        hint_sent = False
        self.say("TURN_HEAD")
        while (now := clock()) < deadline or (pending is not None and now < grace):
            if self.cancelled():
                raise SessionError("CANCELLED")
            for camera, frame in streams.read(min(0.05, (grace if pending else deadline) - now)):
                if camera is streams.color:
                    if capture.color_frame(frame) is not None:
                        self._maybe_embed(len(capture.colors) - 1, worker)
                elif (lit := capture.infrared_frame(frame)) is not None:
                    worker.submit(("infrared", frame.timestamp, *lit))
            self._collect(worker)
            if not hint_sent and self._infrared_blind(started):
                self.say("INFRARED_CANNOT_SEE")
                hint_sent = True
            if pending is None and now < deadline:
                found = decision.find_turn(self._samples(), capture.gaps, capture.timing, evaluated, final=False)
                if isinstance(found, Verdict):
                    last_rejection = found
                elif isinstance(found, Turn):
                    chosen = decision.identity_frames(self._samples(), found)
                    for indexes in chosen.values():
                        for index in indexes:
                            self._request(index, worker)
                    pending = (found, chosen)
            if pending is not None and self._ready(pending, worker):
                verdict = self._judge(*pending)
                pending = None
                if verdict.accepted:
                    return self._finish(verdict, started)
                last_rejection = verdict
            capture.forget_old()
        if pending is not None:
            self._collect(worker)
            verdict = self._judge(*pending)
            if verdict.accepted:
                return self._finish(verdict, started)
            last_rejection = verdict
        final = decision.find_turn(self._samples(), capture.gaps, capture.timing, evaluated, final=True)
        if isinstance(final, Turn):
            self._collect(worker)
            final = self._judge(final, decision.identity_frames(self._samples(), final))
            if final.accepted:
                return self._finish(final, started)
        if isinstance(final, Verdict) and not (last_rejection and final.reason in ("RIGHT_TURN_MISSING",
                                                                                   "LEFT_TURN_MISSING")):
            last_rejection = final
        return self._finish(last_rejection or Verdict(False, "TIME_UP"), started)

    def _samples(self) -> list[ColorSample]:
        return [color.sample for color in self.capture.colors]

    def _finish(self, verdict: Verdict, started: float) -> Verdict:
        capture = self.capture
        tracking = 1000 * capture.tracking_seconds / max(1, capture.tracked_frames)
        self.statistics = (f"{capture.clock() - started:.1f}s color {len(capture.colors)}/{capture.tracked_frames} "
                           f"({capture.color_diagnosis()}) tracking {tracking:.1f} ms/frame infrared faces "
                           f"{capture.infrared_faces}/{capture.infrared_faces + capture.infrared_empty} "
                           f"embeddings {len(self.requested)}")
        return verdict

    def _maybe_embed(self, index: int, worker: _Worker) -> None:
        """Embeds a frame early when it is among the two sharpest of its angle bin lately."""
        colors = self.capture.colors
        sample = colors[index].sample
        angle = decision.bin_of(sample.yaw)
        if angle is None:
            return
        rivals = [color.sample.sharpness for color in reversed(colors[:index])
                  if color.sample.timestamp >= sample.timestamp - CANDIDATE_WINDOW_SECONDS
                  and decision.bin_of(color.sample.yaw) == angle]
        if sum(rival > sample.sharpness for rival in rivals) < decision.IDENTITY_FRAMES_PER_BIN:
            self._request(index, worker)

    def _request(self, index: int, worker: _Worker) -> None:
        color = self.capture.colors[index]
        if index not in self.requested and color.jpeg:
            self.requested.add(index)
            worker.submit(("color", index, color.jpeg, color.five))

    def _collect(self, worker: _Worker) -> None:
        while True:
            try:
                kind, key, value = worker.results.get_nowait()
            except queue.Empty:
                return
            if kind == "color":
                angle = decision.bin_of(self.capture.colors[key].sample.yaw)
                self.similarities[key] = (-1.0 if value is None or angle is None
                                          else best_similarity(self.template.color, angle, value))
            elif value is None:
                self.capture.infrared_empty += 1
            else:
                self.capture.infrared_faces += 1
                yaw = value.yaw * self.template.direction
                angle = decision.bin_of(yaw)
                similarity = -1.0 if angle is None else best_similarity(self.template.infrared, angle, value.embedding)
                self.infrared.append(InfraredSample(value.timestamp, yaw, similarity, value.sharpness, value.level))

    def _infrared_blind(self, started: float) -> bool:
        capture = self.capture
        if capture.clock() - started < INFRARED_HINT_SECONDS or capture.infrared_faces:
            return False
        lit_frames = INFRARED_HINT_SECONDS / capture.timing.infrared_interval / 2
        return capture.infrared_empty >= lit_frames and len(capture.colors) > 0

    def _ready(self, pending: tuple[Turn, dict[int, list[int]]], worker: _Worker) -> bool:
        turn, chosen = pending
        if any(index not in self.similarities for indexes in chosen.values() for index in indexes
               if self.capture.colors[index].jpeg or index in self.requested):
            return False
        end = self.capture.colors[turn.end].sample.timestamp
        margin = decision.INFRARED_WINDOW_INTERVALS * self.capture.timing.infrared_interval
        return self.capture.last_infrared_frame >= end + margin and worker.outstanding() == 0

    def _judge(self, turn: Turn, chosen: dict[int, list[int]]) -> Verdict:
        return decision.judge(self._samples(), turn, chosen, lambda index: self.similarities.get(index, -1.0),
                              self.infrared, self.template.reference, self.thresholds, self.capture.timing)


class Enrollment:
    """Collects color and infrared samples across the angle bins and the face's proportions.
    With an existing template only the enrolled person's face is accepted."""

    def __init__(self, models: ModelSet, pair: CameraPair, settings: Settings, say: Say,
                 progress: Callable[[list[int]], None], existing: Template | None,
                 cancelled: Callable[[], bool] = lambda: False, clock: Callable[[], float] = time.monotonic) -> None:
        self.cancelled = cancelled
        self.settings = settings
        self.say = say
        self.progress = progress
        self.existing = existing
        self.capture = _Capture(models, pair, 1, clock)
        self.color: list[list[np.ndarray]] = [[] for _ in range(decision.BIN_COUNT)]
        self.infrared: list[list[np.ndarray]] = [[] for _ in range(decision.BIN_COUNT)]
        self.levels: list[float] = []
        self.shapes: list[np.ndarray] = []
        self.reference: list[np.ndarray] = []
        self.stage = 0
        self.direction = 1
        self.owner_seen_at = -math.inf
        self.skipped = {"color": 0, "infrared": 0}

    def run(self) -> Template:
        with _Streams(self.capture.pair) as streams:
            worker = _Worker(self.capture.models)
            worker.start()
            try:
                return self._loop(streams, worker)
            finally:
                worker.stop()

    def _loop(self, streams: _Streams, worker: _Worker) -> Template:
        capture, clock = self.capture, self.capture.clock
        deadline = clock() + self.settings.ENROLL_SECONDS
        last_progress, last_warning = 0.0, -math.inf
        self.say("ENROLL_LOOK_STRAIGHT")
        while (now := clock()) < deadline:
            if self.cancelled():
                raise SessionError("CANCELLED")
            for camera, frame in streams.read(min(0.05, deadline - now)):
                if camera is streams.color:
                    self._color(frame, worker)
                elif self.stage == 2 and (lit := capture.infrared_frame(frame)) is not None and worker.outstanding() < 3:
                    worker.submit(("infrared", frame.timestamp, *lit))
            while True:
                try:
                    kind, key, value = worker.results.get_nowait()
                except queue.Empty:
                    break
                if kind == "color" and key[0] == "straight" and self.stage == 0:
                    self._straight(value)
                elif kind == "color" and key[0] == "sample" and value is not None:
                    if not self._add_color(key[1], key[2], value) and clock() - last_warning > 3:
                        self.say("ENROLL_NOT_OWNER")
                        last_warning = clock()
                elif kind == "infrared" and value is not None:
                    self._add_infrared(value)
            if self.stage == 2:
                done = self._percentages()
                if min(done) >= 100 and len(self.shapes) >= SHAPE_TARGET:
                    self.progress(done)
                    return self._build()
                if clock() - last_progress > 0.3:
                    self.progress(done)
                    last_progress = clock()
            capture.forget_old()
        raise SessionError("ENROLL_TIME_UP")

    def _color(self, frame: v4l2.Frame, worker: _Worker) -> None:
        color = self.capture.color_frame(frame)
        if color is None:
            return
        sample = color.sample
        if self.stage == 0 and abs(sample.yaw) < STRAIGHT_YAW and worker.outstanding() == 0:
            worker.submit(("color", ("straight",), color.jpeg, color.five))
        elif self.stage == 1 and abs(sample.yaw) > RIGHT_TURN_YAW:
            self.direction = 1 if sample.yaw > 0 else -1
            if self.existing is not None and self.existing.direction != self.direction:
                raise SessionError("ENROLL_DIRECTION_MISMATCH")
            self.stage = 2
            self.say("ENROLL_SWEEP")
        elif self.stage == 2:
            yaw = sample.yaw * self.direction
            if (abs(yaw) <= decision.FRONTAL_YAW and abs(sample.pitch) <= decision.FRONTAL_PITCH
                    and abs(sample.timestamp - self.owner_seen_at) <= OWNER_CONFIRMATION_SECONDS
                    and np.isfinite(sample.shape).all()):
                self.shapes.append(sample.shape)
            angle = decision.bin_of(yaw)
            if angle is not None and len(self.color[angle]) < COLOR_CAP_PER_BIN and worker.outstanding() < 2:
                worker.submit(("color", ("sample", angle, sample.timestamp), color.jpeg, color.five))

    def _straight(self, embedding: np.ndarray | None) -> None:
        if embedding is None:
            return
        if self.existing is not None:
            known = [bank for bank in self.existing.color if len(bank)]
            if not known or max(float((bank @ embedding).max()) for bank in known) < ENROLL_IDENTITY:
                raise SessionError("ENROLL_OWNER_MISMATCH")
            self.reference = [row for bank in known for row in bank]
        else:
            self.reference = [embedding]
        self.stage = 1
        self.say("ENROLL_TURN_RIGHT")

    def _add_color(self, angle: int, timestamp: float, embedding: np.ndarray) -> bool:
        pool = self.reference + [row for bank in self.color for row in bank]
        if max(float(row @ embedding) for row in pool) < ENROLL_IDENTITY:
            self.skipped["color"] += 1
            return False
        self.owner_seen_at = max(self.owner_seen_at, timestamp)
        bank = self.color[angle]
        if len(bank) < COLOR_CAP_PER_BIN and all(float(row @ embedding) < DUPLICATE_SIMILARITY for row in bank):
            bank.append(embedding)
        return True

    def _add_infrared(self, measure: _InfraredMeasure) -> None:
        angle = decision.bin_of(measure.yaw * self.direction)
        if angle is None or len(self.infrared[angle]) >= INFRARED_CAP_PER_BIN:
            return
        if abs(measure.timestamp - self.owner_seen_at) > OWNER_CONFIRMATION_SECONDS:
            return
        pool = [row for bank in self.infrared for row in bank]
        if self.existing is not None:
            pool += [row for bank in self.existing.infrared for row in bank]
        if pool and max(float(row @ measure.embedding) for row in pool) < ENROLL_INFRARED_IDENTITY:
            self.skipped["infrared"] += 1
            return
        self.infrared[angle].append(measure.embedding)
        self.levels.append(measure.level)

    def _percentages(self) -> list[int]:
        return [min(100, int(100 * min(len(self.color[b]) / COLOR_TARGET_PER_BIN,
                                       len(self.infrared[b]) / INFRARED_TARGET_PER_BIN)))
                for b in REQUIRED_BINS]

    def _build(self) -> Template:
        shapes = np.array(self.shapes)
        median = np.median(shapes, axis=0)
        spread = np.maximum(np.std(shapes, axis=0), SHAPE_SPREAD_FLOOR * median)
        reference = Reference(float(statistics.median(self.levels)), median, spread)
        color, infrared = empty_bank(), empty_bank()
        for angle in range(decision.BIN_COUNT):
            if self.color[angle]:
                color[angle] = np.array(self.color[angle], np.float32)
            if self.infrared[angle]:
                infrared[angle] = np.array(self.infrared[angle], np.float32)
        template = Template(self.direction, color, infrared, reference, time.strftime("%Y-%m-%d %H:%M"), [])
        if self.existing is not None:
            template = merge(self.existing, template)
        return template


def describe_template(template: Template) -> str:
    color, infrared = template.counts()
    return f"color {sum(color)} infrared {sum(infrared)} samples, median color per bin {statistics.median(color):.0f}"
