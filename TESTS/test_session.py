"""Whole verification and enrollment sessions with scripted cameras and models.

Color frames carry their index and lit infrared frames carry the head angle in their
pixels; the fake face finder turns them into landmarks of a synthetic 3D head. The real
capture loop, pairing, geometry, embedding requests and decision run unchanged on a fake
clock without a camera or a model.
"""

import re
import unittest
from unittest import mock

import numpy as np

import helpers
from helpers import REFERENCE, view

from head_turn_face_unlock import session, vision
from head_turn_face_unlock.cameras import CameraPair
from head_turn_face_unlock.config import Settings
from head_turn_face_unlock.decision import BIN_COUNT
from head_turn_face_unlock.landmarks import Face
from head_turn_face_unlock.template import Template, empty_bank
from head_turn_face_unlock.v4l2_capture import Frame, Mode

COLOR_MODE, INFRARED_MODE = Mode("MJPG", 1280, 720, 30.0), Mode("GREY", 640, 360, 15.0)
PAIR = CameraPair("/dev/color", COLOR_MODE, "/dev/infrared", INFRARED_MODE)
OWNER, OWNER_INFRARED = helpers.random_units(1, 7)[0], helpers.random_units(1, 8)[0]


def encode_yaw(yaw: float) -> int:
    return int(round(60 + 2 * (yaw + 40)))


def decode_yaw(value: float) -> float:
    return (value - 60) / 2 - 40


