# -*- coding: utf-8 -*-
"""Regression tests for two fixes:
  1. Single colour allowed (K_Clusters may be 1) — needed for monochrome images.
  2. Transparent PNG background is excluded in B&W mode (alpha always defines
     background, even with the background-removal feature OFF).
"""
import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import numpy as np
from PIL import Image

from engine.olegpainter.core import OlegPainter


def make_engine():
    noop = lambda *a, **k: None
    p = OlegPainter(status_callback=noop, pixel_update_callback=noop, preview_update_callback=noop)
    p.draw_region = (0, 0, 40, 40)
    p.brush_size = 1
    p.remove_background = False
    p.use_ai_background = False
    p.stencil_enabled = False
    p.alpha_threshold = 10
    return p


class TransparentBwTests(unittest.TestCase):
    def test_transparent_bg_excluded_in_black_only(self):
        p = make_engine()
        p.mode = "bw"
        p.bw_draw_mode = "black_only"
        # 40x40 fully transparent (RGB black), with a 20x20 opaque black square.
        arr = np.zeros((40, 40, 4), dtype=np.uint8)
        arr[10:30, 10:30, 3] = 255
        p.source_pil_image = Image.fromarray(arr, "RGBA")
        p.IMAGE_PATH = "unit-test"
        self.assertTrue(p.prepare_image_and_palette())
        cm = p.cluster_map
        background = int((cm == -1).sum())
        drawn = int((cm == 1).sum())
        # The transparent area must be background (was 0 before the fix), and the
        # opaque square must be the only drawn region.
        self.assertGreater(background, drawn, "transparent pixels must be background")
        self.assertLess(drawn, cm.size // 2, "only the opaque shape should be drawn")
        self.assertGreater(drawn, 0)


class SingleColorTests(unittest.TestCase):
    def test_k_clusters_one_is_allowed(self):
        p = make_engine()
        self.assertTrue(p.set_k_clusters(1))
        self.assertEqual(p.k_clusters, 1)

    def test_k_clusters_zero_rejected(self):
        p = make_engine()
        self.assertFalse(p.set_k_clusters(0))

    def test_single_color_prep_yields_one_palette_entry(self):
        p = make_engine()
        p.mode = "color"
        p.set_k_clusters(1)
        img = np.zeros((20, 20, 4), dtype=np.uint8)
        img[:, :, 3] = 255
        img[:, :10, 0] = 200
        img[:, 10:, 2] = 200
        p.source_pil_image = Image.fromarray(img, "RGBA")
        p.IMAGE_PATH = "unit-test"
        self.assertTrue(p.prepare_image_and_palette())
        self.assertEqual(len(p.color_palette), 1)


if __name__ == "__main__":
    unittest.main()
