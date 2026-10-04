# -*- coding: utf-8 -*-
"""Accent boost: chroma-weighting pulls k-means centroids toward SATURATED
pixels, so vivid areas come out less washed-out. Opt-in (changes the palette)."""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.color_spaces import rgb_to_oklab_numpy
from engine.olegpainter.prep_pipeline import _chroma_sample_weights, perceptual_kmeans_quantize


def _palette_max_chroma(palette_rgb) -> float:
    okl = rgb_to_oklab_numpy(np.asarray(palette_rgb, dtype=np.uint8).reshape(-1, 1, 3)).reshape(-1, 3)
    return float(np.sqrt((okl[:, 1:3] ** 2).sum(axis=1)).max())


class ChromaWeightTests(unittest.TestCase):
    def test_higher_chroma_gets_higher_weight(self):
        # OKLab vectors: [L, a, b]; chroma = hypot(a, b)
        data = np.array([[0.5, 0.0, 0.0],     # grey, chroma 0
                         [0.5, 0.1, 0.0],     # mild
                         [0.5, 0.3, 0.0]],    # vivid
                        dtype=np.float64)
        w = _chroma_sample_weights(data, "oklab", strength=3.0)
        self.assertIsNotNone(w)
        self.assertLess(w[0], w[1])
        self.assertLess(w[1], w[2])
        self.assertAlmostEqual(float(w[0]), 1.0, places=6)   # grey gets the base weight

    def test_none_for_non_perceptual_or_zero_strength(self):
        data = np.array([[0.5, 0.3, 0.0]], dtype=np.float64)
        self.assertIsNone(_chroma_sample_weights(data, "legacy_rgb", 3.0))
        self.assertIsNone(_chroma_sample_weights(data, "oklab", 0.0))


class AccentBoostEffectTests(unittest.TestCase):
    def _scene(self):
        """A large dull/greyish background + a moderate saturated region that a
        4-colour palette won't fully resolve on its own."""
        img = np.full((48, 48, 3), 150, dtype=np.uint8)
        img[0:24, :] = (140, 138, 142)
        img[24:48, :] = (160, 158, 156)
        img[18:30, 18:30] = (200, 60, 40)        # saturated patch (~144 px)
        return img

    def test_boost_pulls_palette_toward_saturation(self):
        img = self._scene()
        _, _, pal_off = perceptual_kmeans_quantize(
            img, k=4, color_space="oklab", preserve_accents=False, random_state=1)
        _, _, pal_on = perceptual_kmeans_quantize(
            img, k=4, color_space="oklab", preserve_accents=True, accent_strength=5.0, random_state=1)
        # the most-saturated palette entry is at least as vivid with the boost on
        self.assertGreaterEqual(_palette_max_chroma(pal_on), _palette_max_chroma(pal_off) - 1e-9)

    def test_legacy_rgb_is_identical_on_off(self):
        img = self._scene()
        a, _, pa = perceptual_kmeans_quantize(img, k=4, color_space="legacy_rgb",
                                              preserve_accents=False, random_state=1)
        b, _, pb = perceptual_kmeans_quantize(img, k=4, color_space="legacy_rgb",
                                              preserve_accents=True, random_state=1)
        self.assertTrue(np.array_equal(pa, pb) and np.array_equal(a, b))

    def test_engine_default_off_and_config_roundtrip(self):
        from engine.olegpainter.core import OlegPainter
        e = OlegPainter(status_callback=lambda *_a, **_k: None)
        self.assertFalse(e.prep_preserve_accents)            # opt-in
        e.prep_preserve_accents = True
        cfg = e.get_config()
        self.assertEqual(cfg["prep_preserve_accents"], True)
        fresh = OlegPainter(status_callback=lambda *_a, **_k: None)
        fresh.load_config(cfg)
        self.assertTrue(fresh.prep_preserve_accents)


if __name__ == "__main__":
    unittest.main()
