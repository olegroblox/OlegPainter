# -*- coding: utf-8 -*-
"""CIEDE2000 implementation correctness (Sharma et al. 2005 reference pairs) +
the palette-match-metric flag. CIEDE2000 is used for the FINAL fixed-palette
snap, where it predicts perceived difference better than Euclidean Lab."""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.color_spaces import ciede2000
from engine.olegpainter.core import OlegPainter

# (lab1, lab2, expected dE2000) — canonical Sharma/Rochester test set.
SHARMA = [
    ((50, 2.6772, -79.7751), (50, 0, -82.7485), 2.0425),
    ((50, 3.1571, -77.2803), (50, 0, -82.7485), 2.8615),
    ((50, 2.8361, -74.0200), (50, 0, -82.7485), 3.4412),
    ((50, -1.3802, -84.2814), (50, 0, -82.7485), 1.0000),
    ((50, -1.1848, -84.8006), (50, 0, -82.7485), 1.0000),
    ((50, 0, 0), (50, -1, 2), 2.3669),
    ((50, 2.49, -0.001), (50, -2.49, 0.0009), 7.1792),
    ((60.2574, -34.0099, 36.2677), (60.4626, -34.1751, 39.4387), 1.2644),
    ((63.0109, -31.0961, -5.8663), (62.8187, -29.7946, -4.0864), 1.2630),
    ((35.0831, -44.1164, 3.7933), (35.0232, -40.0716, 1.5901), 1.8645),
    ((22.7233, 20.0904, -46.6940), (23.0331, 14.9730, -42.5619), 2.0373),
    ((2.0776, 0.0795, -1.1350), (0.9033, -0.0636, -0.5514), 0.9082),
]


class CIEDE2000Tests(unittest.TestCase):
    def test_matches_sharma_reference_pairs(self):
        for lab1, lab2, expected in SHARMA:
            got = float(ciede2000(np.array(lab1, float), np.array(lab2, float)))
            self.assertAlmostEqual(got, expected, places=3, msg=f"{lab1} vs {lab2}")

    def test_zero_for_identical(self):
        lab = np.array([55.0, 12.0, -7.0])
        self.assertAlmostEqual(float(ciede2000(lab, lab)), 0.0, places=6)

    def test_broadcasts_target_vs_array(self):
        target = np.array([50.0, 0.0, 0.0])
        cands = np.array([[50.0, 0.0, 0.0], [50.0, -1.0, 2.0], [60.0, 10.0, 10.0]])
        d = ciede2000(target, cands)
        self.assertEqual(d.shape, (3,))
        self.assertAlmostEqual(float(d[0]), 0.0, places=6)
        self.assertAlmostEqual(float(d[1]), 2.3669, places=3)
        self.assertEqual(int(np.argmin(d)), 0)

    def test_metric_can_disagree_with_euclidean_lab(self):
        """The whole point of switching the final match: CIEDE2000 and Euclidean
        Lab do NOT always pick the same nearest candidate. Verified statistically
        over random Lab triples (the disagreement rate is well above zero)."""
        rng = np.random.default_rng(0)
        disagreements = 0
        trials = 400
        for _ in range(trials):
            target = np.array([rng.uniform(20, 80), rng.uniform(-60, 60), rng.uniform(-60, 60)])
            cands = np.column_stack([rng.uniform(20, 80, 6),
                                     rng.uniform(-60, 60, 6),
                                     rng.uniform(-60, 60, 6)])
            eucl = ((cands - target) ** 2).sum(axis=1)
            de00 = ciede2000(target, cands)
            if int(np.argmin(eucl)) != int(np.argmin(de00)):
                disagreements += 1
        # if the metric never changed the choice, switching it would be pointless
        self.assertGreater(disagreements, 0, "CIEDE2000 must sometimes pick differently than CIE76")


class PaletteMatchMetricFlagTests(unittest.TestCase):
    def test_default_is_ciede2000_and_roundtrips(self):
        e = OlegPainter(status_callback=lambda *_a, **_k: None)
        self.assertEqual(e.palette_match_metric, "ciede2000")
        self.assertEqual(e._palette_match_metric_value(), "ciede2000")
        e.palette_match_metric = "euclidean"
        cfg = e.get_config()
        self.assertEqual(cfg["palette_match_metric"], "euclidean")
        fresh = OlegPainter(status_callback=lambda *_a, **_k: None)
        fresh.load_config(cfg)
        self.assertEqual(fresh.palette_match_metric, "euclidean")

    def test_garbage_normalizes_to_ciede2000(self):
        e = OlegPainter(status_callback=lambda *_a, **_k: None)
        e.palette_match_metric = "nonsense"
        self.assertEqual(e._palette_match_metric_value(), "ciede2000")
        fresh = OlegPainter(status_callback=lambda *_a, **_k: None)
        fresh.load_config({"palette_match_metric": "junk"})
        self.assertEqual(fresh.palette_match_metric, "ciede2000")


if __name__ == "__main__":
    unittest.main()
