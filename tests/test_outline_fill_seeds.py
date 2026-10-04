# -*- coding: utf-8 -*-
"""Outline+Fill bucket-seed selection: every seed must land DEEP inside the
region (>= safety radius from any edge), never on the outline — the fix for
"clicks outside / misses on complex or tiny areas". Regions with no safe
interior point return [] so the caller brush-fills them instead."""
from __future__ import annotations

import os
import sys
import unittest

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.core import OlegPainter


def _engine(brush=1):
    e = OlegPainter(status_callback=lambda *_a, **_k: None)
    e.brush_size = brush
    return e


def _dist(mask):
    return cv2.distanceTransform(mask.astype(np.uint8) * 255, cv2.DIST_L2, 3).astype(np.float32)


class OutlineFillSeedTests(unittest.TestCase):
    def _assert_all_seeds_deep_inside(self, e, mask, seeds):
        dist = _dist(mask)
        safety = e._outline_fill_safety_radius(int(e.brush_size))
        for r, c in seeds:
            self.assertTrue(mask[r, c], f"seed ({r},{c}) is OUTSIDE the region")
            self.assertGreaterEqual(float(dist[r, c]), safety,
                                    f"seed ({r},{c}) too close to edge: {dist[r, c]} < {safety}")

    def test_solid_blob_seed_is_central_and_deep(self):
        e = _engine(brush=1)
        mask = np.zeros((20, 20), bool)
        mask[4:16, 4:16] = True                       # 12x12 blob, max dist ~6
        seeds = e._outline_fill_seed_points(mask, limit=1)
        self.assertEqual(len(seeds), 1)
        self._assert_all_seeds_deep_inside(e, mask, seeds)

    def test_concave_C_shape_never_seeds_outside(self):
        """A C / U shape whose CENTROID lies in the empty notch — the old
        centroid+anchor seeds landed outside and snapped to the boundary."""
        mask = np.zeros((24, 24), bool)
        mask[4:20, 4:20] = True
        mask[8:16, 10:20] = False                     # cut a notch on the right -> "C"
        cy, cx = np.argwhere(mask).mean(axis=0)
        # the centroid really is outside the region (that's the trap)
        self.assertFalse(mask[int(round(cy)), int(round(cx))], "test setup: centroid should be outside")
        e = _engine(brush=1)
        seeds = e._outline_fill_seed_points(mask, limit=3)
        self.assertGreater(len(seeds), 0, "a C-shape has a thick arm to click")
        self._assert_all_seeds_deep_inside(e, mask, seeds)

    def test_thin_stroke_returns_empty_for_brush_fallback(self):
        e = _engine(brush=1)             # safety = 2.0 cells
        mask = np.zeros((10, 30), bool)
        mask[5, 2:28] = True             # 1-cell-wide stroke: max dist ~0.5
        mask[4:7, 14] = True             # a tiny 3-tall bump, still < 2 deep
        self.assertEqual(e._outline_fill_seed_points(mask, limit=3), [],
                         "no point is 2 cells deep -> brush-fill")

    def test_two_lobes_get_one_seed_each(self):
        """Dumbbell: two blobs joined by a thin neck. With >=2 clicks each lobe
        gets its own deep seed (the neck is too thin to be a safe lobe)."""
        mask = np.zeros((20, 40), bool)
        mask[5:15, 3:13] = True          # left blob
        mask[5:15, 27:37] = True         # right blob
        mask[9:11, 13:27] = True         # thin neck (2 cells tall)
        e = _engine(brush=1)
        seeds = e._outline_fill_seed_points(mask, limit=2)
        self.assertEqual(len(seeds), 2)
        self._assert_all_seeds_deep_inside(e, mask, seeds)
        # the two seeds sit on opposite sides (one per blob)
        cols = sorted(c for _r, c in seeds)
        self.assertLess(cols[0], 20)
        self.assertGreater(cols[1], 20)

    def test_safety_radius_scales_with_brush(self):
        e = _engine(brush=1)
        self.assertEqual(e._outline_fill_safety_radius(1), 2.0)   # floor
        self.assertEqual(e._outline_fill_safety_radius(5), 5.0)   # brush dominates
        e.outline_fill_min_interior_cells = 3.0
        self.assertEqual(e._outline_fill_safety_radius(1), 3.0)   # tunable floor

    def test_empty_and_degenerate_inputs_are_safe(self):
        e = _engine(brush=1)
        self.assertEqual(e._outline_fill_seed_points(None), [])
        self.assertEqual(e._outline_fill_seed_points(np.zeros((8, 8), bool)), [])
        big = np.zeros((30, 30), bool)
        big[5:25, 5:25] = True
        seeds = e._outline_fill_seed_points(big, limit=5)
        self.assertGreaterEqual(len(seeds), 1)
        self._assert_all_seeds_deep_inside(e, big, seeds)


if __name__ == "__main__":
    unittest.main()
