import math
import unittest

import numpy as np
from helpers import view

from head_turn_face_unlock import geometry


class PoseTests(unittest.TestCase):
    def test_yaw_pitch_and_roll_in_degrees(self):
        for yaw in (-35, -20, 0, 20, 35):
            self.assertAlmostEqual(geometry.pose(view(yaw))[0], yaw, delta=2.0)
        for pitch in (-15, 15):
            self.assertAlmostEqual(geometry.pose(view(0, pitch))[1], pitch, delta=2.0)
        tilted = view(0)
        centre = tilted[:, :2].mean(axis=0)
        angle = math.radians(10)
        rotation = np.array([[math.cos(angle), -math.sin(angle)], [math.sin(angle), math.cos(angle)]])
        tilted[:, :2] = (tilted[:, :2] - centre) @ rotation.T + centre
        self.assertAlmostEqual(geometry.pose(tilted)[2], 10.0, delta=1.0)

    def test_pose_ignores_size_and_position(self):
        self.assertAlmostEqual(geometry.pose(view(20, scale=0.5, shift=200))[0], geometry.pose(view(20))[0], delta=1.0)


class ParallaxTests(unittest.TestCase):
    def measure(self, first, second):
        return geometry.parallax(geometry.rigid_points(first), geometry.rigid_points(second),
                                 geometry.eye_distance(first), geometry.eye_distance(second))

    def test_a_turning_head_is_not_flat(self):
        self.assertGreater(self.measure(view(25), view(-25)), 0.3)

    def test_a_turned_picture_is_flat(self):
        self.assertLess(self.measure(view(25, flat=True), view(-25, flat=True)), 0.01)

    def test_a_picture_moved_and_resized_is_still_flat(self):
        self.assertLess(self.measure(view(0, flat=True), view(20, flat=True, scale=0.8, shift=50)), 0.01)

    def test_noise_like_the_measured_landmark_jitter_stays_far_below_the_limit(self):
        generator = np.random.default_rng(3)
        first, second = view(20, flat=True), view(-20, flat=True)
        jitter = 0.011 * geometry.eye_distance(first) / math.sqrt(2)
        first[:, :2] += generator.normal(0, jitter, first[:, :2].shape)
        second[:, :2] += generator.normal(0, jitter, second[:, :2].shape)
        self.assertLess(self.measure(first, second), 0.03)

    def test_degenerate_or_broken_input_gives_zero(self):
        empty = np.zeros((geometry.POINT_COUNT, 3), np.float32)
        self.assertEqual(self.measure(empty, empty), 0.0)
        broken = view(25)
        broken[geometry.NOSE_TIP] = np.nan
        self.assertEqual(self.measure(broken, view(-25)), 0.0)


class PointTests(unittest.TestCase):
    def test_alignment_points_follow_the_recognizer_template_order(self):
        for yaw in (-30, 0, 30):
            five = geometry.alignment_points(view(yaw))
            self.assertLess(five[0][0], five[1][0])
            self.assertLess(five[3][0], five[4][0])
            self.assertLess(five[0][1], five[2][1])
            self.assertLess(five[2][1], five[3][1])

    def test_alignment_order_holds_for_a_mirroring_camera(self):
        mirrored = view(10)
        mirrored[:, 0] = 640 - mirrored[:, 0]
        five = geometry.alignment_points(mirrored)
        self.assertLess(five[0][0], five[1][0])
        self.assertLess(five[3][0], five[4][0])

    def test_shape_does_not_depend_on_size_or_position_in_the_picture(self):
        points = view(0)
        moved = points.copy()
        moved[:, :2] = moved[:, :2] * 0.6 + (120, -40)
        np.testing.assert_allclose(geometry.shape(moved), geometry.shape(points), rtol=1e-5)
        self.assertEqual(len(geometry.shape(view(0))), len(geometry.SHAPE_NAMES))

    def test_shape_of_nothing_is_not_a_number(self):
        self.assertTrue(np.isnan(geometry.shape(np.zeros((geometry.POINT_COUNT, 3)))).all())

    def test_face_box_and_mask(self):
        points = view(0)
        centre_x, centre_y, width = geometry.face_box(points)
        self.assertAlmostEqual(centre_x, 320, delta=2)
        mask = geometry.face_mask(points, (360, 640))
        self.assertEqual(mask[int(centre_y), int(centre_x)], 1)
        self.assertEqual(mask[0, 0], 0)
        self.assertAlmostEqual(width, mask[int(centre_y)].sum(), delta=4)


if __name__ == "__main__":
    unittest.main()
