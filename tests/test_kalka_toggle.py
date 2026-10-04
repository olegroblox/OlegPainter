# -*- coding: utf-8 -*-
"""Regression: toggling the Kalka stencil (F2) must NOT drift the zoom or bake the crop.

Root cause being guarded against: capture_from_kalka writes the rendered (window-cropped)
view back into the engine as "<Калька>". If that render is fed back into Kalka on the next
toggle, Kalka re-centres/re-fits an already-cropped pixmap → zoom drifts every cycle and the
crop becomes destructive (you can't drag it back out). Kalka must keep its ORIGINAL image and
preserve the user's transform across show/hide; only a genuinely new source reloads it."""
import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtTest import QTest  # noqa: E402
from PIL import Image  # noqa: E402

_app = QApplication.instance() or QApplication([])

from application.controller import ApplicationController  # noqa: E402
from ui.services.painter_service import PainterService  # noqa: E402
from ui.services.stencil_sync import USE_KALKA_OVERLAY  # noqa: E402
from ui.kalka import win_native  # noqa: E402
from engine.olegpainter.drawing_events import DrawingEvent, DrawingPhase  # noqa: E402


@unittest.skipUnless(USE_KALKA_OVERLAY, "Kalka overlay disabled")
class KalkaToggleTests(unittest.TestCase):
    def setUp(self):
        self.svc = PainterService()
        self.controller = ApplicationController(self.svc)
        self.desktop = self.controller.enable_desktop()
        self.k = self.desktop.kalka_overlay

    def tearDown(self):
        try:
            self.controller.close(save=False)
            self.controller.deleteLater()
            self.svc.deleteLater()
        finally:
            _app.processEvents()

    def _set_engine_source(self, size, path):
        self.svc.engine.source_pil_image = Image.new("RGBA", size, (40, 80, 120, 255))
        self.svc.engine.IMAGE_PATH = path

    def test_kalka_render_is_never_fed_back(self):
        # Real original loaded.
        self._set_engine_source((300, 200), "C:/fake/original.png")
        self.assertTrue(self.desktop._load_current_image_into_kalka())
        self.assertEqual(self.k._view.item().pixmap_size(), (300, 200))

        # Engine source becomes our own rendered crop (what capture does).
        self._set_engine_source((640, 520), "<Калька>")
        # A reload attempt must be ignored → Kalka keeps the original full image.
        self.assertFalse(self.desktop._load_current_image_into_kalka())
        self.assertEqual(self.k._view.item().pixmap_size(), (300, 200))

    def test_toggle_preserves_image_and_transform(self):
        self._set_engine_source((300, 200), "C:/fake/original.png")
        self.desktop.toggle_stencil(); _app.processEvents()       # show (loads original)
        self.assertTrue(self.k.has_image())
        item = self.k._view.item()
        size0 = item.pixmap_size()
        # User zooms/moves the image.
        item.scale_x = item.scale_y = 1.7
        item.apply_transform()

        # Capture overwrites the engine source with a 640x520 render.
        self._set_engine_source((640, 520), "<Калька>")

        # Toggle off/on several times — must stay rock-stable (no drift, no re-crop).
        for _ in range(4):
            self.desktop.toggle_stencil(); _app.processEvents()   # hide
            self.desktop.toggle_stencil(); _app.processEvents()   # show
        item = self.k._view.item()
        self.assertEqual(item.pixmap_size(), size0, "original image must be preserved")
        self.assertAlmostEqual(item.scale_x, 1.7, places=5)
        self.assertAlmostEqual(item.scale_y, 1.7, places=5)

    def test_altf2_opens_editor_directly(self):
        # Alt+F2 (edit_stencil hotkey) must route to the Kalka editor, not the legacy
        # HUD layout-edit — and open it straight into edit mode (no hunting for a button).
        route = self.svc._global_action_map().get("edit_stencil")
        self.assertEqual(getattr(route, "__name__", ""), "edit_stencil")
        self._set_engine_source((300, 200), "C:/fake/original.png")
        self.assertFalse(self.k.isVisible())
        route()
        _app.processEvents()
        self.assertTrue(self.k.isVisible())
        self.assertTrue(self.k.is_edit_mode(), "Alt+F2 opens the editor directly")
        route()
        _app.processEvents()
        self.assertFalse(self.k.is_edit_mode(), "Alt+F2 again exits edit")

    def test_new_image_reloads_and_resets(self):
        self._set_engine_source((300, 200), "C:/fake/original.png")
        self.assertTrue(self.desktop._load_current_image_into_kalka())
        self.k._view.item().scale_x = 2.0  # user zoom on the OLD image

        # A genuinely different image is loaded → reload + fresh transform.
        self._set_engine_source((128, 256), "C:/fake/other.png")
        self.assertTrue(self.desktop._load_current_image_into_kalka())
        item = self.k._view.item()
        self.assertEqual(item.pixmap_size(), (128, 256))
        self.assertAlmostEqual(item.scale_x, 1.0, places=5, msg="new image resets transform")

    def test_f2_does_not_capture_or_reprepare_during_drawing(self):
        self._set_engine_source((300, 200), "<Изображение из буфера>")
        self.svc.engine.apply_viewport_state(draw_region_desktop_px=(40, 50, 300, 200))
        self.svc._on_drawing_event(DrawingEvent(0, DrawingPhase.RUNNING))
        capture = Mock(wraps=self.svc.capture_from_kalka)
        rebuild = Mock()
        self.svc.capture_from_kalka = capture
        self.svc._rebuild_preview_strict = rebuild
        route = self.svc._global_action_map()["toggle_stencil"]
        before = self.svc.engine.draw_region
        for _ in range(3):
            route()
            QTest.qWait(400)  # include both former 180/350 ms deferred callbacks
            self.assertTrue(self.k.isVisible())
            self.assertTrue(self.k.testAttribute(Qt.WA_ShowWithoutActivating))
            self.assertTrue(self.k.testAttribute(Qt.WA_TransparentForMouseEvents))
            route()
            QTest.qWait(20)
            self.assertFalse(self.k.isVisible())
        capture.assert_not_called()
        rebuild.assert_not_called()
        self.assertEqual(self.svc.engine.draw_region, before)
        self.assertEqual(self.svc.drawing_phase, DrawingPhase.RUNNING)
        self.assertEqual(self.svc._drawing_run_id, 0)
        self.svc._on_drawing_event(DrawingEvent(0, DrawingPhase.STOPPED))

    def test_passive_geometry_events_do_not_commit_but_editor_does(self):
        self._set_engine_source((300, 200), "C:/fake/original.png")
        capture = Mock()
        self.svc.capture_from_kalka = capture
        self.desktop.toggle_stencil()
        QTest.qWait(400)
        self.k.move(80, 90)
        self.k.request_save()
        QTest.qWait(220)
        capture.assert_not_called()
        self.desktop.edit_stencil()
        self.assertFalse(self.k.testAttribute(Qt.WA_ShowWithoutActivating))
        self.assertFalse(self.k.testAttribute(Qt.WA_TransparentForMouseEvents))
        self.k.move(100, 110)
        self.k.request_save()  # canvas/window gesture commits after changing geometry
        # 180 ms debounce: a loaded full suite may deliver the timer later than 220 ms.
        for _ in range(100):
            if capture.called:
                break
            QTest.qWait(20)
        capture.assert_called_with(self.k)


class NativeStencilStyleTests(unittest.TestCase):
    def test_passthrough_disables_activation_and_editor_restores_it(self):
        original = win_native.WS_EX_TOOLWINDOW
        with patch.object(win_native, "IS_WINDOWS", True), \
                patch.object(win_native, "_get_exstyle", return_value=original), \
                patch.object(win_native, "_set_exstyle") as write:
            win_native.set_click_through(123, True)
            passive = write.call_args.args[1]
            self.assertTrue(passive & win_native.WS_EX_NOACTIVATE)
            self.assertTrue(passive & win_native.WS_EX_TRANSPARENT)
            self.assertTrue(passive & original)
        with patch.object(win_native, "IS_WINDOWS", True), \
                patch.object(win_native, "_get_exstyle", return_value=passive), \
                patch.object(win_native, "_set_exstyle") as write:
            win_native.set_click_through(123, False)
            editable = write.call_args.args[1]
            self.assertFalse(editable & win_native.WS_EX_NOACTIVATE)
            self.assertFalse(editable & win_native.WS_EX_TRANSPARENT)
            self.assertTrue(editable & win_native.WS_EX_LAYERED)
            self.assertTrue(editable & original)


if __name__ == "__main__":
    unittest.main()
