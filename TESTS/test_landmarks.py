import math
import os
import unittest

import cv2
import numpy as np
from helpers import MODELS, models_available, runtime_available

from head_turn_face_unlock import landmarks
from head_turn_face_unlock.landmarks import ANCHORS, DETECTOR_SIZE, Detection


def raw_for(anchor: int, centre, size, keypoints):
    """Detector output that decodes to the given box and keypoints (in letterboxed 0-1 units)."""
    row = np.zeros(16, np.float32)
    row[0:2] = (np.array(centre) - ANCHORS[anchor]) * DETECTOR_SIZE
    row[2:4] = np.array(size) * DETECTOR_SIZE
    row[4:16] = ((np.array(keypoints) - ANCHORS[anchor]) * DETECTOR_SIZE).ravel()
    return row


class DetectorDecodingTests(unittest.TestCase):
    def test_anchors_of_the_short_range_model(self):
        self.assertEqual(ANCHORS.shape, (896, 2))
        self.assertTrue(((ANCHORS > 0) & (ANCHORS < 1)).all())
        self.assertAlmostEqual(float(ANCHORS[0][0]), 0.5 / 16)
        self.assertAlmostEqual(float(ANCHORS[-1][0]), 7.5 / 8)

    def test_a_detection_is_placed_back_in_the_letterboxed_frame(self):
        boxes = np.zeros((896, 16), np.float32)
        logits = np.full(896, -20.0, np.float32)
        keypoints = [(0.45, 0.45), (0.55, 0.45), (0.5, 0.5), (0.5, 0.55), (0.4, 0.47), (0.6, 0.47)]
        boxes[300] = raw_for(300, (0.5, 0.5), (0.2, 0.25), keypoints)
        logits[300] = 5.0
        # A 640 x 360 frame is padded to 640 x 640 with 140 rows on top.
        found = landmarks.decode(boxes, logits, side=640, left=0, top=140)
        self.assertEqual(len(found), 1)
        np.testing.assert_allclose(found[0].box, [256, 100, 384, 260], atol=0.01)
        np.testing.assert_allclose(found[0].keypoints[0], [288, 148], atol=0.01)
        self.assertGreater(found[0].score, 0.99)

    def test_overlapping_detections_count_once_and_weak_ones_not_at_all(self):
        boxes = np.zeros((896, 16), np.float32)
        logits = np.full(896, -20.0, np.float32)
        keypoints = [(0.5, 0.5)] * 6
        boxes[300] = raw_for(300, (0.5, 0.5), (0.2, 0.2), keypoints)
        boxes[301] = raw_for(301, (0.51, 0.5), (0.2, 0.2), keypoints)
        boxes[600] = raw_for(600, (0.2, 0.2), (0.1, 0.1), keypoints)
        boxes[700] = raw_for(700, (0.8, 0.8), (0.1, 0.1), keypoints)
        logits[[300, 301, 600]] = (6.0, 5.0, 3.0)
        logits[700] = -1.0
        found = landmarks.decode(boxes, logits, 128, 0, 0)
        self.assertEqual(len(found), 2)
        self.assertGreater(found[0].score, found[1].score)

    def test_extreme_logits_do_not_overflow(self):
        with np.errstate(all="raise"):
            landmarks.decode(np.zeros((896, 16), np.float32), np.full(896, -1e6, np.float32), 128, 0, 0)

    def test_letterbox_pads_to_a_square(self):
        tensor, side, left, top = landmarks.letterbox(np.full((360, 640, 3), 255, np.uint8))
        self.assertEqual((tensor.shape, side, left, top), ((128, 128, 3), 640, 0, 140))
        self.assertAlmostEqual(float(tensor[0, 64, 0]), -1.0)
        self.assertAlmostEqual(float(tensor[64, 64, 0]), 1.0)


class CropTests(unittest.TestCase):
    def detection(self, angle):
        centre, half = np.array([300.0, 200.0]), 30.0
        direction = np.array([math.cos(math.radians(angle)), math.sin(math.radians(angle))])
        eyes = [centre - half * direction, centre + half * direction]
        return Detection(0.9, np.array([250.0, 150.0, 350.0, 260.0]),
                         np.array(eyes + [centre, centre, centre, centre]))

    def test_the_crop_levels_the_eyes_and_centres_the_face(self):
        for angle in (-25, 0, 18):
            detection = self.detection(angle)
            matrix, side = landmarks.crop_transform(detection)
            eyes = detection.keypoints[:2] @ matrix[:, :2].T + matrix[:, 2]
            self.assertAlmostEqual(eyes[0][1], eyes[1][1], places=3)
            self.assertLess(eyes[0][0], eyes[1][0])
            self.assertAlmostEqual(side, 110 * landmarks.ROI_SCALE)
            centre = np.array([300.0, 205.0]) @ matrix[:, :2].T + matrix[:, 2]
            np.testing.assert_allclose(centre, [128, 128], atol=0.01)

    def test_points_go_back_to_the_image(self):
        matrix, side = landmarks.crop_transform(self.detection(12))
        image_points = np.array([[280.0, 190.0], [330.0, 240.0]])
        local = np.c_[image_points @ matrix[:, :2].T + matrix[:, 2], [5.0, -5.0]]
        back = landmarks.to_image(local, matrix, side)
        np.testing.assert_allclose(back[:, :2], image_points, atol=1e-3)
        self.assertAlmostEqual(float(back[0, 2]), 5.0 * side / landmarks.MESH_SIZE, places=4)


@unittest.skipUnless(runtime_available() and models_available(), "LiteRT or the models are not installed")
class RealModelTests(unittest.TestCase):
    def test_no_face_in_an_empty_frame(self):
        detector, mesh = landmarks.model_paths(MODELS)
        finder = landmarks.FaceFinder(detector, mesh, 2)
        self.assertEqual(finder.find(np.zeros((360, 640, 3), np.uint8)), (0, None))
        noise = np.random.default_rng(4).integers(0, 255, (360, 640, 3), dtype=np.uint8)
        count, face = finder.find(cv2.GaussianBlur(noise, (9, 9), 3))
        self.assertTrue(count == 0 or face is None or face.points.shape == (478, 3))
        self.assertTrue(os.path.isfile(mesh))


if __name__ == "__main__":
    unittest.main()
