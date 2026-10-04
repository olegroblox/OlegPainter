# -*- coding: utf-8 -*-
"""«Планы» в трафарете (Alt+F2): движковое RGBA-превью семантических зон +
тоггл в калке, который кладёт его полупрозрачным слоем ПОВЕРХ картинки."""
from __future__ import annotations

import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from engine.olegpainter.core import OlegPainter  # noqa: E402
from ui.widgets.kalka_stencil import KalkaStencilOverlay  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _engine_with_planes():
    e = OlegPainter(status_callback=lambda *_a, **_k: None)
    cm = np.zeros((20, 20), dtype=int)          # cluster 0 = backdrop (frame-touching)
    cm[6:14, 6:14] = 1                          # cluster 1 = central object
    cm[2, 17] = 2                               # cluster 2 = detail speck
    cm[0, 0] = -1                               # a background (not-drawn) cell
    e.cluster_map = cm
    e.brush_size = 2
    return e


class SemanticPlanesPreviewTests(unittest.TestCase):
    def test_engine_builds_rgba_zone_image(self):
        e = _engine_with_planes()
        img = e.build_semantic_planes_preview()
        self.assertIsNotNone(img)
        self.assertEqual(img.mode, "RGBA")
        self.assertEqual(img.size, (40, 40))     # cluster cell -> brush block (2x)
        arr = np.asarray(img)
        # backdrop cell (1,1)-> pixel (2,2): blue tint, translucent
        self.assertEqual(tuple(arr[2, 2]), e.SEMANTIC_ZONE_COLORS[0])
        # object centre -> orange tint
        self.assertEqual(tuple(arr[20, 20]), e.SEMANTIC_ZONE_COLORS[1])
        # detail speck -> object plane too (two-zone model)
        self.assertEqual(tuple(arr[4, 34]), e.SEMANTIC_ZONE_COLORS[1])
        # background (-1) stays fully transparent
        self.assertEqual(int(arr[0, 0, 3]), 0)

    def test_engine_preview_none_without_planes(self):
        e = OlegPainter(status_callback=lambda *_a, **_k: None)
        e.cluster_map = None
        self.assertIsNone(e.build_semantic_planes_preview())
        e.cluster_map = np.zeros((8, 8), dtype=int)   # single cluster -> degenerate
        self.assertIsNone(e.build_semantic_planes_preview())

    def test_kalka_planes_toggle_overlays_and_hides(self):
        o = KalkaStencilOverlay()
        o.setGeometry(120, 90, 400, 300)
        src = QImage(200, 150, QImage.Format_ARGB32)
        src.fill(0xFF888888)
        self.assertTrue(o.set_source_pixmap(QPixmap.fromImage(src)))
        o.show()
        zones = QImage(40, 40, QImage.Format_ARGB32)
        zones.fill(0x60FF9628)
        o.planes_image_provider = lambda: zones
        o.set_edit_mode(True)

        o.set_planes_preview(True)
        zi = getattr(o._view, "_zone_item", None)
        self.assertIsNotNone(zi, "zone overlay item must exist")
        self.assertTrue(zi.isVisible())
        self.assertTrue(o._view.item().isVisible(), "source must STAY visible under zones")

        o.set_planes_preview(False)
        self.assertFalse(zi.isVisible())

        # leaving edit mode always clears the zones and the button state
        o.set_planes_preview(True)
        self.assertTrue(zi.isVisible())
        o.set_edit_mode(False, capture_on_exit=False)
        self.assertFalse(zi.isVisible())
        self.assertFalse(o._edit_toolbar.planes.isChecked())
        o.close()

    def test_kalka_planes_toggle_without_provider_is_safe(self):
        o = KalkaStencilOverlay()
        o.setGeometry(120, 90, 400, 300)
        src = QImage(100, 80, QImage.Format_ARGB32)
        src.fill(0xFF888888)
        o.set_source_pixmap(QPixmap.fromImage(src))
        o.show()
        o.set_edit_mode(True)
        o.set_planes_preview(True)               # no provider -> no crash, snaps back off
        self.assertFalse(o._edit_toolbar.planes.isChecked())
        o.close()

    def test_toolbar_semantic_controls_drive_setters_and_refresh_zones(self):
        o = KalkaStencilOverlay()
        o.setGeometry(120, 90, 400, 300)
        src = QImage(200, 150, QImage.Format_ARGB32)
        src.fill(0xFF888888)
        self.assertTrue(o.set_source_pixmap(QPixmap.fromImage(src)))
        o.show()
        state = {"mode": "bg_first", "threshold": 0.6}
        modes, thresholds = [], []
        o.semantic_state_provider = lambda: dict(state)
        o.semantic_mode_setter = modes.append
        o.semantic_threshold_setter = thresholds.append
        zones = QImage(40, 40, QImage.Format_ARGB32)
        zones.fill(0x60FF9628)
        o.planes_image_provider = lambda: zones
        refreshes = []
        orig_refresh = o._refresh_planes_preview
        o._refresh_planes_preview = lambda: (refreshes.append(1), orig_refresh())[1]

        o.set_edit_mode(True)                          # syncs the bar from the engine
        bar = o._edit_toolbar
        self.assertEqual(bar.semantic_mode.currentData(), "bg_first")
        self.assertEqual(bar.threshold.value(), 60)
        self.assertEqual(bar.thr_value.text(), "0.60")
        self.assertEqual(modes, [], "sync must not echo back into the setter")

        # mode change -> setter + live zone refresh
        for i in range(bar.semantic_mode.count()):
            if bar.semantic_mode.itemData(i) == "objects_first":
                bar.semantic_mode.setCurrentIndex(i)
                break
        self.assertEqual(modes, ["objects_first"])
        self.assertGreaterEqual(len(refreshes), 1)

        # threshold drag -> debounced commit -> setter + refresh
        bar.threshold.setValue(25)
        self.assertEqual(bar.thr_value.text(), "0.25")
        self.assertTrue(o._semantic_thr_timer.isActive())
        o._commit_toolbar_semantic_threshold()          # what the timer fires
        self.assertEqual(thresholds, [0.25])
        o.set_edit_mode(False, capture_on_exit=False)
        o.close()


if __name__ == "__main__":
    unittest.main()
