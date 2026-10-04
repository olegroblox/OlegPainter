# -*- coding: utf-8 -*-
"""Tests for the Kalka stencil overlay bridge (window→draw_region, visible→source)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtGui import QImage, QPixmap  # noqa: E402

from ui.widgets.kalka_stencil import KalkaStencilOverlay  # noqa: E402
from ui.kalka.canvas_view import CanvasView  # noqa: E402

_app = QApplication.instance() or QApplication([])


class KalkaStencilTests(unittest.TestCase):
    def _overlay(self):
        o = KalkaStencilOverlay()
        o.setGeometry(120, 90, 400, 300)
        return o

    def test_set_source_and_has_image(self):
        o = self._overlay()
        img = QImage(200, 150, QImage.Format_ARGB32)
        img.fill(0xFF3366CC)
        self.assertTrue(o.set_source_pixmap(QPixmap.fromImage(img)))
        self.assertTrue(o.has_image())

    def test_get_stencil_geometry_returns_region_and_crop(self):
        o = self._overlay()
        img = QImage(200, 100, QImage.Format_ARGB32)
        img.fill(0xFF3366CC)
        o.set_source_pixmap(QPixmap.fromImage(img))
        o.set_edit_mode(False, capture_on_exit=False)
        o.show()
        geom = o.get_stencil_geometry()
        self.assertIsNotNone(geom)
        region, crop = geom
        self.assertEqual(len(region), 4)
        self.assertEqual(len(crop), 4)
        for c in crop:
            self.assertGreaterEqual(c, 0.0)
            self.assertLessEqual(c, 1.0)
        # Self-consistency: on-screen size == visible source fraction × image size (zoom 1).
        l, t, r, b = crop
        _x, _y, w, h = region
        self.assertAlmostEqual(w, (r - l) * 200, delta=2)
        self.assertAlmostEqual(h, (b - t) * 100, delta=2)

    def test_edit_mode_toggles_passthrough(self):
        o = self._overlay()
        img = QImage(80, 60, QImage.Format_ARGB32); img.fill(0xFF3366CC)
        o.set_source_pixmap(QPixmap.fromImage(img))
        o.set_edit_mode(False)
        self.assertFalse(o.is_edit_mode())
        self.assertTrue(o._state.window.click_through, "passive must be click-through")
        o.set_edit_mode(True)
        self.assertTrue(o.is_edit_mode())
        self.assertFalse(o._state.window.click_through, "edit must be interactive")

    def test_stencil_opacity_and_border_controls(self):
        o = self._overlay()
        img = QImage(80, 60, QImage.Format_ARGB32); img.fill(0xFF3366CC)
        o.set_source_pixmap(QPixmap.fromImage(img))
        o.set_stencil_opacity(0.3)
        self.assertAlmostEqual(o._stencil_opacity, 0.3, places=3)
        self.assertAlmostEqual(o._view.item().opacity(), 0.3, places=3)
        # clamped to a usable floor
        o.set_stencil_opacity(0.01)
        self.assertGreaterEqual(o._stencil_opacity, 0.1)
        # border toggle
        o.set_border_visible(True)
        self.assertTrue(o._state.window.show_border)
        o.set_border_visible(False)
        self.assertFalse(o._state.window.show_border)
        # toolbar reflects state when entering edit
        o.show()
        o.set_stencil_opacity(0.7)
        o.set_edit_mode(True)
        self.assertEqual(o._edit_toolbar.opacity.value(), 70)

    def test_app_shell_is_stripped(self):
        o = self._overlay()
        o.show()
        self.assertFalse(o._tray.isVisible(), "no system tray")
        self.assertFalse(o._toolbar.isVisible(), "no floating toolbar")
        self.assertFalse(getattr(o._view, "_rotate_enabled", True), "rotation disabled")

    def test_f1_placement_snaps_window_and_enters_edit(self):
        from PySide6.QtCore import QRect, QPoint

        class _Snap:
            logical_rect = (0, 0, 1000, 800)
            screen_name = device_name = monitor_id = "FAKE"

        o = self._overlay()
        img = QImage(200, 100, QImage.Format_ARGB32); img.fill(0xFFCC3333)
        o.set_source_pixmap(QPixmap.fromImage(img))
        o.begin_area_placement(_Snap())
        self.assertTrue(o._placement_active)
        self.assertTrue(o._view._placement_mode)
        self.assertFalse(o._view.item().isVisible(), "source hidden during placement")
        self.assertEqual((o.geometry().width(), o.geometry().height()), (1000, 800))
        # Finish with a chosen rect (global logical px).
        v = o._view
        v._rb_start = QPoint(100, 80)
        g_tl = v.viewport().mapToGlobal(QPoint(100, 80))
        g_br = v.viewport().mapToGlobal(QPoint(400, 280))
        v._finish_placement(QRect(g_tl, g_br))
        self.assertFalse(o._placement_active)
        self.assertTrue(o.is_edit_mode(), "placement ends in edit mode for fine-tuning")
        self.assertTrue(o._view.item().isVisible())
        self.assertIsNotNone(o.get_stencil_geometry())

    def test_f1_placement_cancel_restores(self):
        class _Snap:
            logical_rect = (0, 0, 1000, 800)
            screen_name = device_name = monitor_id = "FAKE"

        o = self._overlay()
        img = QImage(80, 60, QImage.Format_ARGB32); img.fill(0xFFCC3333)
        o.set_source_pixmap(QPixmap.fromImage(img))
        before = (o.geometry().width(), o.geometry().height())
        o.begin_area_placement(_Snap())
        o._view.cancel_placement()
        self.assertFalse(o._placement_active)
        self.assertFalse(o._view._placement_mode)
        self.assertEqual((o.geometry().width(), o.geometry().height()), before)

    def test_done_button_finishes_edit(self):
        o = self._overlay()
        img = QImage(80, 60, QImage.Format_ARGB32); img.fill(0xFFCC3333)
        o.set_source_pixmap(QPixmap.fromImage(img))
        o.set_edit_mode(False, capture_on_exit=False)
        o.show()
        self.assertFalse(o._done_btn.isVisible(), "no Done button in passive mode")
        o.set_edit_mode(True)
        self.assertTrue(o._done_btn.isVisible(), "Done button shown in edit mode")
        self.assertTrue(o._view._edit_hint)
        o._done_btn.click()
        self.assertFalse(o.is_edit_mode(), "Done finishes editing → drawable ghost")
        self.assertFalse(o._done_btn.isVisible())
        self.assertTrue(o._state.window.click_through, "after Done the stencil is click-through")

    def test_does_not_keep_app_alive(self):
        from PySide6.QtCore import Qt
        o = self._overlay()
        self.assertFalse(o.testAttribute(Qt.WA_QuitOnClose),
                         "stencil must not keep the app alive after the main window closes")

    def test_passive_shows_quantized_preview_edit_shows_source(self):
        o = self._overlay()
        src = QImage(200, 100, QImage.Format_ARGB32); src.fill(0xFFCC3333)
        o.set_source_pixmap(QPixmap.fromImage(src))
        o.set_edit_mode(False, capture_on_exit=False)
        o.show()
        prev = QImage(180, 100, QImage.Format_ARGB32); prev.fill(0xFF1144CC)
        self.assertTrue(o.show_quantized_preview(prev), "passive must accept the preview")
        pi = o._view._preview_item
        self.assertTrue(pi.isVisible(), "preview item shown in passive")
        self.assertFalse(o._view.item().isVisible(), "raw source hidden in passive")
        # Edit mode: raw source back, preview hidden; preview is rejected while editing.
        o.set_edit_mode(True)
        self.assertFalse(pi.isVisible())
        self.assertTrue(o._view.item().isVisible())
        self.assertFalse(o.show_quantized_preview(prev))

    def test_result_arriving_while_hidden_or_editing_shows_once_passive(self):
        # Owner 2026-10-01: after a snapshot and F1 the passive stencil showed the raw
        # source, because the prepared picture arrived while it was hidden or editing.
        o = self._overlay()
        src = QImage(200, 100, QImage.Format_ARGB32); src.fill(0xFFCC3333)
        o.set_source_pixmap(QPixmap.fromImage(src))
        prev = QImage(180, 100, QImage.Format_ARGB32); prev.fill(0xFF1144CC)
        o.set_edit_mode(True)
        o.show()
        self.assertFalse(o.show_quantized_preview(prev), "editing shows the source")
        o.set_edit_mode(False)
        self.assertTrue(o._view._preview_item.isVisible(), "leaving edit shows the result")
        self.assertFalse(o._view.item().isVisible())
        o.hide()
        o.set_source_pixmap(QPixmap.fromImage(src))       # a new picture: the old result is gone
        o.show()
        self.assertFalse(o._view._preview_item.isVisible())
        self.assertTrue(o._view.item().isVisible())

    def test_content_changed_is_debounced(self):
        o = self._overlay()
        o.request_save()
        self.assertFalse(o._content_timer.isActive(), "passive view changes must not commit")
        o.set_edit_mode(True)
        o.request_save()
        self.assertTrue(o._content_timer.isActive(), "contentChanged must be scheduled (debounced)")

    def test_quit_app_hides_not_quits(self):
        o = self._overlay()
        o.show()
        o.quit_app()
        self.assertFalse(o.isVisible())

    def test_canvas_load_pixmap(self):
        cv = CanvasView()
        img = QImage(64, 48, QImage.Format_ARGB32)
        img.fill(0xFFAABBCC)
        self.assertTrue(cv.load_pixmap(QPixmap.fromImage(img)))
        self.assertEqual(cv.item().pixmap_size(), (64, 48))

    def test_zone_mode_swaps_controls(self):
        # Redesigned bar: zone brush tools stay hidden until the "Зона" toggle is on,
        # and they replace the normal middle group (opacity/show/order) rather than adding
        # a permanent second row.
        o = self._overlay()
        bar = o._edit_toolbar
        self.assertTrue(bar.mask_brush.isHidden(), "zone tools hidden until zone mode")
        self.assertTrue(bar.mask_clear.isHidden())
        self.assertFalse(bar.opacity_btn.isHidden(), "appearance is reachable in normal mode")
        self.assertTrue(bar._opacity_pop.isAncestorOf(bar.border))
        self.assertFalse(bar.order_btn.isHidden())
        bar.mask.setChecked(True)
        self.assertFalse(bar.mask_brush.isHidden(), "zone tools revealed in zone mode")
        self.assertTrue(bar.opacity_btn.isHidden(), "appearance is swapped out in mask mode")
        self.assertFalse(bar._opacity_pop.isVisible())
        self.assertTrue(bar.order_btn.isHidden())
        bar.mask.setChecked(False)
        self.assertTrue(bar.mask_brush.isHidden(), "zone tools hidden again")
        self.assertFalse(bar.opacity_btn.isHidden())

    def test_secondary_params_live_in_popovers(self):
        # Opacity / semantic order are reachable via popovers (not raw sliders in the bar),
        # but the widgets external code syncs still exist under the same names.
        o = self._overlay()
        bar = o._edit_toolbar
        self.assertTrue(hasattr(bar, "opacity") and hasattr(bar, "threshold"))
        self.assertTrue(hasattr(bar, "semantic_mode") and hasattr(bar, "thr_value"))
        self.assertTrue(bar._opacity_pop.isAncestorOf(bar.opacity), "opacity slider lives in its popover")
        self.assertTrue(bar._order_pop.isAncestorOf(bar.threshold), "threshold lives in the order popover")


if __name__ == "__main__":
    unittest.main()
