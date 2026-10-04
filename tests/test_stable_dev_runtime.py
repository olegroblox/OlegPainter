from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from app_paths import get_app_paths
from ui.helpers.config_store import ConfigStore
from ui.services.painter_service import PainterService


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _drain_events(app: QApplication, cycles: int = 6) -> None:
    for _ in range(max(1, cycles)):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.sendPostedEvents(None, 0)
        app.processEvents()


class StableDevRuntimeTests(unittest.TestCase):

    def test_config_store_migrates_legacy_ai_payload_and_keeps_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            base_dir = Path(tmp_dir) / "configs"
            base_dir.mkdir(parents=True, exist_ok=True)
            session_path = base_dir / "__autosave.json"
            legacy_doc = {
                "meta": {
                    "name": "Last Session",
                    "slug": "__autosave",
                    "categories": ["drawing"],
                    "created_at": "2026-02-01T00:00:00",
                    "updated_at": "2026-02-01T00:00:00",
                },
                "payload": {
                    "painter": {
                        "remove_background": True,
                        "alpha_threshold": 12,
                        "use_ai_background": True,
                        "bg_ai_threshold": 0.42,
                        "bg_ai_model_path": "u2netp.onnx",
                        "ai_upscale_model_path": "realesrgan-x4plus-anime",
                        "ai": {
                            "use_gpu": True,
                            "bg": {"model": "bg.u2netp"},
                        },
                    }
                },
            }
            session_path.write_text(json.dumps(legacy_doc, ensure_ascii=False, indent=2), encoding="utf-8")

            store = ConfigStore(base_dir=base_dir)
            loaded = store.load("__autosave")

            painter = loaded["payload"]["painter"]
            self.assertTrue(painter["remove_background"])
            self.assertEqual(painter["alpha_threshold"], 12)
            self.assertTrue(painter["background_removal_enabled"])
            self.assertEqual(painter["background_removal_mode"], "corner")
            self.assertEqual(painter["background_alpha_threshold"], 12)
            self.assertNotIn("ai", painter)
            self.assertNotIn("use_ai_background", painter)
            self.assertNotIn("bg_ai_threshold", painter)
            self.assertNotIn("bg_ai_model_path", painter)
            self.assertNotIn("ai_upscale_model_path", painter)

            backups = list(base_dir.glob("__autosave.pre_ai_cleanup_*.json.bak"))
            self.assertEqual(len(backups), 1)
            on_disk = json.loads(session_path.read_text(encoding="utf-8"))
            self.assertNotIn("ai", on_disk["payload"]["painter"])
            self.assertEqual(on_disk["meta"]["version"], 3)

    def test_path_resolution_does_not_depend_on_cwd(self) -> None:
        paths = get_app_paths()
        with tempfile.TemporaryDirectory() as tmp_dir:
            previous = Path.cwd()
            os.chdir(tmp_dir)
            try:
                # Exercise the default app path independently of the suite's sandbox.
                with patch.dict(os.environ, {"OLEGPAINTER_CONFIG_DIR": ""}):
                    store = ConfigStore()
                self.assertEqual(store.base_dir.resolve(), paths.configs_root.resolve())
                self.assertTrue((paths.assets_root / "icons").is_dir())
            finally:
                os.chdir(previous)