class Script:
    """Color frames at 30 fps following yaws (None: no face); infrared frames at 15 fps,
    every other one lit with the head angle written into its pixels."""

    def __init__(self, yaws, start=50.0):
        self.yaws, self.now, self.events, self.log = yaws, start, [], []
        for index in range(len(yaws)):
            self.events.append((start + index / 30, "color", index))
        for index in range(len(yaws) // 2):
            moment = start + index / 15 + 0.012
            yaw = yaws[min(len(yaws) - 1, int(round((moment - start) * 30)))]
            lit = index % 2 == 0 and yaw is not None
            self.events.append((moment, "infrared", encode_yaw(yaw) if lit else 0))
        self.events.sort()

    def clock(self):
        return self.now


class FakeStreams:
    script: Script = None

    def __init__(self, pair):
        self.color, self.infrared = object(), object()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.script.log.append("closed")

    def read(self, timeout):
        script = self.script
        due = [event for event in script.events if event[0] <= script.now + timeout]
        if not due:
            script.now += timeout
            return []
        script.now = max(script.now, due[0][0])
        ready = [event for event in script.events if event[0] <= script.now]
        script.events = [event for event in script.events if event[0] > script.now]
        frames = []
        for stamp, kind, value in ready:
            if kind == "color":
                frames.append((self.color, Frame(value, stamp, value.to_bytes(4, "little"), COLOR_MODE, 1280)))
            else:
                frames.append((self.infrared, Frame(value, stamp, bytes([value]) * (640 * 360), INFRARED_MODE, 640)))
        return frames


class FakeFinder:
    def __init__(self, yaws, infrared_blind=False, faceless=None):
        self.yaws, self.infrared_blind, self.faceless = yaws, infrared_blind, faceless or {}

    def find(self, rgb):
        if rgb.shape[:2] == (1, 1):  # a color frame marker
            index = int(rgb[0, 0, 0])
            if index in self.faceless:  # detections seen in a frame whose face is not usable
                return self.faceless[index], None
            yaw = self.yaws[index]
            return (0, None) if yaw is None else (1, Face(view(yaw), 1.0))
        if self.infrared_blind or rgb.max() == 0:
            return 0, None
        return 1, Face(view(decode_yaw(float(rgb.max()))), 1.0)


class FakeRecognizer:
    def __init__(self, owner, owner_infrared, noise):
        self.owner, self.owner_infrared, self.noise = owner, owner_infrared, noise
        self.random = np.random.default_rng(3)

    def embed(self, image, five):
        base = self.owner if image.shape == (2, 2, 3) else self.owner_infrared
        return helpers.unit(base + self.noise * self.random.normal(size=base.shape))


class FakeModels:
    def __init__(self, yaws, owner=OWNER, owner_infrared=OWNER_INFRARED, noise=0.0, infrared_blind=False,
                 faceless=None):
        self.recognizer = FakeRecognizer(owner, owner_infrared, noise)
        self.yaws, self.infrared_blind, self.faceless = yaws, infrared_blind, faceless

    def new_finder(self):
        return FakeFinder(self.yaws, self.infrared_blind, self.faceless)


class SyncWorker(session._Worker):
    def start(self):
        pass

    def stop(self):
        pass

    def submit(self, job):
        self.results.put(self._color(job) if job[0] == "color" else self._infrared(job))


def owner_template():
    color, infrared = empty_bank(), empty_bank()
    for angle in range(BIN_COUNT):
        color[angle] = OWNER[None, :]
        infrared[angle] = OWNER_INFRARED[None, :]
    return Template(1, color, infrared, REFERENCE, "2026-01-31 12:00", [])


class SessionTestCase(unittest.TestCase):
    def use(self, script):
        FakeStreams.script = script
        marker = (lambda data: np.full((1, 1, 3), int.from_bytes(data, "little"), np.float32))
        for patch in (mock.patch.object(session, "_Streams", FakeStreams), mock.patch.object(session, "_Worker", SyncWorker),
                      mock.patch.object(vision, "decode_half", marker),
                      mock.patch.object(vision, "decode_full", lambda data: np.ones((2, 2, 3), np.uint8))):
            patch.start()
            self.addCleanup(patch.stop)


class VerificationTests(SessionTestCase):
    def verify(self, yaws, models=None, cancelled=lambda: False):
        script = Script(yaws)
        self.use(script)
        said = []
        verification = session.Verification(models or FakeModels(yaws), PAIR, owner_template(), Settings(),
                                            said.append, cancelled=cancelled, clock=script.clock)
        verdict = verification.run(lambda result: script.log.append("delivered"))
        self.statistics = verification.statistics
        return verdict, said, script

    def test_the_owner_turning_is_accepted_before_the_cameras_close(self):
        verdict, said, script = self.verify(helpers.turn_yaws() + [0.0] * 30)
        self.assertTrue(verdict.accepted, verdict)
        self.assertEqual(said[0], "TURN_HEAD")
        self.assertEqual(script.log, ["delivered", "closed"])
        self.assertLess(script.now - 50.0, 2.5)

    def test_another_person_is_rejected(self):
        yaws = helpers.turn_yaws() + [0.0] * 30
        verdict, _, _ = self.verify(yaws, models=FakeModels(yaws, owner=helpers.random_units(1, 99)[0]))
        self.assertEqual(verdict.reason, "COLOR_MISMATCH")

    def test_no_turn_runs_until_the_time_is_up(self):
        verdict, _, script = self.verify([0.0] * 200)
        self.assertEqual(verdict.reason, "RIGHT_TURN_MISSING")
        self.assertGreaterEqual(script.now - 50.0, Settings().ATTEMPT_SECONDS)

    def test_frames_without_a_face_break_the_turn(self):
        yaws = helpers.turn_yaws() + [0.0] * 60
        yaws[18] = None
        verdict, _, _ = self.verify(yaws)
        self.assertEqual(verdict.reason, "TRACK_BROKEN")

    def test_the_journal_says_why_color_frames_had_no_face(self):
        yaws = [0.0] * 160
        verdict, _, _ = self.verify(yaws, models=FakeModels(yaws, faceless={3: 0, 4: 2, 5: 3, 6: 1}))
        self.assertEqual(verdict.reason, "RIGHT_TURN_MISSING")
        self.assertRegex(self.statistics, r"^5\.0s color (\d+)/(\d+) \(none 1, several 2, landmarks rejected 1, "
                                          r"unreadable 0, brightness \d+ middle \d+\) tracking [0-9.]+ ms/frame "
                                          r"infrared faces (\d+)/\3 embeddings \d+$")
        found, frames = map(int, re.match(r"[0-9.]+s color (\d+)/(\d+)", self.statistics).groups())
        self.assertEqual(frames - found, 4)

    def test_brightness_shows_light_from_behind(self):
        frame = np.full((90, 160, 3), 200, np.uint8)
        frame[30:60, 53:107] = 20
        whole, middle = session._brightness(frame)
        self.assertAlmostEqual(whole, (200 * (90 * 160 - 30 * 54) + 20 * 30 * 54) / (90 * 160), places=3)
        self.assertAlmostEqual(middle, 20.0)
        self.assertEqual(session._brightness(np.full((1, 1, 3), 7, np.uint8)), (7.0, 7.0))

    def test_a_session_without_readable_frames_still_reports(self):
        capture = session._Capture(FakeModels([]), PAIR, 1, lambda: 0.0)
        capture.unreadable = 4
        self.assertEqual(capture.color_diagnosis(), "none 0, several 0, landmarks rejected 0, unreadable 4")

    def test_a_blind_infrared_camera_is_reported(self):
        yaws = helpers.turn_yaws() + [0.0] * 60
        verdict, said, _ = self.verify(yaws, models=FakeModels(yaws, infrared_blind=True))
        self.assertFalse(verdict.accepted)
        self.assertIn("INFRARED_CANNOT_SEE", said)

    def test_cancelling_stops_the_session(self):
        with self.assertRaises(session.SessionError) as raised:
            self.verify([0.0] * 200, cancelled=lambda: True)
        self.assertEqual(raised.exception.args[0], "CANCELLED")

    def test_cameras_that_cannot_open_are_reported(self):
        pair = CameraPair("/nonexistent/video0", COLOR_MODE, "/nonexistent/video2", INFRARED_MODE)
        with self.assertRaises(session.SessionError) as raised, session._Streams(pair):
            pass
        self.assertEqual(raised.exception.args[0], "CAMERA_MISSING")


class EnrollmentTests(SessionTestCase):
    def enroll(self, yaws, models, existing=None):
        script = Script(yaws)
        self.use(script)
        said, progress = [], []
        enrollment = session.Enrollment(models, PAIR, Settings(ENROLL_SECONDS=60), said.append, progress.append,
                                        existing, clock=script.clock)
        return enrollment.run(), said, progress

    @staticmethod
    def sweeps(count=6):
        yaws = [0.0] * 10 + list(np.linspace(0, 35, 15))
        for _ in range(count):
            yaws += list(np.linspace(35, -35, 45)) + list(np.linspace(-35, 35, 45))
        return yaws

    def test_enrollment_collects_every_required_angle_and_the_proportions(self):
        yaws = self.sweeps()
        template, said, progress = self.enroll(yaws, FakeModels(yaws, noise=0.02))
        self.assertEqual(said[:3], ["ENROLL_LOOK_STRAIGHT", "ENROLL_TURN_RIGHT", "ENROLL_SWEEP"])
        self.assertEqual(progress[-1], [100] * len(session.REQUIRED_BINS))
        color, infrared = template.counts()
        for angle in session.REQUIRED_BINS:
            self.assertGreaterEqual(color[angle], session.COLOR_TARGET_PER_BIN)
            self.assertGreaterEqual(infrared[angle], session.INFRARED_TARGET_PER_BIN)
        np.testing.assert_allclose(template.reference.shape_median, REFERENCE.shape_median, rtol=0.05)
        self.assertGreater(template.reference.infrared_level, 50)

    def test_adding_with_another_face_is_refused(self):
        yaws = self.sweeps(1)
        with self.assertRaises(session.SessionError) as raised:
            self.enroll(yaws, FakeModels(yaws, owner=helpers.random_units(1, 42)[0]), existing=owner_template())
        self.assertEqual(raised.exception.args[0], "ENROLL_OWNER_MISMATCH")

    def test_enrollment_runs_out_of_time(self):
        yaws = [0.0] * 10 + [30.0] * 20 + [0.0] * 2000
        with self.assertRaises(session.SessionError) as raised:
            self.enroll(yaws, FakeModels(yaws))
        self.assertEqual(raised.exception.args[0], "ENROLL_TIME_UP")


if __name__ == "__main__":
    unittest.main()
