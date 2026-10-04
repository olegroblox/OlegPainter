# -*- coding: utf-8 -*-
"""area_sequence='nearest': greedy nearest-neighbour ordering of same-colour regions.

Instead of large->small / small->large, regions are visited closest-first from where the
pen finished the previous region (start_anchor), regardless of size. Only the order in which
_ordered_component_labels hands components to the traversal changes; the per-region route is
untouched. See engine/olegpainter/core.py:_ordered_component_labels / _nearest_neighbor_labels.
"""
import os
import sys
import unittest

import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from ui.services.painter_service import PainterService  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _stats(rows):
    """rows: [(left, top, width, height, area), ...] indexed by label (index 0 = bg)."""
    s = np.zeros((len(rows), 5), dtype=np.int32)
    for i, (l, t, w, h, a) in enumerate(rows):
        s[i, cv2.CC_STAT_LEFT] = l
        s[i, cv2.CC_STAT_TOP] = t
        s[i, cv2.CC_STAT_WIDTH] = w
        s[i, cv2.CC_STAT_HEIGHT] = h
        s[i, cv2.CC_STAT_AREA] = a
    return s


def _centroids(rows):
    """rows: [(x, y), ...] indexed by label (index 0 = bg)."""
    return np.array(rows, dtype=np.float64)


class NearestAreaTests(unittest.TestCase):
    def setUp(self):
        self.svc = PainterService()
        self.e = self.svc.engine
        # 4 regions on a line: L1=(0,0) L2=(10,0) L3=(100,0 far, largest) L4=(11,0)
        self.num = 5
        self.stats = _stats([
            (0, 0, 0, 0, 0),     # bg
            (0, 0, 0, 0, 5),     # L1
            (10, 0, 0, 0, 5),    # L2
            (100, 0, 0, 0, 100), # L3 (largest)
            (11, 0, 0, 0, 5),    # L4
        ])
        self.cents = _centroids([(0, 0), (0, 0), (10, 0), (100, 0), (11, 0)])

    def tearDown(self):
        self.svc.shutdown(); self.svc.deleteLater(); _app.processEvents()

    # ---- the greedy chain -------------------------------------------------
    def test_no_anchor_starts_at_largest_then_nearest(self):
        order = self.e._nearest_neighbor_labels([1, 2, 3, 4], self.stats, self.cents, None)
        # start = largest area (L3@100), then nearest-remaining each step
        self.assertEqual(order, [3, 4, 2, 1])

    def test_anchor_starts_nearest_to_anchor(self):
        order = self.e._nearest_neighbor_labels([1, 2, 3, 4], self.stats, self.cents, (0, 0))
        self.assertEqual(order, [1, 2, 4, 3])

    def test_bbox_center_fallback_when_no_centroids(self):
        # WIDTH/HEIGHT are 0 so bbox-centre == (LEFT, TOP) == the centroid positions above
        order = self.e._nearest_neighbor_labels([1, 2, 3, 4], self.stats, None, (0, 0))
        self.assertEqual(order, [1, 2, 4, 3])

    def test_single_region_is_trivial(self):
        self.assertEqual(self.e._nearest_neighbor_labels([1], self.stats, self.cents, None), [1])

    def test_huge_count_falls_back(self):
        big = list(range(1, 9000))
        self.assertEqual(self.e._nearest_neighbor_labels(big, self.stats, self.cents, None), [])

    # ---- dispatch through _ordered_component_labels -----------------------
    def test_dispatch_nearest_vs_size_orders_differ(self):
        self.e.area_sequence = "nearest"
        nn = self.e._ordered_component_labels(self.num, self.stats, self.cents, None)
        self.assertEqual(nn, [3, 4, 2, 1])

        self.e.area_sequence = "large_to_small"
        big = self.e._ordered_component_labels(self.num, self.stats, self.cents, None)
        self.assertEqual(big[0], 3)            # largest first
        self.assertNotEqual(big, nn)

        self.e.area_sequence = "small_to_large"
        small = self.e._ordered_component_labels(self.num, self.stats, self.cents, None)
        self.assertEqual(small[-1], 3)         # largest last

    def test_nearest_falls_back_to_size_when_stats_missing(self):
        self.e.area_sequence = "nearest"
        self.assertEqual(self.e._ordered_component_labels(self.num, None, None, None),
                         [1, 2, 3, 4])         # stats None -> early plain-label return

    # ---- config / setter --------------------------------------------------
    def test_set_area_sequence_accepts_nearest(self):
        self.e.area_sequence = "large_to_small"
        self.assertTrue(self.e.set_area_sequence("nearest"))
        self.assertEqual(self.e.area_sequence, "nearest")
        self.assertFalse(self.e.set_area_sequence("garbage"))
        self.assertEqual(self.e.area_sequence, "nearest")

    def test_config_round_trip(self):
        self.e.area_sequence = "nearest"
        self.assertEqual(self.e.get_config().get("area_sequence"), "nearest")
        self.e.area_sequence = "large_to_small"
        self.e.load_config({"area_sequence": "nearest"})
        self.assertEqual(self.e.area_sequence, "nearest")

    def test_last_pen_cell_state_exists_and_resets(self):
        self.assertIsNone(getattr(self.e, "_last_pen_cell"))
        self.e._last_pen_cell = (5.0, 7.0)
        self.e.reset()
        self.assertIsNone(self.e._last_pen_cell)


if __name__ == "__main__":
    unittest.main()
