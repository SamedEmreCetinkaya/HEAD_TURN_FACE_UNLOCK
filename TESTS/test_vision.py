import os
import tempfile
import unittest

import cv2
import numpy as np

import helpers  # noqa: F401

from head_turn_face_unlock import vision
from head_turn_face_unlock.model_files import FILES

MODEL_DIRECTORY = os.environ.get("HEAD_TURN_FACE_UNLOCK_MODELS", "/usr/local/lib/head-turn-face-unlock/models")


def face(nose_offset=0.0, x=100.0, y=100.0, w=100.0, h=120.0):
    """A face row: box, right eye, left eye, nose, right and left mouth corner, score."""
    return np.array([x, y, w, h, x + 30, y + 40, x + 70, y + 40, x + 50 + nose_offset, y + 65,
                     x + 35, y + 90, x + 65, y + 90, 0.95], np.float32)


class GeometryTests(unittest.TestCase):
    def test_yaw_index(self):
        self.assertAlmostEqual(vision.yaw_index(face()), 0.0, places=5)
        self.assertGreater(vision.yaw_index(face(10)), 0.0)
        self.assertLess(vision.yaw_index(face(-10)), 0.0)

    def test_full_scale_doubles_positions_only(self):
        scaled = vision.to_full_scale(face())
        self.assertEqual(scaled[0], 200.0)
        self.assertEqual(scaled[14], np.float32(0.95))


class InfraredTests(unittest.TestCase):
    def setUp(self):
        self.lit = np.zeros((360, 640), np.uint8)
        self.lit[100:220, 100:200] = 90
        self.lit[150:180, 140:160] = 120

    def test_profile_of_a_face(self):
        level, nose, centroid = vision.infrared_profile(self.lit, face())
        self.assertGreater(level, 80)
        self.assertGreater(nose, 1.0)
        self.assertTrue(0.3 < centroid < 0.7)

    def test_a_nose_outside_the_face_box_gives_no_profile_instead_of_nan(self):
        self.assertIsNone(vision.infrared_profile(self.lit, face(nose_offset=200)))
        self.assertIsNone(vision.infrared_profile(self.lit, face(nose_offset=-200)))

    def test_a_face_outside_the_picture_gives_no_profile(self):
        self.assertIsNone(vision.infrared_profile(self.lit, face(x=700)))
        self.assertIsNone(vision.infrared_profile(self.lit, face(w=10, h=10)))

    def test_pairing_lit_and_unlit_frames(self):
        pairer = vision.InfraredPairer()
        dark = np.zeros_like(self.lit)
        self.assertIsNone(pairer.push(dark))
        lit, difference = pairer.push(self.lit)
        self.assertIs(lit, self.lit)
        self.assertEqual(int(difference.max()), 120)
        self.assertIsNone(pairer.push(dark), "a lit frame is used once, not again with the next unlit frame")
        self.assertIsNotNone(pairer.push(self.lit))
        self.assertIsNone(pairer.push(self.lit), "two lit frames in a row are not a pair")

    def test_sharpness(self):
        flat = np.full((200, 200, 3), 128, np.uint8)
        noisy = np.random.default_rng(1).integers(0, 255, (200, 200, 3), dtype=np.uint8)
        self.assertEqual(vision.sharpness(flat, face(x=10, y=10)), 0.0)
        self.assertGreater(vision.sharpness(noisy, face(x=10, y=10)), 100.0)
        self.assertGreater(vision.sharpness(noisy[:, :, 0], face(x=10, y=10)), 100.0)


class DecodeTests(unittest.TestCase):
    def test_half_size_decode(self):
        image = np.random.default_rng(2).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
        jpeg = cv2.imencode(".jpg", image)[1].tobytes()
        self.assertEqual(vision.decode_for_detection(jpeg).shape, (360, 640, 3))
        self.assertEqual(vision.decode_full(jpeg).shape, (720, 1280, 3))
        self.assertIsNone(vision.decode_full(b"not a jpeg"))


class ModelTests(unittest.TestCase):
    def test_a_wrong_model_file_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in FILES:
                with open(os.path.join(directory, name), "wb") as handle:
                    handle.write(b"not the model")
            with self.assertRaises(vision.ModelError):
                vision.verify_models(directory)
            with self.assertRaises(vision.ModelError):
                vision.verify_models(os.path.join(directory, "absent"))

    @unittest.skipUnless(all(os.path.isfile(os.path.join(MODEL_DIRECTORY, name)) for name in FILES),
                         "models not installed; set HEAD_TURN_FACE_UNLOCK_MODELS")
    def test_real_models(self):
        models = vision.FaceModels(MODEL_DIRECTORY, 2)
        detector = models.new_detector()
        self.assertEqual(len(detector.detect(np.zeros((360, 640, 3), np.uint8))), 0)
        embedding = models.embed(np.full((720, 1280, 3), 100, np.uint8), vision.to_full_scale(face()))
        self.assertEqual(embedding.shape, (128,))
        self.assertAlmostEqual(float(np.linalg.norm(embedding)), 1.0, places=4)


if __name__ == "__main__":
    unittest.main()
