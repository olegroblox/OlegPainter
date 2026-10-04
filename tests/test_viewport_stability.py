from __future__ import annotations

import os
import threading
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtGui import QImage
from PySide6.QtCore import QCoreApplication, QEvent, QObject, QThread
from PySide6.QtWidgets import QApplication
from PIL import Image

from ui.helpers.viewport_mapper import (
    MonitorSnapshot,
    desktop_rect_to_ui_rect,
    select_monitor_snapshot,
    ui_rect_to_desktop_rect,
)
from ui.services.painter_service import PainterService


def _qt_app() -> QApplication:
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


def _drain_events(app: QApplication, rounds: int = 6) -> None:
    for _ in range(rounds):
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
        QCoreApplication.sendPostedEvents(None, 0)
        app.processEvents()


class _OverlaySpy(QObject):
    def __init__(self, service: PainterService):
        super().__init__()
        self.visible = False
        self.editing = False
        self.closed = False
        self.close_count = 0
        self.hide_count = 0
        self.show_count = 0
        self.set_area_count = 0
        self.set_image_count = 0
        self.cancel_count = 0
        self.thread_checks: list[bool] = []
        self.last_area = None
        self.last_image = None

    def _record(self) -> None:
        self.thread_checks.append(QThread.isMainThread())

    def isVisible(self) -> bool:
        return self.visible

    def is_editing(self) -> bool:
        return self.editing

    def set_area(self, rect) -> None:
        self._record()
        self.set_area_count += 1
        self.last_area = (rect.x(), rect.y(), rect.width(), rect.height())

    def set_image(self, qimg: QImage) -> None:
        self._record()
        self.set_image_count += 1
        self.last_image = qimg.copy()

    def show_overlay(self) -> None:
        self._record()
        self.show_count += 1
        self.visible = True

    def hide(self) -> None:
        self._record()
        self.hide_count += 1
        self.visible = False

    def close(self) -> None:
        self._record()
        self.close_count += 1
        self.closed = True
        self.visible = False

    def cancel_edit_session(self, emit_signal: bool = False) -> None:
        del emit_signal
        self._record()
        self.cancel_count += 1
        self.editing = False


class ViewportMapperTests(unittest.TestCase):
    def test_roundtrip_desktop_and_ui_rect_for_negative_monitor(self) -> None:
        monitors = [
            MonitorSnapshot(
                monitor_id="DISPLAY1",
                device_name="DISPLAY1",
                desktop_rect=(0, 0, 1920, 1080),
                logical_rect=(0, 0, 1920, 1080),
                scale_x=1.0,
                scale_y=1.0,
                is_primary=True,
            ),
            MonitorSnapshot(
                monitor_id="DISPLAY2",
                device_name="DISPLAY2",
                desktop_rect=(-1600, 0, 1600, 900),
                logical_rect=(-1280, 0, 1280, 720),
                scale_x=1.25,
                scale_y=1.25,
                is_primary=False,
            ),
        ]
        rect = (-1200, 120, 400, 240)

        snapshot = select_monitor_snapshot(rect, monitors=monitors)
        self.assertIsNotNone(snapshot)
        self.assertEqual(snapshot.monitor_id, "DISPLAY2")

        logical = desktop_rect_to_ui_rect(rect, snapshot)
        self.assertEqual(logical, (-960, 96, 320, 192))
        self.assertEqual(ui_rect_to_desktop_rect(logical, snapshot), rect)

    def test_stale_rect_has_no_monitor_binding_without_fallback(self) -> None:
        monitors = [
            MonitorSnapshot(
                monitor_id="DISPLAY1",
                device_name="DISPLAY1",
                desktop_rect=(0, 0, 1920, 1080),
                logical_rect=(0, 0, 1920, 1080),
                scale_x=1.0,
                scale_y=1.0,
                is_primary=True,
            )
        ]
        self.assertIsNone(select_monitor_snapshot((5000, 5000, 200, 100), monitors=monitors, allow_fallback=False))


class ViewportConfigTests(unittest.TestCase):
    def test_load_config_drops_removed_manual_fit_settings(self) -> None:
        app = _qt_app()
        service = PainterService()
        try:
            service.load_config(
                {
                    "draw_region": (10, 20, 300, 200),
                    "stretch_to_area": False,
                    "manual_image_rect": (5, 6, 111, 112),
                }
            )
            app.processEvents()
            self.assertTrue(service.get_stretch_to_area())
            self.assertIsNone(service.get_manual_image_rect())
            self.assertEqual(tuple(service.get_viewport_state().draw_region_desktop_px), (10, 20, 300, 200))
        finally:
            service.shutdown()
            service.deleteLater()
            app.processEvents()


