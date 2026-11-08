import math
import unittest
from dataclasses import replace

import helpers
from helpers import REFERENCE, THRESHOLDS, TIMING, color_track, infrared_track

from head_turn_face_unlock import decision
from head_turn_face_unlock.decision import Turn, Verdict


def decide(samples, gaps=(), infrared=None, similarity=0.8, reference=REFERENCE, final=False):
    """Runs the whole decision the way a session does and returns the verdict."""
    found = decision.find_turn(samples, list(gaps), TIMING, set(), final=final)
    if not isinstance(found, Turn):
        return found
    chosen = decision.identity_frames(samples, found)
    score = similarity if callable(similarity) else (lambda index: similarity)
    infrared = infrared_track(samples) if infrared is None else infrared
    return decision.judge(samples, found, chosen, score, infrared, reference, THRESHOLDS, TIMING)


class TurnTests(unittest.TestCase):
    def test_a_clean_turn_is_accepted(self):
        verdict = decide(color_track())
        self.assertTrue(verdict.accepted, verdict)

    def test_without_a_face_nothing_is_decided_until_the_end(self):
        self.assertIsNone(decision.find_turn(color_track()[:3], [], TIMING, set(), final=False))
        self.assertEqual(decision.find_turn(color_track()[:3], [], TIMING, set(), final=True).reason, "NO_FACE")

    def test_missing_right_turn(self):
        verdict = decide(color_track([0.0] * 10 + [-0.1 * i for i in range(8)]), final=True)
        self.assertEqual(verdict.reason, "RIGHT_TURN_MISSING")

    def test_missing_left_turn(self):
        verdict = decide(color_track([0.05 * i for i in range(10)] + [0.45 - 0.05 * i for i in range(8)]), final=True)
        self.assertEqual(verdict.reason, "LEFT_TURN_MISSING")

    def test_left_to_right_is_not_accepted(self):
        verdict = decide(color_track([-y for y in helpers.turn_yaws()]), final=True)
        self.assertFalse(verdict.accepted)

    def test_a_jump_from_right_to_left_is_too_fast(self):
        verdict = decide(color_track([0.0, 0.2, 0.4, 0.45, -0.4, -0.45, -0.45]))
        self.assertEqual(verdict.reason, "TURN_TOO_FAST")

    def test_zigzag_is_not_one_way(self):
        yaws = [0.45]
        for step in range(60):
            yaws.append(yaws[-1] + (0.09 if step % 2 else -0.12))
        verdict = decide(color_track([0.0, 0.2] + yaws))
        self.assertEqual(verdict.reason, "TURN_NOT_ONE_WAY")

    def test_a_frame_without_the_face_breaks_the_track(self):
        samples = color_track()
        verdict = decide(samples, gaps=[samples[20].timestamp + 0.01])
        self.assertEqual(verdict.reason, "TRACK_BROKEN")

    def test_a_position_jump_breaks_the_track(self):
        samples = color_track()
        samples[20] = replace(samples[20], center_x=samples[20].center_x + 100)
        self.assertEqual(decide(samples).reason, "TRACK_BROKEN")

    def test_a_size_jump_breaks_the_track(self):
        samples = color_track()
        samples[20] = replace(samples[20], width=samples[20].width * 1.6)
        self.assertEqual(decide(samples).reason, "TRACK_BROKEN")

    def test_a_long_pause_breaks_the_track(self):
        samples = color_track()
        samples = samples[:20] + [replace(s, timestamp=s.timestamp + 1.0) for s in samples[20:]]
        self.assertEqual(decide(samples).reason, "TRACK_BROKEN")

    def test_a_turn_is_judged_once(self):
        samples, evaluated = color_track(), set()
        self.assertIsInstance(decision.find_turn(samples, [], TIMING, evaluated, False), Turn)
        self.assertIsNone(decision.find_turn(samples, [], TIMING, evaluated, False))

    def test_identity_frames_are_the_sharpest_per_bin(self):
        samples = color_track()
        turn = decision.find_turn(samples, [], TIMING, set(), False)
        chosen = decision.identity_frames(samples, turn)
        for angle, indexes in chosen.items():
            self.assertLessEqual(len(indexes), decision.IDENTITY_FRAMES_PER_BIN)
            in_bin = [i for i in range(turn.evidence_start, turn.end + 1) if decision.bin_of(samples[i].yaw) == angle]
            best = sorted(in_bin, key=lambda i: -samples[i].sharpness)[:len(indexes)]
            self.assertEqual(sorted(indexes), sorted(best))


class IdentityTests(unittest.TestCase):
    def test_another_person_is_rejected(self):
        self.assertEqual(decide(color_track(), similarity=0.25).reason, "COLOR_MISMATCH")

    def test_one_bin_below_the_floor_is_rejected(self):
        samples = color_track()
        weak = {i for i, s in enumerate(samples) if decision.bin_of(s.yaw) == 4}
        self.assertEqual(decide(samples, similarity=lambda i: 0.3 if i in weak else 0.9).reason, "COLOR_MISMATCH")

    def test_not_a_number_is_rejected(self):
        self.assertFalse(decide(color_track(), similarity=math.nan).accepted)

    def test_not_a_number_in_infrared_is_rejected(self):
        samples = color_track()
        self.assertFalse(decide(samples, infrared=infrared_track(samples, nose_ratio=math.nan)).accepted)
        self.assertFalse(decide(samples, infrared=infrared_track(samples, similarity=math.nan)).accepted)


class InfraredTests(unittest.TestCase):
    def test_too_few_infrared_frames(self):
        samples = color_track()
        self.assertEqual(decide(samples, infrared=infrared_track(samples)[:2]).reason, "INFRARED_TOO_FEW")

    def test_infrared_frames_outside_the_turn_do_not_count(self):
        samples = color_track()
        late = [replace(s, timestamp=s.timestamp + 10) for s in infrared_track(samples)]
        self.assertEqual(decide(samples, infrared=late).reason, "INFRARED_TOO_FEW")

    def test_infrared_mismatch(self):
        samples = color_track()
        self.assertEqual(decide(samples, infrared=infrared_track(samples, similarity=0.5)).reason, "INFRARED_MISMATCH")

    def test_a_dark_reflection_is_not_live(self):
        samples = color_track()
        verdict = decide(samples, infrared=infrared_track(samples, face_level=20.0))
        self.assertEqual(verdict.reason, "INFRARED_NOT_LIVE")

    def test_a_flat_nose_is_not_live(self):
        samples = color_track()
        verdict = decide(samples, infrared=infrared_track(samples, nose_ratio=0.8))
        self.assertEqual(verdict.reason, "INFRARED_NOT_LIVE")

    def test_a_flat_picture_fails_the_depth_cue(self):
        samples = color_track()
        self.assertEqual(decide(samples, infrared=infrared_track(samples, slope=0.0)).reason, "DEPTH_CUE_FAILED")
        self.assertEqual(decide(samples, infrared=infrared_track(samples, slope=-0.05)).reason, "DEPTH_CUE_FAILED")


class BinTests(unittest.TestCase):
    def test_edges(self):
        self.assertEqual(decision.bin_of(-0.6), None)
        self.assertEqual(decision.bin_of(-0.59), 0)
        self.assertEqual(decision.bin_of(0.59), decision.BIN_COUNT - 1)
        self.assertIsNone(decision.bin_of(0.61))
        self.assertIsNone(decision.bin_of(math.nan))

    def test_verdicts_are_immutable(self):
        with self.assertRaises(Exception):
            Verdict(True, "ACCEPTED").accepted = False  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
