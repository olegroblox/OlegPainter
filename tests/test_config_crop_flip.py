# -*- coding: utf-8 -*-
"""Phase 1: crop_norm / flip persistence in engine config + payload category."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.core import OlegPainter  # noqa: E402
from ui.helpers.config_payload import PAINTER_CATEGORY_KEYS  # noqa: E402

_NOOP = lambda *a, **k: None  # noqa: E731


def _engine():
    return OlegPainter(status_callback=_NOOP, pixel_update_callback=_NOOP, preview_update_callback=_NOOP)


class CropFlipPersistenceTests(unittest.TestCase):
    def test_get_config_includes_crop_and_flip(self):
        e = _engine()
        e.set_crop_norm((0.1, 0.2, 0.8, 0.9), reprocess=False)
        e.set_source_flip(horizontal=True, vertical=False, reprocess=False)
        cfg = e.get_config()
        self.assertEqual(tuple(cfg["crop_norm"]), (0.1, 0.2, 0.8, 0.9))
        self.assertTrue(cfg["flip_horizontal"])
        self.assertFalse(cfg["flip_vertical"])

    def test_load_config_restores_crop_and_flip(self):
        e = _engine()
        e.load_config({"crop_norm": [0.25, 0.0, 0.75, 1.0], "flip_horizontal": True, "flip_vertical": True})
        self.assertEqual(e.get_crop_norm(), (0.25, 0.0, 0.75, 1.0))
        self.assertTrue(e.flip_horizontal)
        self.assertTrue(e.flip_vertical)

    def test_load_config_missing_resets_to_full(self):
        e = _engine()
        e.set_crop_norm((0.1, 0.1, 0.9, 0.9), reprocess=False)
        e.set_source_flip(horizontal=True, reprocess=False)
        e.load_config({})
        self.assertEqual(e.get_crop_norm(), (0.0, 0.0, 1.0, 1.0))
        self.assertFalse(e.flip_horizontal)
        self.assertFalse(e.flip_vertical)

    def test_payload_category_area_has_keys(self):
        area = PAINTER_CATEGORY_KEYS["area"]
        for key in ("draw_region", "crop_norm", "flip_horizontal", "flip_vertical"):
            self.assertIn(key, area)


if __name__ == "__main__":
    unittest.main()
