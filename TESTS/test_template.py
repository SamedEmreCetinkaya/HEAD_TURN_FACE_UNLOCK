import base64
import json
import unittest

import numpy as np
from helpers import random_units, sample_template, unit

from head_turn_face_unlock import template as templates
from head_turn_face_unlock.template import TemplateError, from_json, merge, prune, to_json


def edited(change):
    data = json.loads(to_json(sample_template()))
    change(data)
    return json.dumps(data)


class RoundTripTests(unittest.TestCase):
    def test_round_trip_keeps_vectors_within_float16_rounding(self):
        original = sample_template()
        restored = from_json(to_json(original))
        for before, after in zip(original.color + original.infrared, restored.color + restored.infrared):
            self.assertEqual(before.shape, after.shape)
            self.assertGreater(float(np.min(np.sum(before * after, axis=1))), 0.999)
        np.testing.assert_allclose(restored.reference.shape_median, original.reference.shape_median)
        np.testing.assert_allclose(restored.reference.shape_spread, original.reference.shape_spread)
        self.assertEqual(restored.reference.infrared_level, original.reference.infrared_level)

    def test_a_full_template_fits_a_system_credential(self):
        full = sample_template(per_bin=templates.COLOR_LIMIT_PER_BIN)
        full.infrared = [random_units(templates.INFRARED_LIMIT_PER_BIN, 300 + i) for i in range(len(full.infrared))]
        self.assertLess(len(to_json(full)), templates.MAXIMUM_TEXT_BYTES)


class StrictReadingTests(unittest.TestCase):
    def assertRefused(self, text):
        with self.assertRaises(TemplateError):
            from_json(text)

    def test_unknown_or_missing_fields(self):
        self.assertRefused(edited(lambda d: d.update(extra=1)))
        self.assertRefused(edited(lambda d: d.pop("created")))

    def test_wrong_format_version_or_direction(self):
        self.assertRefused(edited(lambda d: d.update(format="OTHER")))
        self.assertRefused(edited(lambda d: d.update(version=1)))
        self.assertRefused(edited(lambda d: d.update(direction=True)))
        self.assertRefused(edited(lambda d: d.update(direction=0)))

    def test_wrong_bins_or_encoding(self):
        self.assertRefused(edited(lambda d: d.update(color=d["color"][:-1])))
        self.assertRefused(edited(lambda d: d["color"].__setitem__(0, "not base64!")))
        self.assertRefused(edited(lambda d: d["color"].__setitem__(0, base64.b64encode(b"\0" * 10).decode())))

    def test_too_many_samples(self):
        rows = random_units(templates.COLOR_LIMIT_PER_BIN + 1, 9).astype("<f2").tobytes()
        self.assertRefused(edited(lambda d: d["color"].__setitem__(0, base64.b64encode(rows).decode())))

    def test_non_finite_or_non_unit_vectors(self):
        nan = np.full((1, templates.EMBEDDING_SIZE), np.nan, "<f2").tobytes()
        self.assertRefused(edited(lambda d: d["infrared"].__setitem__(1, base64.b64encode(nan).decode())))
        long = (random_units(1, 5) * 2).astype("<f2").tobytes()
        self.assertRefused(edited(lambda d: d["infrared"].__setitem__(1, base64.b64encode(long).decode())))

    def test_non_finite_numbers_and_repeated_keys(self):
        self.assertRefused(edited(lambda d: d.update(infrared_level=float("nan"))))
        self.assertRefused(edited(lambda d: d["shape_median"].__setitem__(0, float("inf"))))
        text = edited(lambda d: None)
        self.assertRefused(text[:-1] + ',"created":"2026-01-31 12:00"}')

    def test_out_of_range_reference(self):
        self.assertRefused(edited(lambda d: d.update(infrared_level=0)))
        self.assertRefused(edited(lambda d: d["shape_median"].__setitem__(2, -1.0)))
        self.assertRefused(edited(lambda d: d["shape_spread"].__setitem__(2, -0.1)))
        self.assertRefused(edited(lambda d: d.update(shape_median=d["shape_median"][:-1])))
        self.assertRefused(edited(lambda d: d["shape_spread"].__setitem__(0, True)))

    def test_bad_dates(self):
        self.assertRefused(edited(lambda d: d.update(created="yesterday")))
        self.assertRefused(edited(lambda d: d.update(additions=["2026-01-31 12:00", 5])))

    def test_oversized_text(self):
        self.assertRefused(" " * (templates.MAXIMUM_TEXT_BYTES + 1))


class MergeTests(unittest.TestCase):
    def test_prune_drops_repeats_first(self):
        distinct = random_units(3, 11)
        repeated = np.vstack([distinct, unit(distinct[0] + 0.01 * random_units(1, 12)[0])])
        kept = prune(repeated, 3)
        self.assertEqual(len(kept), 3)
        similarity = kept @ kept.T
        np.fill_diagonal(similarity, 0)
        self.assertLess(similarity.max(), 0.99)

    def test_merge_limits_and_combines(self):
        old, new = sample_template(per_bin=templates.COLOR_LIMIT_PER_BIN - 1), sample_template(per_bin=5, seed=50)
        new.reference = type(new.reference)(new.reference.infrared_level * 2, new.reference.shape_median * 1.02,
                                            new.reference.shape_spread * 0.5)
        merged = merge(old, new)
        self.assertTrue(all(len(bank) <= templates.COLOR_LIMIT_PER_BIN for bank in merged.color))
        self.assertEqual((merged.created, merged.additions), (old.created, [new.created]))
        self.assertAlmostEqual(merged.reference.infrared_level, old.reference.infrared_level * 1.5)
        self.assertTrue((merged.reference.shape_spread >= old.reference.shape_spread).all())

    def test_merge_refuses_another_direction(self):
        other = sample_template()
        other.direction = -1
        with self.assertRaises(TemplateError):
            merge(sample_template(), other)

    def test_best_similarity_uses_neighbouring_bins(self):
        template = sample_template()
        probe = template.color[3][0]
        self.assertAlmostEqual(templates.best_similarity(template.color, 4, probe), 1.0, places=5)
        self.assertLess(templates.best_similarity(template.color, 6, probe), 0.9)


if __name__ == "__main__":
    unittest.main()
