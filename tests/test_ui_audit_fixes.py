# -*- coding: utf-8 -*-
"""Regression tests for the UI audit fixes (segmented, number_input, palette rotation)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from ui.services.painter_service import PainterService  # noqa: E402


class PaletteRotationServiceTests(unittest.TestCase):
    def test_set_palette_rotation_direction(self):
        svc = PainterService()
        try:
            svc.set_palette_rotation_direction("ccw")
            self.assertEqual(svc.engine.palette_rotation_direction_calib, "ccw")
            svc.set_palette_rotation_direction("cw")
            self.assertEqual(svc.engine.palette_rotation_direction_calib, "cw")
            svc.set_palette_rotation_direction("nonsense")   # anything not 'ccw' → 'cw'
            self.assertEqual(svc.engine.palette_rotation_direction_calib, "cw")
        finally:
            svc.shutdown(); svc.deleteLater(); _app.processEvents()


class PreflightCorrectiveTests(unittest.TestCase):
    def test_failed_start_emits_corrective_stopped(self):
        # A fresh service has no image/area → preflight rejects the start. It must emit a
        # corrective drawingStateChanged so an optimistic "playing" button resyncs.
        svc = PainterService()
        states = []
        svc.drawingStateChanged.connect(states.append)
        try:
            svc.start_pause()
            _app.processEvents()
            self.assertIn("stopped", states,
                          "rejected start must emit a corrective 'stopped' state")
        finally:
            svc.shutdown(); svc.deleteLater(); _app.processEvents()


if __name__ == "__main__":
    unittest.main()
