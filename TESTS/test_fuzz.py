"""Bad input: every parser and the decision get thousands of mutated or random inputs.

They may refuse with their own error and nothing else; nothing broken may be accepted. The
random generators are seeded, so a failure repeats exactly.
"""

import dataclasses
import json
import math
import random
import unittest

import numpy as np

import helpers
from helpers import REFERENCE, THRESHOLDS, TIMING, color_track, infrared_track

from head_turn_face_unlock import config, decision, geometry, landmarks, pam_config, protocol, template

ROUNDS = 1500


def mutate(data: bytes, generator: random.Random) -> bytes:
    data = bytearray(data)
    for _ in range(generator.randint(1, 4)):
        choice = generator.random()
        position = generator.randrange(len(data) + 1)
        if choice < 0.4 and data:
            data[min(position, len(data) - 1)] = generator.randrange(256)
        elif choice < 0.7:
            data[position:position] = bytes(generator.randrange(256) for _ in range(generator.randint(1, 8)))
        else:
            del data[position:position + generator.randint(1, 16)]
    return bytes(data)


class ParserTests(unittest.TestCase):
    def test_requests(self):
        generator = random.Random(1)
        seeds = [b'{"request":"VERIFY","user":"alice","service":"sudo"}', b'{"request":"STATUS"}',
                 b'{"request":"ENROLL","user":"alice","mode":"ADD"}']
        for _ in range(ROUNDS):
            line = mutate(generator.choice(seeds), generator)
            try:
                request = protocol.parse_request(line)
            except protocol.ProtocolError:
                continue
            self.assertIn(request.kind, protocol.REQUESTS)
            if request.kind != "STATUS":
                self.assertTrue(protocol.valid_user(request.user))

    def test_templates(self):
        generator = random.Random(2)
        valid = template.to_json(helpers.sample_template(per_bin=2)).encode()
        accepted = 0
        for _ in range(ROUNDS // 3):
            text = mutate(valid, generator)
            try:
                parsed = template.from_json(text)
            except template.TemplateError:
                continue
            accepted += 1
            for bank in parsed.color + parsed.infrared:
                self.assertTrue(np.isfinite(bank).all())
                np.testing.assert_allclose(np.linalg.norm(bank, axis=1), 1.0, atol=1e-5)
            self.assertTrue(math.isfinite(parsed.reference.infrared_level))
        self.assertLess(accepted, ROUNDS // 30, "random damage should almost never leave a valid template")

    def test_templates_from_random_json(self):
        generator = random.Random(3)
        values = [None, True, 0, -1, 1.5, "", "x" * 300, [], {}, [1.0] * 7, ["AAAA"] * 8]
        keys = sorted(template._KEYS)
        for _ in range(ROUNDS):
            data = {key: generator.choice(values) for key in keys if generator.random() < 0.9}
            with self.assertRaises(template.TemplateError):
                template.from_json(json.dumps(data))

    def test_settings(self):
        generator = random.Random(4)
        lines = ["LANGUAGE=tr", "MATCH_THRESHOLD=0.7", "ATTEMPT_SECONDS=8", "COLOR_CAMERA=/dev/video0", "# note", ""]
        for _ in range(ROUNDS):
            text = "\n".join(generator.choice(lines) for _ in range(generator.randint(0, 6)))
            text = mutate(text.encode(), generator).decode("utf-8", "replace")
            try:
                settings = config.parse(text)
            except config.ConfigError:
                continue
            self.assertIn(settings.LANGUAGE, config.LANGUAGES)
            self.assertLessEqual(settings.MATCH_FLOOR, settings.MATCH_THRESHOLD)

    def test_pam_files(self):
        generator = random.Random(5)
        pieces = ["@include common-auth", "auth include system-auth", "auth required pam_unix.so", "# comment",
                  "session required pam_limits.so", pam_config.LINE, pam_config.MARKER, "", "auth substack x"]
        for _ in range(ROUNDS):
            text = "\n".join(generator.choice(pieces) for _ in range(generator.randint(0, 8))) + "\n"
            try:
                added = pam_config.add(text)
            except pam_config.PamError:
                self.assertNotIn("@include common-auth", pam_config.remove(text))
                continue
            self.assertTrue(pam_config.present(added))
            self.assertEqual(added.count(pam_config.LINE), 1)
            self.assertEqual(pam_config.remove(added), pam_config.remove(text))


class NumericTests(unittest.TestCase):
    def test_detector_output(self):
        generator = np.random.default_rng(6)
        for _ in range(200):
            boxes = generator.normal(0, 200, (896, 16)).astype(np.float32)
            logits = generator.normal(0, 50, 896).astype(np.float32)
            logits[generator.integers(0, 896, 5)] = generator.choice([np.inf, -np.inf, np.nan, 1e30], 5)
            with np.errstate(all="ignore"):
                for found in landmarks.decode(boxes, logits, 640, 0, 140):
                    self.assertTrue(landmarks.DETECTION_SCORE <= found.score <= 1.0)

    def test_geometry_of_random_points(self):
        generator = np.random.default_rng(7)
        for _ in range(300):
            points = generator.normal(0, 100, (geometry.POINT_COUNT, 3)).astype(np.float32)
            if generator.random() < 0.2:
                points[generator.integers(0, geometry.POINT_COUNT)] = np.nan
            with np.errstate(all="ignore"):
                geometry.pose(points)
                geometry.shape(points)
                value = geometry.parallax(geometry.rigid_points(points), geometry.rigid_points(points[::-1].copy()),
                                          geometry.eye_distance(points), 50.0)
            self.assertTrue(value == 0.0 or math.isfinite(value))

    def test_broken_frames_never_raise_and_never_count(self):
        generator = np.random.default_rng(8)
        good = color_track()
        infrared = infrared_track(good)
        turns = 0
        for _ in range(200):
            samples = list(good)
            broken = {int(index) for index in generator.integers(0, len(samples), generator.integers(1, 6))}
            for index in broken:
                samples[index] = _broken(samples[index], generator)
            with np.errstate(all="ignore"):
                turn = decision.find_turn(samples, [], TIMING, set(), final=True)
                if not isinstance(turn, decision.Turn):
                    self.assertIsInstance(turn, decision.Verdict)
                    self.assertFalse(turn.accepted)
                    continue
                turns += 1
                chosen = decision.identity_frames(samples, turn)
                verdicts = [decision.judge(samples, turn, chosen, lambda index: unmeasured if index in broken else 0.9,
                                           infrared, REFERENCE, THRESHOLDS, TIMING)
                            for unmeasured in (math.nan, math.inf, -1.0)]
            self.assertEqual(len({(verdict.accepted, verdict.reason) for verdict in verdicts}), 1,
                             "an unmeasured similarity must count as no match")
        self.assertGreater(turns, 20, "too few rounds reached the judge")

    def test_unmeasurable_evidence_is_refused(self):
        good = color_track()
        infrared = infrared_track(good)
        cases = {
            "COLOR_MISMATCH": (good, math.nan, infrared),
            "NOT_THREE_DIMENSIONAL": ([dataclasses.replace(s, rigid=np.full_like(s.rigid, np.nan)) for s in good],
                                      0.9, infrared),
            "SHAPE_MISMATCH": ([dataclasses.replace(s, shape=np.full_like(s.shape, np.nan)) for s in good], 0.9, infrared),
            "INFRARED_MISMATCH": (good, 0.9, [dataclasses.replace(s, similarity=math.nan) for s in infrared]),
            "INFRARED_NOT_LIVE": (good, 0.9, [dataclasses.replace(s, level=math.nan) for s in infrared]),
        }
        for reason, (samples, similarity, lit) in cases.items():
            turn = decision.find_turn(samples, [], TIMING, set(), final=True)
            self.assertIsInstance(turn, decision.Turn, reason)
            with np.errstate(all="ignore"):
                verdict = decision.judge(samples, turn, decision.identity_frames(samples, turn),
                                         lambda index: similarity, lit, REFERENCE, THRESHOLDS, TIMING)
            self.assertEqual((verdict.accepted, verdict.reason), (False, reason))


def _broken(sample: decision.ColorSample, generator: np.random.Generator) -> decision.ColorSample:
    """One measurement of the frame replaced by NaN, an infinity or an absurd value."""
    value = float(generator.choice([np.nan, np.inf, -np.inf, 1e9, -1e9]))
    field = str(generator.choice(["yaw", "pitch", "center_x", "width", "sharpness", "eye_distance", "rigid", "shape"]))
    if field in ("rigid", "shape"):
        array = getattr(sample, field).copy()
        array.flat[generator.integers(0, array.size)] = value
        return dataclasses.replace(sample, **{field: array})
    return dataclasses.replace(sample, **{field: value})


if __name__ == "__main__":
    unittest.main()
