# -*- coding: utf-8 -*-
"""Phase 2: crop/flip must NOT call prepare_image_and_palette synchronously on the
Qt thread (that froze the UI). They set the value with reprocess=False and schedule
an off-thread preview rebuild."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from ui.services.painter_service import PainterService  # noqa: E402

_app = QApplication.instance() or QApplication([])


class CropFlipThreadingTests(unittest.TestCase):
    def _service(self):
        svc = PainterService()
        calls = {"rebuild": 0, "prepare_sync": 0}
        svc._rebuild_preview_strict = lambda: calls.__setitem__("rebuild", calls["rebuild"] + 1)
        def _boom(*a, **k):
            calls["prepare_sync"] += 1
            return False
        svc.engine.prepare_image_and_palette = _boom  # must NOT be called synchronously
        return svc, calls

    def test_crop_applied_is_non_blocking(self):
        svc, calls = self._service()
        try:
            captured = {}
            svc.engine.get_crop_norm = lambda: (0.0, 0.0, 1.0, 1.0)
            def _set_crop(crop, *, reprocess=True):
                captured["crop"] = (crop, reprocess)
            svc.engine.set_crop_norm = _set_crop
            svc._on_crop_applied(0.25, 0.0, 1.0, 1.0)
            self.assertIn("crop", captured)
            self.assertFalse(captured["crop"][1], "set_crop_norm must use reprocess=False")
            self.assertEqual(calls["prepare_sync"], 0, "prepare must NOT run synchronously")
            self.assertEqual(calls["rebuild"], 1, "off-thread preview rebuild must be scheduled")
        finally:
            svc.shutdown(); svc.deleteLater(); _app.processEvents()

    def test_crop_reset_is_non_blocking(self):
        svc, calls = self._service()
        try:
            captured = {}
            svc.engine.reset_crop = lambda *, reprocess=True: captured.__setitem__("reprocess", reprocess)
            svc._on_crop_reset()
            self.assertFalse(captured["reprocess"])
            self.assertEqual(calls["prepare_sync"], 0)
            self.assertEqual(calls["rebuild"], 1)
        finally:
            svc.shutdown(); svc.deleteLater(); _app.processEvents()

    def test_flip_changed_is_non_blocking(self):
        svc, calls = self._service()
        try:
            captured = {}
            def _flip(*, horizontal=None, vertical=None, reprocess=True):
                captured["args"] = (horizontal, vertical, reprocess)
            svc.engine.set_source_flip = _flip
            svc._on_flip_changed(True, False)
            self.assertEqual(captured["args"], (True, False, False))
            self.assertEqual(calls["prepare_sync"], 0)
            self.assertEqual(calls["rebuild"], 1)
        finally:
            svc.shutdown(); svc.deleteLater(); _app.processEvents()


if __name__ == "__main__":
    unittest.main()
