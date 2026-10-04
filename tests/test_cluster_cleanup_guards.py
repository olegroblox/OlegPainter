# -*- coding: utf-8 -*-
"""Regression guards for the two cleanup bugs reported live:

  Bug 1 — "Векторная + слияние мусора" collapsed a gradient to one colour when
          min_area was large (it is shared with the aggressive cell-merge). The
          Area-criterion colour cap must keep DISTINCT-colour regions apart.
  Bug 2 — "объединение ячеек" left a speck surrounded only by removed background
          as an orphan ("картинка рваная"). It must drop into the background.
"""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.cluster_cleanup import (
    apply_cleanup_mode, distance_relabel_specks, smart_stray_merge,
)
from engine.olegpainter.core import OlegPainter


def _bands():
    """6 vertical colour bands (clusters 0..5), each 4 px wide, on a 24x24 grid.
    Adjacent band colours are ~16 in CIELAB apart (a 40-value grey ramp)."""
    H, W = 24, 24
    lbl = np.zeros((H, W), dtype=int)
    rgb = np.zeros((H, W, 3), dtype=np.uint8)
    for b in range(6):
        lbl[:, b * 4:(b + 1) * 4] = b
        v = 30 + b * 40
        rgb[:, b * 4:(b + 1) * 4] = (v, v, v)
    return lbl, rgb


def _distinct(m):
    return sorted(int(v) for v in np.unique(m) if int(v) != -1)


class StrayMergeColourGuardTests(unittest.TestCase):
    def test_distinct_bands_do_not_collapse_with_cap_even_at_huge_min_area(self):
        lbl, rgb = _bands()
        out, meta = apply_cleanup_mode(lbl, rgb, mode="vectorized_plus_stray_merge",
                                       min_area=100, background=-1, space="lab",
                                       max_merge_distance=12.0)
        self.assertEqual(_distinct(out), [0, 1, 2, 3, 4, 5], "distinct colours must survive")
        self.assertEqual(meta["changed_cells"], 0)

    def test_collapse_reproduces_without_the_cap(self):
        """Documents the legacy behaviour the cap fixes: no cap + huge min_area
        DOES cascade (this is exactly what the user hit)."""
        lbl, rgb = _bands()
        out, _ = apply_cleanup_mode(lbl, rgb, mode="vectorized_plus_stray_merge",
                                    min_area=100, background=-1, space="lab",
                                    max_merge_distance=None)
        self.assertLess(len(_distinct(out)), 6, "without the cap the gradient collapses")

    def test_close_colour_speck_still_merges_under_the_cap(self):
        """The cap must not block legitimate speck removal: a tiny speck whose
        colour is near its neighbour still merges."""
        H, W = 12, 12
        lbl = np.zeros((H, W), dtype=int)       # cluster 0 everywhere
        lbl[5:7, 5:7] = 1                        # a 4-px speck of cluster 1
        rgb = np.zeros((H, W, 3), dtype=np.uint8)
        rgb[:] = (100, 100, 100)                 # cluster 0 colour
        rgb[5:7, 5:7] = (104, 104, 104)          # cluster 1: ~1.5 lab away (close)
        out = smart_stray_merge(lbl, {0: (100, 100, 100), 1: (104, 104, 104)},
                                min_area=8, background=-1, space="lab",
                                max_merge_distance=12.0)
        self.assertEqual(_distinct(out), [0], "a close-colour speck must merge away")


