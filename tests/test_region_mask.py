"""Custom freeform draw-zone ("кисть-зона") tests.

Covers the engine-side gating (drawing is restricted to the painted shape) and the
Kalka CanvasView painting bridge (paint → boolean array → save/clear/load).
"""
from __future__ import annotations

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import unittest

import numpy as np

from engine.olegpainter.core import OlegPainter


class RegionMaskEngineTests(unittest.TestCase):
    def _engine_with_clusters(self, h=4, w=4):
        e = OlegPainter(status_callback=lambda *a, **k: None)
        e.cluster_map = np.zeros((h, w), dtype=int)   # one drawable colour everywhere
        e._background_cluster_id = -1
        e._ensure_drawn_mask_shape(force=True)
        return e

    def test_no_mask_is_fully_drawable(self):
        e = self._engine_with_clusters()
        # drawn_mask False everywhere → every cell is still to be drawn
        self.assertEqual(int(np.count_nonzero(~e.drawn_mask)), 16)

    def test_mask_restricts_to_shape(self):
        e = self._engine_with_clusters()
        mask = np.zeros((4, 4), dtype=bool)
        mask[:2, :2] = True
        self.assertTrue(e.set_region_mask(mask))
        undrawn = ~e.drawn_mask
        self.assertEqual(int(np.count_nonzero(undrawn)), 4)
        self.assertTrue(np.array_equal(undrawn, mask))

    def test_mask_resampled_from_other_resolution(self):
        e = self._engine_with_clusters(h=8, w=8)
        coarse = np.zeros((2, 2), dtype=bool)
        coarse[0, 0] = True                     # top-left quadrant only
        e.set_region_mask(coarse)
        undrawn = ~e.drawn_mask
        # nearest-neighbour upscale of a 2x2 to 8x8 → top-left 4x4 block (16 cells)
        self.assertEqual(int(np.count_nonzero(undrawn)), 16)
        self.assertTrue(undrawn[:4, :4].all())
        self.assertFalse(undrawn[4:, 4:].any())

    def test_clear_restores_full_rectangle(self):
        e = self._engine_with_clusters()
        e.set_region_mask(np.ones((4, 4), dtype=bool))
        e.set_region_mask(None)
        e._ensure_drawn_mask_shape(force=True)
        self.assertEqual(int(np.count_nonzero(~e.drawn_mask)), 16)

    def test_rgba_mask_uses_alpha_channel(self):
        e = self._engine_with_clusters()
        rgba = np.zeros((4, 4, 4), dtype=np.uint8)
        rgba[:2, :2, 3] = 255                   # alpha marks the active zone
        e.set_region_mask(rgba)
        self.assertEqual(int(np.count_nonzero(~e.drawn_mask)), 4)


class RegionMaskCanvasTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        cls.app = QApplication.instance() or QApplication([])

    def _canvas_with_image(self):
        from PySide6.QtGui import QPixmap, QColor
        from ui.kalka.canvas_view import CanvasView
        v = CanvasView()
        v.resize(200, 200)
        pix = QPixmap(120, 120)
        pix.fill(QColor("white"))
        v.load_pixmap(pix)
        v.fit_image_to_viewport()
        v.show()
        self.app.processEvents()
        return v

    def test_paint_produces_mask_array(self):
        from PySide6.QtCore import QPointF
        v = self._canvas_with_image()
        self.assertIsNone(v.mask_region_array())     # nothing painted yet
        v.set_mask_mode(True)
        v.set_mask_brush(28)
        for x in range(40, 160, 5):
            v._paint_mask_segment(QPointF(x - 5, 100), QPointF(x, 100))
        m = v.mask_region_array()
        self.assertIsNotNone(m)
        self.assertEqual(m.dtype, np.bool_)
        self.assertGreater(int(np.count_nonzero(m)), 0)

    def test_clear_and_roundtrip(self):
        from PySide6.QtCore import QPointF
        v = self._canvas_with_image()
        v.set_mask_mode(True)
        v.set_mask_brush(40)
        v._paint_mask_segment(QPointF(60, 60), QPointF(140, 140))
        self.assertTrue(v.has_mask())
        qimg = v.export_mask_qimage()
        self.assertIsNotNone(qimg)
        self.assertFalse(qimg.isNull())
        v.clear_mask()
        self.assertFalse(v.has_mask())
        self.assertIsNone(v.mask_region_array())
        self.assertTrue(v.import_mask_qimage(qimg))
        self.assertTrue(v.has_mask())
        self.assertIsNotNone(v.mask_region_array())

    def test_erase_removes_paint(self):
        from PySide6.QtCore import QPointF
        v = self._canvas_with_image()
        v.set_mask_mode(True)
        v.set_mask_brush(60)
        v._paint_mask_segment(QPointF(100, 100), QPointF(100, 100))
        self.assertIsNotNone(v.mask_region_array())
        v.set_mask_erase(True)
        # erase over the whole visible area
        for x in range(0, 200, 8):
            for y in range(0, 200, 8):
                v._paint_mask_segment(QPointF(x, y), QPointF(x, y))
        # everything erased → no active cells
        self.assertIsNone(v.mask_region_array())


if __name__ == "__main__":
    unittest.main()
