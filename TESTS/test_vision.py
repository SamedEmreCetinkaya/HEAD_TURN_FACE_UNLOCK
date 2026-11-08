import os
import tempfile
import unittest

import cv2
import numpy as np
from helpers import MODELS, models_available, view

from head_turn_face_unlock import geometry, vision
from head_turn_face_unlock.model_files import INSTALLED


class InfraredTests(unittest.TestCase):
    def setUp(self):
        self.points = view(0)
        self.lit = np.full((360, 640), 10, np.uint8)
        cv2.fillPoly(self.lit, [np.round(self.points[list(geometry.FACE_OVAL), :2]).astype(np.int32)], 90)

    def test_level_inside_the_face_outline(self):
        self.assertAlmostEqual(vision.infrared_level(self.lit, self.points), 90.0, delta=1.0)

    def test_an_outline_off_the_picture_gives_no_level(self):
        away = self.points.copy()
        away[:, 0] += 2000
        self.assertIsNone(vision.infrared_level(self.lit, away))

    def test_pairing_lit_and_unlit_frames(self):
        pairer = vision.InfraredPairer()
        dark = np.zeros_like(self.lit)
        self.assertIsNone(pairer.push(dark))
        lit, difference = pairer.push(self.lit)
        self.assertIs(lit, self.lit)
        self.assertEqual(int(difference.max()), 90)
        self.assertIsNone(pairer.push(dark), "a lit frame is used once, not again with the next unlit frame")
        self.assertIsNotNone(pairer.push(self.lit))
        self.assertIsNone(pairer.push(self.lit), "two lit frames in a row are not a pair")

    def test_sharpness(self):
        flat = np.full((360, 640, 3), 128, np.uint8)
        noisy = np.random.default_rng(1).integers(0, 255, (360, 640, 3), dtype=np.uint8)
        self.assertEqual(vision.sharpness(flat, 320, 180, 100), 0.0)
        self.assertGreater(vision.sharpness(noisy, 320, 180, 100), 100.0)
        self.assertGreater(vision.sharpness(noisy[:, :, 0], 320, 180, 100), 100.0)
        self.assertEqual(vision.sharpness(noisy, -500, -500, 10), 0.0)


class DecodeTests(unittest.TestCase):
    def test_half_size_decode(self):
        image = np.random.default_rng(2).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
        jpeg = cv2.imencode(".jpg", image)[1].tobytes()
        self.assertEqual(vision.decode_half(jpeg).shape, (360, 640, 3))
        self.assertEqual(vision.decode_full(jpeg).shape, (720, 1280, 3))
        self.assertIsNone(vision.decode_full(b"not a jpeg"))


class ModelTests(unittest.TestCase):
    def test_a_wrong_model_file_is_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            for name in INSTALLED:
                with open(os.path.join(directory, name), "wb") as handle:
                    handle.write(b"not the model")
            with self.assertRaises(vision.ModelError):
                vision.verify_models(directory)
            with self.assertRaises(vision.ModelError):
                vision.verify_models(os.path.join(directory, "absent"))

    @unittest.skipUnless(models_available(), "models not installed; set HEAD_TURN_FACE_UNLOCK_MODELS")
    def test_real_recognizer(self):
        recognizer = vision.Recognizer(MODELS, 2)
        image = np.random.default_rng(5).integers(0, 255, (720, 1280, 3), dtype=np.uint8)
        five = geometry.alignment_points(view(0)) * 2
        embedding = recognizer.embed(image, five)
        self.assertEqual(embedding.shape, (vision.EMBEDDING_SIZE,))
        self.assertAlmostEqual(float(np.linalg.norm(embedding)), 1.0, places=4)
        self.assertAlmostEqual(float(embedding @ recognizer.embed(image, five)), 1.0, places=5)


if __name__ == "__main__":
    unittest.main()
