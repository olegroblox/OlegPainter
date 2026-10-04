# -*- coding: utf-8 -*-
"""Round-trip tests for stencil-overlay session persistence: opacity, border, and the
Alt+F2 zone-mask must survive export_state -> restore_state -> apply_pending_mask, and the
debounced geometry/zone change must be flushable on shutdown (flush_pending)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage, QPixmap  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from ui.widgets.kalka_stencil import KalkaStencilOverlay  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _overlay_with_image(w=200, h=100):
    o = KalkaStencilOverlay()
    o.setGeometry(120, 90, 400, 300)
    img = QImage(w, h, QImage.Format_ARGB32)
    img.fill(0xFF3366CC)
    o.set_source_pixmap(QPixmap.fromImage(img))
    o.set_edit_mode(False, capture_on_exit=False)
    o.show()
    return o


def _paint_a_zone(o):
    """Simulate a painted Alt+F2 zone by importing a non-empty mask onto the viewport."""
    mask = QImage(120, 70, QImage.Format_ARGB32)
    mask.fill(0xFFFFFFFF)   # fully opaque → a real zone
    ok = o._view.import_mask_qimage(mask)
    assert ok and o._view.has_mask()


class KalkaPersistenceTests(unittest.TestCase):
    def test_opacity_and_border_round_trip(self):
        o1 = _overlay_with_image()
        o1.set_stencil_opacity(0.35)
        o1.set_border_visible(True)
        state = o1.export_state()
        self.assertAlmostEqual(state["stencil_opacity"], 0.35, places=3)
        self.assertTrue(state["show_border"])

        o2 = _overlay_with_image()
        o2.restore_state(state)
        self.assertAlmostEqual(o2._stencil_opacity, 0.35, places=3)
        self.assertTrue(o2._state.window.show_border)

    def test_zone_mask_round_trip(self):
        o1 = _overlay_with_image()
        _paint_a_zone(o1)
        state = o1.export_state()
        self.assertIn("mask_png_b64", state, "painted zone must be exported")
        self.assertTrue(state["mask_png_b64"])

        # Fresh overlay: restore stashes the mask, applies it only after geometry is ready.
        o2 = _overlay_with_image()
        self.assertFalse(o2._view.has_mask())
        o2.restore_state(state)
        self.assertEqual(o2._pending_mask_b64, state["mask_png_b64"])
        self.assertFalse(o2._view.has_mask(), "mask must not apply before apply_pending_mask")

        applied = o2.apply_pending_mask()
        self.assertTrue(applied)
        self.assertTrue(o2._view.has_mask(), "zone must be present after apply_pending_mask")
        self.assertEqual(o2._pending_mask_b64, "", "pending mask consumed (one-shot)")

    def test_unopened_mask_is_preserved_not_dropped(self):
        """If the stencil is never opened this session, a restored-but-unapplied mask must
        survive the next export (re-reading the empty view would silently drop the zone)."""
        o1 = _overlay_with_image()
        _paint_a_zone(o1)
        saved = o1.export_state()["mask_png_b64"]

        o2 = _overlay_with_image()
        o2.restore_state({"stencil_opacity": 0.5, "mask_png_b64": saved})
        # Did NOT call apply_pending_mask (stencil never opened) → export must keep the mask.
        re_exported = o2.export_state()
        self.assertEqual(re_exported.get("mask_png_b64"), saved)

    def test_export_has_no_mask_key_when_nothing_painted(self):
        o = _overlay_with_image()
        state = o.export_state()
        self.assertNotIn("mask_png_b64", state)

    def test_flush_pending_fires_debounced_content(self):
        o = _overlay_with_image()
        o.set_edit_mode(True)
        fired = []
        o.contentChanged.connect(lambda: fired.append(True))
        o.request_save()
        self.assertTrue(o._content_timer.isActive())
        o.flush_pending()
        self.assertFalse(o._content_timer.isActive(), "timer stopped by flush")
        self.assertEqual(len(fired), 1, "pending contentChanged delivered synchronously")

    def test_flush_pending_noop_when_idle(self):
        o = _overlay_with_image()
        fired = []
        o.contentChanged.connect(lambda: fired.append(True))
        o.flush_pending()   # nothing pending → must not emit
        self.assertEqual(fired, [])


class KalkaCropRestoreTests(unittest.TestCase):
    """FIX: on restart the stencil must restore the saved crop/zoom (crop_norm), not reset to
    the whole image. apply_crop is the inverse of stencil_geometry's crop."""

    def _overlay(self, img_w=200, img_h=100):
        o = KalkaStencilOverlay()
        o.setGeometry(120, 90, 400, 300)   # window 4:3
        img = QImage(img_w, img_h, QImage.Format_ARGB32)
        img.fill(0xFF3366CC)
        o.set_source_pixmap(QPixmap.fromImage(img))
        o.set_edit_mode(False, capture_on_exit=False)
        o.show()
        return o

    def test_apply_crop_round_trips_via_stencil_geometry(self):
        o = self._overlay(200, 100)
        # crop chosen so its image-px aspect (0.4*200):(0.6*100) = 80:60 = 4:3 == window aspect,
        # so a zoomed-in stencil reproduces it exactly.
        crop = (0.3, 0.2, 0.7, 0.8)
        self.assertTrue(o._view.apply_crop(crop))
        geom = o._view.stencil_geometry()   # CanvasView: (crop, rect)
        self.assertIsNotNone(geom)
        got, _rect = geom
        for a, b in zip(got, crop):
            self.assertAlmostEqual(a, b, delta=0.03)

    def test_apply_crop_whole_image_is_fit(self):
        o = self._overlay(200, 100)
        self.assertTrue(o._view.apply_crop((0.0, 0.0, 1.0, 1.0)))
        got, _ = o._view.stencil_geometry()
        for a, b in zip(got, (0.0, 0.0, 1.0, 1.0)):
            self.assertAlmostEqual(a, b, delta=0.03)

    def test_apply_draw_region_with_crop_does_not_reset_to_whole(self):
        o = self._overlay(200, 100)
        g = o.geometry()
        region = (g.x(), g.y(), g.width(), g.height())   # offscreen: dpr=1, no monitor remap
        self.assertTrue(o.apply_draw_region(region, (0.3, 0.2, 0.7, 0.8)))
        _region, crop = o.get_stencil_geometry()   # overlay wrapper: (region, crop)
        self.assertGreater(crop[0], 0.15, "left crop preserved, not reset to 0")
        self.assertLess(crop[2], 0.85, "right crop preserved, not reset to 1")

    def test_apply_draw_region_without_crop_fits_whole(self):
        o = self._overlay(200, 100)
        g = o.geometry()
        region = (g.x(), g.y(), g.width(), g.height())
        self.assertTrue(o.apply_draw_region(region, None))
        _region, crop = o.get_stencil_geometry()
        self.assertAlmostEqual(crop[0], 0.0, delta=0.03)
        self.assertAlmostEqual(crop[2], 1.0, delta=0.03)


