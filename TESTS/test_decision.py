import math
import unittest
from dataclasses import replace

import numpy as np

import helpers
from helpers import REFERENCE, THRESHOLDS, TIMING, color_track, infrared_track

from head_turn_face_unlock import decision
from head_turn_face_unlock.decision import Reference, Turn, Verdict


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
    def test_a_clean_turn_of_a_real_head_is_accepted(self):
        verdict = decide(color_track())
        self.assertTrue(verdict.accepted, verdict)

    def test_without_a_face_nothing_is_decided_until_the_end(self):
        self.assertIsNone(decision.find_turn(color_track()[:3], [], TIMING, set(), final=False))
        self.assertEqual(decision.find_turn(color_track()[:3], [], TIMING, set(), final=True).reason, "NO_FACE")

    def test_missing_right_turn(self):
        self.assertEqual(decide(color_track([0.0] * 10 + [-3.0 * i for i in range(12)]), final=True).reason,
                         "RIGHT_TURN_MISSING")

    def test_missing_left_turn(self):
        yaws = [2.5 * i for i in range(12)] + [30 - 2.5 * i for i in range(10)]
        self.assertEqual(decide(color_track(yaws), final=True).reason, "LEFT_TURN_MISSING")

    def test_half_turns_do_not_count(self):
        to_left = list(np.linspace(0, 30, 10)) + list(np.linspace(30, -12, 15)) + [-12.0] * 10
        verdict = decide(color_track(to_left), final=True)
        self.assertEqual((verdict.accepted, verdict.reason), (False, "LEFT_TURN_MISSING"))
        to_right = list(np.linspace(0, 12, 10)) + list(np.linspace(12, -30, 15)) + [-30.0] * 10
        verdict = decide(color_track(to_right), final=True)
        self.assertEqual((verdict.accepted, verdict.reason), (False, "RIGHT_TURN_MISSING"))

    def test_left_to_right_is_not_accepted(self):
        self.assertFalse(decide(color_track([-y for y in helpers.turn_yaws()]), final=True).accepted)

    def test_a_jump_from_right_to_left_is_too_fast(self):
        self.assertEqual(decide(color_track([0.0, 10, 20, 25, -25, -30, -30])).reason, "TURN_TOO_FAST")

    def test_zigzag_is_not_one_way(self):
        yaws = [0.0, 15.0, 30.0]
        for step in range(40):
            yaws.append(yaws[-1] + (5.0 if step % 2 else -8.0))
        self.assertEqual(decide(color_track(yaws)).reason, "TURN_NOT_ONE_WAY")

    def test_a_frame_without_one_face_breaks_the_track(self):
        samples = color_track()
        verdict = decide(samples, gaps=[samples[20].timestamp + 0.01, samples[21].timestamp + 0.01])
        self.assertEqual(verdict.reason, "TRACK_BROKEN")
        self.assertIn(f"face not found in 2 frames, first between {samples[20].yaw:+.0f} and {samples[21].yaw:+.0f} deg",
                      verdict.details)

    def test_a_position_jump_breaks_the_track(self):
        samples = color_track()
        samples[20] = replace(samples[20], center_x=samples[20].center_x + 100)
        verdict = decide(samples)
        self.assertEqual(verdict.reason, "TRACK_BROKEN")
        before, after = samples[19], samples[20]
        moved = math.hypot(after.center_x - before.center_x, after.center_y - before.center_y) / before.width
        self.assertTrue(verdict.details.endswith(f", moved {moved:.2f} face widths in 1 frames at {samples[20].yaw:+.0f} deg"),
                        verdict.details)

    def test_a_size_jump_breaks_the_track(self):
        samples = color_track()
        samples[20] = replace(samples[20], width=samples[20].width * 1.6)
        verdict = decide(samples)
        self.assertEqual(verdict.reason, "TRACK_BROKEN")
        self.assertIn(f"size changed 1.60 times at {samples[20].yaw:+.0f} deg", verdict.details)

    def test_a_refusal_after_a_quick_turn_asks_for_a_slower_one(self):
        quick = color_track([0.0] * 10 + [10.0, 20, 30, 22, 14, 6, -2, -10, -18, -26, -30] + [-30.0] * 5)
        verdict = decide(quick, similarity=0.3)
        self.assertEqual((verdict.reason, verdict.hint), ("COLOR_MISMATCH", "TURN_SLOWER"))
        self.assertIn("0.23s", verdict.details)
        verdict = decide(quick, gaps=[quick[14].timestamp + 0.01])
        self.assertEqual((verdict.reason, verdict.hint), ("TRACK_BROKEN", "TURN_SLOWER"))

    def test_a_refusal_after_a_steady_turn_gives_no_advice(self):
        verdict = decide(color_track(), similarity=0.3)
        self.assertEqual((verdict.reason, verdict.hint), ("COLOR_MISMATCH", ""))
        self.assertEqual(decide(color_track()).hint, "")
        samples = color_track()
        self.assertEqual(decide(samples, gaps=[samples[20].timestamp + 0.01]).hint, "")

    def test_too_few_frames_always_ask_for_a_slower_turn(self):
        samples = color_track()
        verdict = decide(samples, infrared=infrared_track(samples)[:2])
        self.assertEqual((verdict.reason, verdict.hint), ("INFRARED_TOO_FEW", "TURN_SLOWER"))

    def test_a_long_pause_breaks_the_track(self):
        samples = color_track()
        samples = samples[:20] + [replace(s, timestamp=s.timestamp + 1.0) for s in samples[20:]]
        verdict = decide(samples)
        self.assertEqual(verdict.reason, "TRACK_BROKEN")
        self.assertIn(f"no frame for 1.03 s at {samples[20].yaw:+.0f} deg", verdict.details)

    def test_a_turn_is_judged_once(self):
        samples, evaluated = color_track(), set()
        self.assertIsInstance(decision.find_turn(samples, [], TIMING, evaluated, False), Turn)
        self.assertIsNone(decision.find_turn(samples, [], TIMING, evaluated, False))

    def test_identity_frames_are_the_sharpest_per_bin(self):
        samples = color_track()
        turn = decision.find_turn(samples, [], TIMING, set(), False)
        for angle, indexes in decision.identity_frames(samples, turn).items():
            in_bin = [i for i in range(turn.evidence_start, turn.end + 1) if decision.bin_of(samples[i].yaw) == angle]
            best = sorted(in_bin, key=lambda i: -samples[i].sharpness)[:len(indexes)]
            self.assertEqual(sorted(indexes), sorted(best))
            self.assertLessEqual(len(indexes), decision.IDENTITY_FRAMES_PER_BIN)


