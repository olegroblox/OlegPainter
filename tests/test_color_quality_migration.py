# -*- coding: utf-8 -*-
"""OKLab-by-default + the one-time legacy->OKLab migration of saved configs.
A config that persisted the old "legacy_rgb" default is bumped to OKLab once;
a config that deliberately stored legacy (carrying the migration marker) is
left untouched. Covers both the engine and the service config paths."""
from __future__ import annotations

import os
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from engine.olegpainter.core import OlegPainter
from ui.services.painter_service import PainterService


def _qt_app() -> QApplication:
    return QApplication.instance() or QApplication([])


class EngineColorDefaultTests(unittest.TestCase):
    def test_fresh_default_is_oklab(self):
        e = OlegPainter(status_callback=lambda *_a, **_k: None)
        self.assertEqual(e.prep_color_space, "oklab")

    def test_legacy_without_marker_migrates_once(self):
        e = OlegPainter(status_callback=lambda *_a, **_k: None)
        e.load_config({"prep_color_space": "legacy_rgb"})            # old preset
        self.assertEqual(e.prep_color_space, "oklab")
        self.assertTrue(e.color_quality_migrated_v1)
        # the marker now persists in the config
        self.assertTrue(e.get_config()["color_quality_migrated_v1"])

    def test_legacy_with_marker_is_respected(self):
        e = OlegPainter(status_callback=lambda *_a, **_k: None)
        e.load_config({"prep_color_space": "legacy_rgb", "color_quality_migrated_v1": True})
        self.assertEqual(e.prep_color_space, "legacy_rgb")          # deliberate choice kept

    def test_oklab_roundtrip_unaffected(self):
        e = OlegPainter(status_callback=lambda *_a, **_k: None)
        e.load_config({"prep_color_space": "cielab"})
        self.assertEqual(e.prep_color_space, "cielab")              # explicit non-legacy kept


class ServiceColorMigrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = _qt_app()

    def _service(self):
        return PainterService()

    def test_service_fresh_default_oklab(self):
        svc = self._service()
        try:
            self.assertEqual(svc.engine.prep_color_space, "oklab")
        finally:
            svc.shutdown()

    def test_service_migrates_legacy_preset(self):
        svc = self._service()
        try:
            svc.load_config({"prep_color_space": "legacy_rgb"})
            # both the engine AND the service cache (what gets saved) agree on oklab
            self.assertEqual(svc.engine.prep_color_space, "oklab")
            self.assertEqual(svc.snapshot_painter_config()["prep_color_space"], "oklab")
            self.assertTrue(svc.snapshot_painter_config()["color_quality_migrated_v1"])
        finally:
            svc.shutdown()

    def test_service_respects_deliberate_legacy(self):
        svc = self._service()
        try:
            svc.load_config({"prep_color_space": "legacy_rgb", "color_quality_migrated_v1": True})
            self.assertEqual(svc.engine.prep_color_space, "legacy_rgb")
            self.assertEqual(svc.snapshot_painter_config()["prep_color_space"], "legacy_rgb")
        finally:
            svc.shutdown()


if __name__ == "__main__":
    unittest.main()