class KalkaMonitorClampTests(unittest.TestCase):
    """FIX #2 safety: a restored stencil rect is clamped into the monitor's logical bounds so
    it never reopens off-screen or larger than the display (monitor unplugged / DPI changed)."""

    @staticmethod
    def _snap(rect):
        import types
        return types.SimpleNamespace(logical_rect=rect, desktop_rect=rect, monitor_id="M")

    def test_in_bounds_unchanged(self):
        s = self._snap((0, 0, 1920, 1080))
        self.assertEqual(KalkaStencilOverlay._clamp_rect_to_monitor((100, 200, 800, 600), s),
                         (100, 200, 800, 600))

    def test_negative_origin_pulled_in(self):
        s = self._snap((0, 0, 1920, 1080))
        self.assertEqual(KalkaStencilOverlay._clamp_rect_to_monitor((-50, -30, 800, 600), s),
                         (0, 0, 800, 600))

    def test_overflow_fits_inside(self):
        s = self._snap((0, 0, 1920, 1080))
        self.assertEqual(KalkaStencilOverlay._clamp_rect_to_monitor((1800, 1000, 800, 600), s),
                         (1120, 480, 800, 600))

    def test_oversize_clamped_to_monitor(self):
        s = self._snap((0, 0, 1920, 1080))
        self.assertEqual(KalkaStencilOverlay._clamp_rect_to_monitor((0, 0, 3000, 2000), s),
                         (0, 0, 1920, 1080))

    def test_far_offset_pulled_onto_second_monitor(self):
        s = self._snap((1920, 0, 1280, 1024))   # second monitor to the right
        self.assertEqual(KalkaStencilOverlay._clamp_rect_to_monitor((-500, 0, 400, 300), s),
                         (1920, 0, 400, 300))

    def test_no_snap_keeps_rect_positive_size(self):
        self.assertEqual(KalkaStencilOverlay._clamp_rect_to_monitor((10, 20, 0, -5), None),
                         (10, 20, 1, 1))


if __name__ == "__main__":
    unittest.main()
