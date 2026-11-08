import os
import tempfile
import unittest

import helpers  # noqa: F401  (adds SOURCE to the path)

from head_turn_face_unlock.config import ConfigError, Settings, load, parse


class ConfigTests(unittest.TestCase):
    def test_shipped_file_is_valid_and_equals_the_defaults(self):
        path = os.path.join(os.path.dirname(os.path.dirname(os.path.realpath(__file__))),
                            "CONFIG", "HEAD_TURN_FACE_UNLOCK.conf")
        with open(path, encoding="utf-8") as handle:
            self.assertEqual(parse(handle.read()), Settings())

    def test_values_and_comments(self):
        settings = parse("# comment\n\nLANGUAGE = tr  # Turkish\nMATCH_THRESHOLD=0.7\nINFRARED_CAMERA=/dev/video2\n")
        self.assertEqual((settings.LANGUAGE, settings.MATCH_THRESHOLD, settings.INFRARED_CAMERA),
                         ("tr", 0.7, "/dev/video2"))

    def test_mistakes_are_errors(self):
        for text in ("MATCH_TRESHOLD=0.6", "MATCH_THRESHOLD=0.6\nMATCH_THRESHOLD=0.7", "MATCH_THRESHOLD",
                     "MATCH_THRESHOLD=high", "MATCH_THRESHOLD=1.5", "MATCH_THRESHOLD=nan", "LANGUAGE=de",
                     "ATTEMPT_SECONDS=1", "COLOR_CAMERA=/etc/passwd", "COLOR_CAMERA=/dev/../etc",
                     "MATCH_FLOOR=0.7\nMATCH_THRESHOLD=0.6"):
            with self.subTest(text=text), self.assertRaises(ConfigError):
                parse(text)

    def test_a_missing_file_means_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(load(os.path.join(directory, "absent.conf")), Settings())


if __name__ == "__main__":
    unittest.main()
