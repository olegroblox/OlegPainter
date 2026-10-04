# -*- coding: utf-8 -*-
"""Semantic background removal: drop a whole plane (backdrop or object) from
drawing, post-quantization. Opt-in; composes with the pixel-based removal."""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.core import OlegPainter


def _engine_with_planes():
    """cluster 0 = frame-touching backdrop, cluster 1 = central object, both
    foreground. Synthetic saliency makes the split unambiguous."""
    e = OlegPainter(status_callback=lambda *_a, **_k: None)
    cm = np.zeros((20, 20), dtype=int)
    cm[6:14, 6:14] = 1
    e.cluster_map = cm
    e.color_palette = [("#202020", 0, 256, (32, 32, 32)),
                       ("#f0f0f0", 1, 64, (240, 240, 240))]
    sal = np.zeros(cm.shape, dtype=np.float32)
    sal[cm == 1] = 0.95          # object salient
    sal[cm == 0] = 0.05          # backdrop not
    e._semantic_saliency_cache = (id(cm), sal)
    return e


class SemanticBackgroundRemovalTests(unittest.TestCase):
    def test_drop_backdrop_keeps_object_only(self):
        e = _engine_with_planes()
        e.semantic_background_drop = "backdrop"
        dropped = e._apply_semantic_background_removal(-1)
        self.assertGreater(dropped, 0)
        self.assertTrue(np.all(e.cluster_map[e.cluster_map != -1] == 1),
                        "only the object plane survives")
        self.assertEqual([entry[1] for entry in e.color_palette], [1],
                         "backdrop colour dropped from palette")
        self.assertTrue(np.array_equal(e.background_mask, e.cluster_map == -1))

    def test_drop_object_keeps_backdrop_only(self):
        e = _engine_with_planes()
        e.semantic_background_drop = "object"
        dropped = e._apply_semantic_background_removal(-1)
        self.assertGreater(dropped, 0)
        survivors = set(int(v) for v in np.unique(e.cluster_map) if v != -1)
        self.assertEqual(survivors, {0}, "only the backdrop plane survives")
        self.assertEqual([entry[1] for entry in e.color_palette], [0])

    def test_off_is_no_op(self):
        e = _engine_with_planes()
        before = e.cluster_map.copy()
        e.semantic_background_drop = "off"
        self.assertEqual(e._apply_semantic_background_removal(-1), 0)
        self.assertTrue(np.array_equal(e.cluster_map, before))
        self.assertEqual(len(e.color_palette), 2)

    def test_normalizer_and_config_roundtrip(self):
        e = OlegPainter(status_callback=lambda *_a, **_k: None)
        assert e.semantic_background_drop == "off"
        self.assertEqual(e._normalize_semantic_background_drop("BACKDROP"), "backdrop")
        self.assertEqual(e._normalize_semantic_background_drop("nonsense"), "off")
        e.semantic_background_drop = "object"
        cfg = e.get_config()
        self.assertEqual(cfg["semantic_background_drop"], "object")
        fresh = OlegPainter(status_callback=lambda *_a, **_k: None)
        fresh.load_config(cfg)
        self.assertEqual(fresh.semantic_background_drop, "object")

    def test_degenerate_planes_are_safe(self):
        e = OlegPainter(status_callback=lambda *_a, **_k: None)
        e.cluster_map = np.zeros((8, 8), dtype=int)   # single cluster -> no split
        e.color_palette = [("#101010", 0, 64, (16, 16, 16))]
        e.semantic_background_drop = "backdrop"
        self.assertEqual(e._apply_semantic_background_removal(-1), 0)
        self.assertEqual(len(e.color_palette), 1)


if __name__ == "__main__":
    unittest.main()
