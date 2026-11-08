"""Whole verification and enrollment sessions with scripted cameras and models.

Frames carry their index; the fake detector looks the face up in a script, so the
real capture loop, pairing, embedding requests and decision run unchanged on a
fake clock without a camera.
"""

import unittest
from unittest import mock

import numpy as np

import helpers
from helpers import REFERENCE, unit

from head_turn_face_unlock import session, vision
from head_turn_face_unlock.cameras import CameraPair
from head_turn_face_unlock.config import Settings
from head_turn_face_unlock.decision import BIN_COUNT
from head_turn_face_unlock.template import Template, empty_bank
from head_turn_face_unlock.v4l2_capture import Frame, Mode

COLOR_MODE, INFRARED_MODE = Mode("MJPG", 1280, 720, 30.0), Mode("GREY", 640, 360, 15.0)
PAIR = CameraPair("/dev/color", COLOR_MODE, "/dev/infrared", INFRARED_MODE)
OWNER, OWNER_INFRARED = helpers.random_units(1, 7)[0], helpers.random_units(1, 8)[0]


def face_for(yaw: float) -> np.ndarray:
    """Landmarks whose yaw index equals yaw (eyes 40 apart, eyes to mouth 50)."""
    nose_x = 50 + yaw * (0.5 * 40 + 0.5 * 50)
    return np.array([100, 60, 100, 120, 130, 100, 170, 100, 100 + nose_x, 125, 135, 150, 165, 150, 0.95], np.float32)


class Script:
    """Color frames at 30 fps following yaws; infrared frames at 15 fps, every other one lit,
    with the head angle written into the lit pixels for the fake infrared profile."""

    def __init__(self, yaws, start=50.0):
        self.yaws = yaws
        self.now = start
        self.events = []
        for index, yaw in enumerate(yaws):
            self.events.append((start + index / 30, "color", index))
        for index in range(int(len(yaws) / 2)):
            moment = start + index / 15 + 0.012
            yaw = yaws[min(len(yaws) - 1, int(round((moment - start) * 30)))]
            level = 0 if index % 2 else int(60 + 100 * (yaw + 0.6))
            self.events.append((moment, "infrared", level))
        self.events.sort()
        self.closed_at = None
        self.log = []

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
        moment = due[0][0]
        script.now = max(script.now, moment)
        ready = [event for event in script.events if event[0] <= script.now]
        script.events = [event for event in script.events if event[0] > script.now]
        frames = []
        for stamp, kind, value in ready:
            if kind == "color":
                frames.append((self.color, Frame(value, stamp, value.to_bytes(4, "little"), COLOR_MODE, 1280)))
            else:
                frames.append((self.infrared, Frame(value, stamp, bytes([value]) * (640 * 360), INFRARED_MODE, 640)))
        return frames


class FakeDetector:
    def __init__(self, yaws):
        self.yaws = yaws

    def detect(self, image):
        if image.shape == (1, 1):  # a color frame marker: [[index]]
            index = int(image[0, 0])
            yaw = self.yaws[index]
            return np.zeros((0, 15), np.float32) if yaw is None else face_for(yaw)[None, :]
        return face_for(0.0)[None, :] if image.mean() > 0 else np.zeros((0, 15), np.float32)


class FakeModels:
    def __init__(self, yaws, owner=OWNER, owner_infrared=OWNER_INFRARED, noise=0.0):
        self.yaws, self.owner, self.owner_infrared = yaws, owner, owner_infrared
        self.random = np.random.default_rng(3)
        self.noise = noise

    def new_detector(self):
        return FakeDetector(self.yaws)

    def embed(self, image, face):
        base = self.owner if image.shape == (2, 2, 3) else self.owner_infrared
        return unit(base + self.noise * self.random.normal(size=base.shape))


class SyncWorker(session._Worker):
    def start(self):
        pass

    def stop(self):
        pass

    def submit(self, job):
        self.results.put(self._color(job) if job[0] == "color" else self._infrared(job))


def fake_profile(difference, face):
    yaw = (float(difference[0, 0]) - 60) / 100 - 0.6
    return 90.0, 1.2, 0.5 + 0.05 * yaw


