# -*- coding: utf-8 -*-
"""Phase C / Variant A: PainterService.capture_from_kalka maps the Kalka stencil's geometry
to the engine's native draw_region (where) + crop_norm (which part of the source), WITHOUT
touching the engine's real source image or IMAGE_PATH. Re-prepare runs off the Qt thread."""
import os
import sys
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from ui.services.painter_service import PainterService  # noqa: E402

_app = QApplication.instance() or QApplication([])


class _FakeKalka:
    """Duck-typed stand-in for KalkaStencilOverlay (no real window needed)."""
    def __init__(self, geom):
        self._geom = geom

    def get_stencil_geometry(self):
        return self._geom


class KalkaCaptureTests(unittest.TestCase):
    def _service(self):
        svc = PainterService()
        calls = {"rebuild": 0, "prepare_sync": 0, "set_image": 0}
        svc._rebuild_preview_strict = lambda: calls.__setitem__("rebuild", calls["rebuild"] + 1)
        def _boom(*a, **k):
            calls["prepare_sync"] += 1
            return False
        svc.engine.prepare_image_and_palette = _boom  # must NOT run synchronously
        svc.engine.set_image_from_pil = lambda *a, **k: calls.__setitem__("set_image", calls["set_image"] + 1)
        return svc, calls

    def test_capture_sets_region_and_crop_without_touching_source(self):
        svc, calls = self._service()
        try:
            captured = {}
            svc.engine.set_crop_norm = lambda crop, *, reprocess=True: captured.__setitem__(
                "crop", (tuple(crop), reprocess)
            )
            svc.engine.apply_viewport_state = (
                lambda *, draw_region_desktop_px=None, **k: captured.__setitem__(
                    "region", draw_region_desktop_px
                )
            )
            # geometry: draw_region (100,50,320,240), crop (left,top,right,bottom)
            k = _FakeKalka(((100, 50, 320, 240), (0.1, 0.2, 0.8, 0.9)))
            svc.capture_from_kalka(k)

            self.assertEqual(captured["region"], (100, 50, 320, 240))
            self.assertEqual(captured["crop"][0], (0.1, 0.2, 0.8, 0.9))
            self.assertFalse(captured["crop"][1], "set_crop_norm must use reprocess=False")
            self.assertEqual(calls["set_image"], 0, "engine source must NOT be overwritten")
            self.assertEqual(calls["prepare_sync"], 0, "prepare must NOT run synchronously")
            self.assertEqual(calls["rebuild"], 1, "off-thread preview rebuild must be scheduled")
        finally:
            svc.shutdown(); svc.deleteLater(); _app.processEvents()

    def test_capture_ignores_empty_geometry(self):
        svc, calls = self._service()
        try:
            touched = {"crop": 0}
            svc.engine.set_crop_norm = lambda *a, **k: touched.__setitem__("crop", touched["crop"] + 1)
            svc.capture_from_kalka(_FakeKalka(None))   # no overlap / no image
            self.assertEqual(touched["crop"], 0)
            self.assertEqual(calls["rebuild"], 0)
        finally:
            svc.shutdown(); svc.deleteLater(); _app.processEvents()

    def test_capture_none_is_safe(self):
        svc, calls = self._service()
        try:
            svc.capture_from_kalka(None)  # must not raise
            self.assertEqual(calls["rebuild"], 0)
        finally:
            svc.shutdown(); svc.deleteLater(); _app.processEvents()

    def test_late_editor_signal_cannot_change_active_worker_data(self):
        svc, calls = self._service()
        try:
            k = Mock()
            for busy in ("drawing", "paused", "stopping", "starting", "thread_alive"):
                with self.subTest(busy=busy):
                    with patch.object(svc, "_is_drawing", busy in ("drawing", "paused")), \
                            patch.object(svc, "_is_paused", busy == "paused"), \
                            patch.object(svc, "_stop_pending", busy == "stopping"), \
                            patch.object(svc, "_thread_is_running", return_value=busy == "starting"), \
                            patch.object(svc.engine, "drawing_thread", Mock(is_alive=lambda: busy == "thread_alive")):
                        svc.capture_from_kalka(k)
            k.get_stencil_geometry.assert_not_called()
            self.assertEqual(calls["rebuild"], 0)
        finally:
            svc.shutdown(); svc.deleteLater(); _app.processEvents()


if __name__ == "__main__":
    unittest.main()