class LivenessTests(unittest.TestCase):
    def test_a_flat_picture_turned_like_a_head_is_rejected(self):
        samples = color_track(flat=True)
        verdict = decide(samples, reference=helpers.reference_for(samples))
        self.assertEqual(verdict.reason, "NOT_THREE_DIMENSIONAL")

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

    def test_a_dark_infrared_reflection_is_not_live(self):
        samples = color_track()
        self.assertEqual(decide(samples, infrared=infrared_track(samples, level=20.0)).reason, "INFRARED_NOT_LIVE")


class IdentityTests(unittest.TestCase):
    def test_another_person_is_rejected(self):
        self.assertEqual(decide(color_track(), similarity=0.25).reason, "COLOR_MISMATCH")

    def test_one_bin_below_the_floor_is_rejected(self):
        samples = color_track()
        weak = {i for i, s in enumerate(samples) if decision.bin_of(s.yaw) == 4}
        self.assertEqual(decide(samples, similarity=lambda i: 0.3 if i in weak else 0.9).reason, "COLOR_MISMATCH")

    def test_other_proportions_are_rejected(self):
        different = Reference(REFERENCE.infrared_level, REFERENCE.shape_median * 1.2, REFERENCE.shape_spread)
        self.assertEqual(decide(color_track(), reference=different).reason, "SHAPE_MISMATCH")

    def test_proportions_need_frontal_frames(self):
        yaws = [25.0] * 5 + list(np.linspace(30, -30, 6)) + [-25.0] * 5
        self.assertIn(decide(color_track(yaws)).reason, ("SHAPE_UNMEASURED", "TURN_TOO_FAST"))
        samples = color_track()
        tilted = [replace(s, pitch=40.0) for s in samples]
        self.assertEqual(decide(tilted).reason, "SHAPE_UNMEASURED")

    def test_not_a_number_is_rejected(self):
        samples = color_track()
        self.assertFalse(decide(samples, similarity=math.nan).accepted)
        self.assertFalse(decide(samples, infrared=infrared_track(samples, similarity=math.nan)).accepted)
        self.assertFalse(decide(samples, infrared=infrared_track(samples, level=math.nan)).accepted)
        broken = [replace(s, shape=np.full_like(s.shape, np.nan)) for s in samples]
        self.assertFalse(decide(broken).accepted)
        no_depth = [replace(s, rigid=np.zeros_like(s.rigid)) for s in samples]
        self.assertFalse(decide(no_depth).accepted)


class BinTests(unittest.TestCase):
    def test_edges(self):
        self.assertIsNone(decision.bin_of(-40.0))
        self.assertEqual(decision.bin_of(-39.0), 0)
        self.assertEqual(decision.bin_of(39.0), decision.BIN_COUNT - 1)
        self.assertIsNone(decision.bin_of(41.0))
        self.assertIsNone(decision.bin_of(math.nan))

    def test_verdicts_are_immutable(self):
        with self.assertRaises(Exception):
            Verdict(True, "ACCEPTED").accepted = False  # type: ignore[misc]


if __name__ == "__main__":
    unittest.main()