def owner_template():
    color, infrared = empty_bank(), empty_bank()
    for angle in range(BIN_COUNT):
        color[angle] = OWNER[None, :]
        infrared[angle] = OWNER_INFRARED[None, :]
    return Template(1, color, infrared, REFERENCE, "2026-01-31 12:00", [])


class SessionTestCase(unittest.TestCase):
    def use(self, script):
        FakeStreams.script = script
        patches = [mock.patch.object(session, "_Streams", FakeStreams),
                   mock.patch.object(session, "_Worker", SyncWorker),
                   mock.patch.object(vision, "decode_for_detection", lambda data: np.array([[int.from_bytes(data, "little")]])),
                   mock.patch.object(vision, "decode_full", lambda data: np.ones((2, 2, 3), np.uint8)),
                   mock.patch.object(vision, "infrared_profile", fake_profile)]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)


class VerificationTests(SessionTestCase):
    def verify(self, yaws, models=None, cancelled=lambda: False, settings=Settings()):
        script = Script(yaws)
        self.use(script)
        said, delivered = [], []
        verification = session.Verification(models or FakeModels(yaws), PAIR, owner_template(), settings,
                                            said.append, cancelled=cancelled, clock=script.clock)

        def deliver(verdict):
            script.log.append("delivered")
            delivered.append(verdict)
        verdict = verification.run(deliver)
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

    def test_a_blind_infrared_camera_is_reported(self):
        yaws = helpers.turn_yaws() + [0.0] * 60
        models = FakeModels(yaws)
        detector = models.new_detector
        models.new_detector = lambda: type("Blind", (), {"detect": lambda self, image: (
            detector().detect(image) if image.shape == (1, 1) else np.zeros((0, 15), np.float32))})()
        verdict, said, _ = self.verify(yaws, models=models)
        self.assertFalse(verdict.accepted)
        self.assertIn("INFRARED_CANNOT_SEE", said)

    def test_cancelling_stops_the_session(self):
        with self.assertRaises(session.SessionError) as raised:
            self.verify([0.0] * 200, cancelled=lambda: True)
        self.assertEqual(raised.exception.args[0], "CANCELLED")

    def test_cameras_that_cannot_open_are_reported(self):
        pair = CameraPair("/nonexistent/video0", COLOR_MODE, "/nonexistent/video2", INFRARED_MODE)
        with self.assertRaises(session.SessionError) as raised:
            with session._Streams(pair):
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
        yaws = [0.0] * 10 + list(np.linspace(0, 0.45, 15))
        for _ in range(count):
            yaws += list(np.linspace(0.45, -0.45, 45)) + list(np.linspace(-0.45, 0.45, 45))
        return yaws

    def test_enrollment_collects_every_required_angle(self):
        yaws = self.sweeps()
        template, said, progress = self.enroll(yaws, FakeModels(yaws, noise=0.02))
        self.assertEqual(said[:3], ["ENROLL_LOOK_STRAIGHT", "ENROLL_TURN_RIGHT", "ENROLL_SWEEP"])
        self.assertEqual(progress[-1], [100] * len(session.REQUIRED_BINS))
        color, infrared = template.counts()
        for angle in session.REQUIRED_BINS:
            self.assertGreaterEqual(color[angle], session.COLOR_TARGET_PER_BIN)
            self.assertGreaterEqual(infrared[angle], session.INFRARED_TARGET_PER_BIN)
        self.assertGreater(template.reference.centroid_slope, 0)

    def test_adding_with_another_face_is_refused(self):
        yaws = self.sweeps(1)
        with self.assertRaises(session.SessionError) as raised:
            self.enroll(yaws, FakeModels(yaws, owner=helpers.random_units(1, 42)[0]), existing=owner_template())
        self.assertEqual(raised.exception.args[0], "ENROLL_OWNER_MISMATCH")

    def test_enrollment_runs_out_of_time(self):
        with self.assertRaises(session.SessionError) as raised:
            self.enroll([0.0] * 10 + [0.5] * 20 + [0.0] * 2000, FakeModels([0.0] * 10 + [0.5] * 20 + [0.0] * 2000))
        self.assertEqual(raised.exception.args[0], "ENROLL_TIME_UP")


if __name__ == "__main__":
    unittest.main()