class ViewportServiceTests(unittest.TestCase):
    def test_apply_viewport_state_ignores_removed_manual_fit_data(self) -> None:
        _qt_app()
        service = PainterService()
        try:
            service.apply_viewport_state(
                draw_region_desktop_px=(0, 0, 200, 100),
                manual_rect_area_px=(5, 5, 50, 40),
                emit=False,
            )
            service.apply_viewport_state(
                draw_region_desktop_px=(100, 50, 300, 150),
                reset_manual=True,
                emit=False,
            )
            self.assertTrue(service.get_stretch_to_area())
            self.assertIsNone(service.get_manual_image_rect())
        finally:
            service.shutdown()
            service.deleteLater()

    def test_overlay_apply_updates_overlay_physical_area(self) -> None:
        from ui.services.stencil_sync import USE_KALKA_OVERLAY
        if USE_KALKA_OVERLAY:
            self.skipTest(
                "Kalka mode: the legacy/unified overlay-apply path is inactive "
                "(the Kalka glass capture path is covered by test_kalka_capture)."
            )
        app = _qt_app()
        service = PainterService()
        try:
            overlay = service._ensure_overlay_widget()
            if hasattr(overlay, "_area_item"):
                # Unified overlay: applying the viewport state + syncing the overlay
                # places the draw-area item at the physical draw region (scene == px).
                service.apply_viewport_state(draw_region_desktop_px=(120, 240, 330, 180), emit=True)
                service._sync_overlay_area()
                app.processEvents()
                self.assertEqual(service.engine.draw_region, (120, 240, 330, 180))
                r = overlay._area_item.rect()
                self.assertEqual(
                    (round(r.x()), round(r.y()), round(r.width()), round(r.height())),
                    (120, 240, 330, 180),
                )
            else:
                service.apply_viewport_state(draw_region_desktop_px=(100, 200, 300, 150), emit=True)
                overlay.begin_edit_session(area_size=(300, 150), pixmap=None, rect=None, stretch=True)
                service._on_overlay_edit_applied(
                    {"area": (120, 240, 330, 180), "stretch": True, "rect": None}
                )
                app.processEvents()
                self.assertEqual(service.engine.draw_region, (120, 240, 330, 180))
                self.assertEqual(
                    (
                        overlay._physical_area.x(),
                        overlay._physical_area.y(),
                        overlay._physical_area.width(),
                        overlay._physical_area.height(),
                    ),
                    (120, 240, 330, 180),
                )
        finally:
            service.shutdown()
            service.deleteLater()
            app.processEvents()

    def test_engine_show_stencil_is_queued_to_qt_thread(self) -> None:
        app = _qt_app()
        service = PainterService()
        try:
            overlay = _OverlaySpy(service)
            service._overlay = overlay
            service.engine.draw_region = (0, 0, 40, 20)
            service._stencil_enabled_requested = True
            service._last_qimg = QImage(40, 20, QImage.Format_RGBA8888)

            worker = threading.Thread(target=lambda: service._engine_show_stencil(None, (0, 0, 40, 20)))
            worker.start()
            worker.join()

            self.assertEqual(overlay.show_count, 0)
            _drain_events(app)

            self.assertEqual(overlay.show_count, 1)
            self.assertEqual(overlay.last_area, (0, 0, 40, 20))
            self.assertTrue(all(overlay.thread_checks))
        finally:
            service.shutdown()
            service.deleteLater()
            _drain_events(app)

    def test_engine_hide_stencil_hides_without_destroying_overlay(self) -> None:
        app = _qt_app()
        service = PainterService()
        try:
            overlay = _OverlaySpy(service)
            overlay.visible = True
            service._overlay = overlay

            service._engine_hide_stencil()
            _drain_events(app)

            self.assertFalse(overlay.visible)
            self.assertEqual(overlay.close_count, 0)
            self.assertFalse(overlay.closed)
        finally:
            service.shutdown()
            service.deleteLater()
            _drain_events(app)

    def test_post_draw_repair_overlay_is_shown_only_when_remaining_cells_exist(self) -> None:
        app = _qt_app()
        service = PainterService()
        try:
            overlay = _OverlaySpy(service)
            service._overlay = overlay
            service.engine.draw_region = (0, 0, 40, 20)
            payload = {
                "pass_index": 1,
                "total_passes": 1,
                "detected_cells": 3,
                "repaired_cells": 1,
                "remaining_cells": 2,
                "unverifiable_cells": 0,
                "regions": [{"area": 2}],
                "overlay": Image.new("RGBA", (40, 20), (255, 0, 0, 90)),
                "draw_region": (0, 0, 40, 20),
                "message": "remaining",
            }

            service._engine_on_post_draw_repair(payload)
            _drain_events(app)

            self.assertEqual(overlay.show_count, 1)
            self.assertEqual(overlay.last_area, (0, 0, 40, 20))
        finally:
            service.shutdown()
            service.deleteLater()
            _drain_events(app)

    def test_post_draw_repair_clear_hides_overlay_when_nothing_remains(self) -> None:
        app = _qt_app()
        service = PainterService()
        try:
            overlay = _OverlaySpy(service)
            overlay.visible = True
            service._overlay = overlay
            service.engine.draw_region = (0, 0, 40, 20)
            payload = {
                "pass_index": 1,
                "total_passes": 1,
                "detected_cells": 2,
                "repaired_cells": 2,
                "remaining_cells": 0,
                "unverifiable_cells": 0,
                "regions": [],
                "overlay": None,
                "draw_region": (0, 0, 40, 20),
                "message": "fixed",
            }

            service._engine_on_post_draw_repair(payload)
            _drain_events(app)

            self.assertFalse(overlay.visible)
            self.assertGreaterEqual(overlay.hide_count, 1)
        finally:
            service.shutdown()
            service.deleteLater()
            _drain_events(app)

    def test_render_invalidation_does_not_close_overlay_instance(self) -> None:
        _qt_app()
        service = PainterService()
        try:
            overlay = _OverlaySpy(service)
            overlay.visible = True
            service._overlay = overlay
            service.engine.set_k_clusters = lambda value: None
            service._rebuild_preview_strict = lambda: None

            service.set_k_clusters(12)

            self.assertEqual(overlay.close_count, 0)
            self.assertFalse(overlay.closed)
            self.assertTrue(service._stencil_dirty)
        finally:
            service.shutdown()
            service.deleteLater()

    def test_edit_mode_defers_stencil_refresh_until_cancel(self) -> None:
        app = _qt_app()
        service = PainterService()
        try:
            overlay = _OverlaySpy(service)
            overlay.editing = True
            overlay.visible = True
            service._overlay = overlay
            service.engine.draw_region = (10, 20, 100, 60)
            service._stencil_enabled_requested = True

            qimg = QImage(100, 60, QImage.Format_RGBA8888)
            service.schedule_stencil_sync("preview-ready", 3, image=qimg)
            _drain_events(app)

            self.assertTrue(service._stencil_refresh_deferred)
            self.assertEqual(overlay.set_image_count, 0)

            overlay.editing = False
            service._on_overlay_edit_cancelled()
            _drain_events(app)

            self.assertFalse(service._stencil_refresh_deferred)
            self.assertEqual(overlay.set_image_count, 1)
            self.assertTrue(overlay.visible)
        finally:
            service.shutdown()
            service.deleteLater()
            _drain_events(app)

    def test_late_preview_does_not_resurrect_disabled_stencil(self) -> None:
        app = _qt_app()
        service = PainterService()
        try:
            overlay = _OverlaySpy(service)
            service._overlay = overlay
            service.engine.draw_region = (0, 0, 32, 32)
            service._stencil_enabled_requested = True
            service.schedule_stencil_sync("initial-show", 1, image=QImage(32, 32, QImage.Format_RGBA8888))
            _drain_events(app)
            self.assertTrue(overlay.visible)

            service._stencil_enabled_requested = False
            service.schedule_stencil_sync("toggle-off", 2, force_hide=True)
            _drain_events(app)
            self.assertFalse(overlay.visible)

            service._preview_active_revision = 2
            service._on_preview_from_engine(Image.new("RGBA", (32, 32), (255, 0, 0, 255)))
            _drain_events(app)

            self.assertFalse(overlay.visible)
            self.assertEqual(overlay.show_count, 1)
        finally:
            service.shutdown()
            service.deleteLater()
            _drain_events(app)
