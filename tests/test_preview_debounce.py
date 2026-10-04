from __future__ import annotations

import os
import unittest
from threading import Thread
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

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


class PreviewDebounceTests(unittest.TestCase):
    def test_live_palette_capture_defers_preview_and_mouse_callback_runs_on_qt(self):
        app = _qt_app()
        service = PainterService()
        started, observed_threads = [], []
        try:
            service._PREVIEW_HEAVY_DEBOUNCE_MS = 30
            service._can_prepare_preview = lambda: True
            service._start_preview_worker = lambda strict: started.append(strict)
            service.engine.color_picking_method = "manual_palette"
            emit = service._emit_manual_palette_changed

            def observe(coords=None):
                observed_threads.append(QThread.isMainThread())
                emit(coords)

            service._emit_manual_palette_changed = observe
            with patch("engine.olegpainter.core.mouse.Listener"):
                service.start_manual_palette_capture()
            QTest.qWait(400)
            self.assertTrue(service.engine.is_capturing_manual_palette)
            self.assertEqual([], started)
            self.assertEqual("strict", service._preview_pending)
            self.assertFalse(service._preview_timer.isActive())

            service.engine.manual_palette_coords = [{"x": 10, "y": 20, "rgb": [0, 0, 0]}]
            worker = Thread(target=service._on_engine_manual_palette_changed)
            worker.start()
            worker.join(timeout=1)
            self.assertFalse(worker.is_alive())
            QTest.qWait(100)
            self.assertTrue(observed_threads)
            self.assertTrue(all(observed_threads))
            self.assertEqual([], started)
            self.assertTrue(service.engine.is_capturing_manual_palette)
            service.engine.finish_manual_palette_capture()
            service.engine.ignore_clicks_until = 0
            QTest.qWait(400)
            self.assertEqual([True], started)
            self.assertFalse(service._capture_watch_timer.isActive())
            self.assertFalse(service.engine.is_capturing_manual_palette)
        finally:
            service.shutdown()

    def test_palette_start_waits_for_running_preparation_but_finish_is_allowed(self):
        _qt_app()
        service = PainterService()
        try:
            with patch.object(service, "_thread_is_running", return_value=True):
                with patch.object(service.engine, "start_manual_palette_capture") as start:
                    service.start_manual_palette_capture()
                    service.toggle_manual_palette_capture()
                    start.assert_not_called()
                service.engine.is_capturing_manual_palette = True
                with patch.object(service, "_should_bounce_toggle", return_value=False):
                    with patch.object(service.engine, "finish_manual_palette_capture") as finish:
                        service.toggle_manual_palette_capture()
                        finish.assert_called_once()
        finally:
            service.engine.is_capturing_manual_palette = False
            service.shutdown()

    def test_heavy_prep_changes_coalesce_into_single_preview_rebuild(self) -> None:
        app = _qt_app()
        service = PainterService()
        started: list[bool] = []
        try:
            service._PREVIEW_HEAVY_DEBOUNCE_MS = 80
            service._can_prepare_preview = lambda: True
            service._start_preview_worker = lambda strict: started.append(bool(strict))

            service.set_prep_color_space("oklab")
            service.set_prep_quantization_mode("minibatch")
            service.set_prep_cleanup_mode("vectorized")
            service.set_fast_min_region_area(7)

            QTest.qWait(30)
            _drain_events(app)
            self.assertEqual([], started)
            self.assertEqual("strict", service._preview_pending)

            QTest.qWait(120)
            _drain_events(app)
            self.assertEqual([True], started)
            self.assertIsNone(service._preview_pending)
            self.assertIsNone(service._preview_pending_delay_ms)
        finally:
            service.shutdown()

    def test_manual_palette_change_triggers_preview_rebuild_in_manual_mode(self) -> None:
        app = _qt_app()
        service = PainterService()
        started: list[bool] = []
        try:
            service._PREVIEW_HEAVY_DEBOUNCE_MS = 80
            service._can_prepare_preview = lambda: True
            service._start_preview_worker = lambda strict: started.append(bool(strict))
            service.engine.color_picking_method = "manual_palette"

            service._on_engine_manual_palette_changed([{"x": 10, "y": 20, "rgb": (255, 0, 0)}])

            QTest.qWait(30)
            _drain_events(app)
            self.assertEqual([], started)
            self.assertEqual("strict", service._preview_pending)

            QTest.qWait(120)
            _drain_events(app)
            self.assertEqual([True], started)
            self.assertIsNone(service._preview_pending)
            self.assertIsNone(service._preview_pending_delay_ms)
        finally:
            service.shutdown()


if __name__ == "__main__":
    unittest.main()
