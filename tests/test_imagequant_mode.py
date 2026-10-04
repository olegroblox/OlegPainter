# -*- coding: utf-8 -*-
"""imagequant quantization mode (soft-import). If neither PIL-libimagequant nor
the `quantizr` wheel is installed, it must fall back to k-means WITHOUT error
and still produce a valid palette + labels."""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.core import OlegPainter
from engine.olegpainter.prep_pipeline import _imagequant_palette, perceptual_kmeans_quantize


def _scene():
    img = np.zeros((32, 32, 3), dtype=np.uint8)
    img[:, :16] = (200, 40, 40)
    img[:, 16:] = (40, 80, 200)
    img[0:8, 0:8] = (250, 240, 30)
    return img


class ImagequantModeTests(unittest.TestCase):
    def test_mode_runs_and_returns_valid_palette(self):
        img = _scene()
        labels, centers, palette = perceptual_kmeans_quantize(
            img, k=4, color_space="oklab", mode="imagequant", random_state=1)
        self.assertEqual(labels.shape, (32, 32))
        self.assertGreaterEqual(centers.shape[0], 1)
        self.assertLessEqual(centers.shape[0], 4)
        self.assertEqual(palette.shape[1], 3)
        # every label indexes a real palette entry
        self.assertTrue(0 <= int(labels.min()) and int(labels.max()) < centers.shape[0])

    def test_palette_helper_returns_none_or_array(self):
        # On a machine without either backend this is None (the fallback path);
        # if a backend IS present it returns an (m,3) uint8 array. Both are OK.
        out = _imagequant_palette(_scene(), 4)
        if out is not None:
            self.assertEqual(out.ndim, 2)
            self.assertEqual(out.shape[1], 3)
            self.assertEqual(out.dtype, np.uint8)

    def test_normalizer_accepts_imagequant_and_config_roundtrip(self):
        e = OlegPainter(status_callback=lambda *_a, **_k: None)
        self.assertEqual(e._normalize_prep_quantization_mode("imagequant"), "imagequant")
        self.assertEqual(e._normalize_prep_quantization_mode("garbage"), "kmeans")
        e.prep_quantization_mode = "imagequant"
        cfg = e.get_config()
        self.assertEqual(cfg["prep_quantization_mode"], "imagequant")
        fresh = OlegPainter(status_callback=lambda *_a, **_k: None)
        fresh.load_config(cfg)
        self.assertEqual(fresh.prep_quantization_mode, "imagequant")


if __name__ == "__main__":
    unittest.main()