class CellMergeOrphanTests(unittest.TestCase):
    def _engine(self):
        return OlegPainter(status_callback=lambda *_a, **_k: None)

    def test_speck_surrounded_by_background_is_dropped_not_orphaned(self):
        e = self._engine()
        cm = np.full((16, 16), -1, dtype=int)    # all removed background
        cm[2:12, 2:12] = 0                        # one big region, cluster 0
        cm[6:8, 6:8] = 7                          # interior speck of cluster 7 (has a real neighbour)
        cm[0, 0] = 5                              # a 1-cell speck floating in the void (only -1 neighbours)
        # actually place the void speck fully surrounded by -1:
        cm[14, 14] = 9
        e.cluster_map = cm
        e.fast_min_region_area = 50               # everything small merges
        center = {0: (10, 10, 10), 5: (200, 0, 0), 7: (0, 200, 0), 9: (0, 0, 200)}
        e._merge_small_components_into_neighbors(center, background_cluster_id=-1)
        # the void speck (9 at [14,14], only -1 neighbours) is dropped to background
        self.assertEqual(int(e.cluster_map[14, 14]), -1, "orphan speck must drop to background")
        self.assertEqual(int(e.cluster_map[0, 0]), -1, "isolated speck must drop to background")
        # the interior speck (7) had a real neighbour (0) -> merged into it, not dropped
        self.assertEqual(int(e.cluster_map[6, 6]), 0)


class DistanceRelabelDespeckleTests(unittest.TestCase):
    def test_specks_relabel_to_nearest_large_no_holes_no_collapse(self):
        # two large regions (left 0, right 1) + a speck of cluster 5 inside the
        # left region; the speck must become 0 (nearest large), nothing deleted.
        H, W = 20, 20
        m = np.zeros((H, W), dtype=int)
        m[:, 10:] = 1
        m[2:4, 2:4] = 5                          # 4-px speck inside region 0
        out = distance_relabel_specks(m, min_area=8, background=-1)
        self.assertEqual(_distinct(out), [0, 1], "speck merged, both big regions survive")
        self.assertEqual(int(out[2, 2]), 0, "speck took the nearest large label")
        self.assertFalse(np.any(out == -1), "no holes introduced")

    def test_isolated_void_speck_is_dropped_not_bridged(self):
        # a speck floating far inside removed background -> dropped to -1, not
        # bridged across the gap to a distant region.
        H, W = 30, 30
        m = np.full((H, W), -1, dtype=int)
        m[0:6, 0:6] = 0                          # one big region in a corner
        m[26, 26] = 7                            # 1-px speck far away in the void
        out = distance_relabel_specks(m, min_area=4, background=-1, max_bridge=3.0)
        self.assertEqual(int(out[26, 26]), -1, "far isolated speck dropped to background")
        self.assertTrue(np.any(out == 0), "the big region is untouched")

    def test_no_large_region_drops_all_specks(self):
        m = np.full((10, 10), -1, dtype=int)
        m[0, 0] = 3                              # only a lone speck, no big region
        out = distance_relabel_specks(m, min_area=4, background=-1)
        self.assertFalse(np.any(out != -1), "with nothing to attach to, specks drop")

    def test_engine_aggressive_despeckle_off_by_default(self):
        e = OlegPainter(status_callback=lambda *_a, **_k: None)
        self.assertFalse(e.aggressive_despeckle_enabled)
        cfg = e.get_config()
        self.assertIn("aggressive_despeckle_enabled", cfg)
        e.aggressive_despeckle_enabled = True
        fresh = OlegPainter(status_callback=lambda *_a, **_k: None)
        fresh.load_config(e.get_config())
        self.assertTrue(fresh.aggressive_despeckle_enabled)

    def test_engine_apply_despeckle_relabels_and_filters_palette(self):
        e = OlegPainter(status_callback=lambda *_a, **_k: None)
        m = np.zeros((20, 20), dtype=int)
        m[:, 10:] = 1
        m[2:4, 2:4] = 5                          # speck of cluster 5 in region 0
        e.cluster_map = m
        e.color_palette = [("#000000", 0, 200, (0, 0, 0)),
                           ("#ffffff", 1, 200, (255, 255, 255)),
                           ("#ff0000", 5, 4, (255, 0, 0))]
        e.aggressive_despeckle_enabled = True
        e.fast_min_region_area = 8
        changed = e._apply_aggressive_despeckle(-1)
        self.assertGreater(changed, 0)
        self.assertEqual(sorted(entry[1] for entry in e.color_palette), [0, 1],
                         "empty cluster 5 dropped from palette")


if __name__ == "__main__":
    unittest.main()
