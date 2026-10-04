"""All presentations and preflight must agree on readiness and capture."""
from dataclasses import FrozenInstanceError
import json
from types import SimpleNamespace

from PIL import Image

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QRect
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from application.controller import ApplicationController
from engine.olegpainter.drawing_events import DrawingEvent, DrawingPhase
from ui.helpers.config_store import ConfigStore
from ui.i18n import i18n, tr
from ui.services.painter_service import PainterService


@pytest.fixture
def application(tmp_path):
    app = QApplication.instance() or QApplication([])
    language = i18n.current_language()
    service = PainterService()
    service._rebuild_preview_if_possible = lambda: None
    service._rebuild_preview_strict = lambda: None
    controller = ApplicationController(service, config_store=ConfigStore(tmp_path))
    controller.enable_desktop()
    yield controller, service
    controller.close(save=False)
    controller.deleteLater()
    service.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()
    i18n.set_language(language)


def prepare(controller, service, *, calibrated=True):
    service._last_source_qimg = QImage(10, 10, QImage.Format_RGBA8888)
    service._last_qimg = QImage(service._last_source_qimg)
    service.apply_viewport_state(draw_region_desktop_px=(20, 20, 100, 100), emit=False)
    service.engine.color_picking_method = "hex_field"
    service.engine.hex_input_coord = (5, 5) if calibrated else None
    controller.refresh_preparation()


def test_hud_web_and_preflight_agree_after_calibration_and_language_change(application):
    controller, service = application
    prepare(controller, service, calibrated=False)
    hud = controller.desktop.hud_overlay
    assert not controller.state.can_start
    assert not service._preflight_check()[0]
    assert tr("hud_state_preparing") in hud._blocks["status"]._value.text()
    service.engine.hex_input_coord = (5, 5)
    service._emit_session_state_changed("calibration", sections=("painter",))
    assert controller.state.can_start
    assert service._preflight_check()[0]
    assert tr("hud_state_idle") in hud._blocks["status"]._value.text()
    controller.set_language("en")
    assert "Ready to start" in hud._blocks["status"]._value.text()


def test_capture_blocks_start_and_image_edits_in_all_presentations(application):
    controller, service = application
    prepare(controller, service)
    service.engine.is_waiting_for_hex_click = True
    service._emit_capture_state()
    assert controller.state.capture.kind == "hex"
    assert not controller.state.can_start and not controller.state.can_edit_source
    assert not service._preflight_check()[0]
    service.engine.is_waiting_for_hex_click = False
    service._emit_capture_state()
    assert controller.state.can_start and controller.state.can_edit_source


def test_stencil_edit_uses_same_start_guard_and_exits_cleanly(application):
    controller, service = application
    prepare(controller, service)
    stencil = controller.desktop.kalka_overlay
    stencil.set_edit_mode(True)
    assert controller.state.desktop_mode == "stencil"
    assert not service._preflight_check()[0]
    stencil.set_edit_mode(False, capture_on_exit=False)
    assert controller.state.desktop_mode == ""
    assert service._preflight_check()[0]


def test_area_placement_blocks_commands_until_cancelled_or_edit_finished(application):
    controller, service = application
    prepare(controller, service)
    stencil = controller.desktop.kalka_overlay
    stencil.set_source_pixmap(QPixmap.fromImage(service._last_source_qimg))
    stencil.begin_area_placement()
    assert stencil.is_placing_area()
    assert controller.state.desktop_mode == "stencil"
    assert not controller.state.can_edit_source
    assert not service._preflight_check()[0]
    stencil.cancel_area_placement()
    assert controller.state.desktop_mode == ""
    assert controller.state.can_start

    stencil.begin_area_placement()
    stencil._view._finish_placement(QRect(20, 20, 100, 100))
    assert not stencil.is_placing_area() and stencil.is_edit_mode()
    assert controller.state.desktop_mode == "stencil"
    assert not controller.state.can_edit_source
    stencil.set_edit_mode(False, capture_on_exit=False)
    assert controller.state.desktop_mode == ""


def test_hidden_stencil_releases_edit_ownership(application):
    controller, service = application
    prepare(controller, service)
    stencil = controller.desktop.kalka_overlay
    stencil.set_source_pixmap(QPixmap.fromImage(service._last_source_qimg))
    stencil.show()
    stencil.set_edit_mode(True)
    controller.desktop.toggle_stencil()
    assert not stencil.is_edit_mode()
    assert controller.state.desktop_mode == ""
    assert controller.state.can_start


def test_queued_session_geometry_does_not_replace_active_area_picker(application):
    controller, service = application
    prepare(controller, service)
    service.engine.source_pil_image = Image.new("RGB", (10, 10), "black")
    desktop = controller.desktop
    desktop._monitor_snapshot_under_cursor = lambda: SimpleNamespace(logical_rect=(0, 0, 800, 600))
    desktop.select_area()
    stencil = desktop.kalka_overlay
    assert stencil.geometry() == QRect(0, 0, 800, 600)
    QApplication.processEvents()
    assert stencil.is_placing_area()
    assert stencil.geometry() == QRect(0, 0, 800, 600)
    assert controller.state.desktop_mode == "stencil"
    stencil.cancel_area_placement()


def test_hidden_area_picker_releases_placement_ownership(application):
    controller, service = application
    prepare(controller, service)
    stencil = controller.desktop.kalka_overlay
    stencil.set_source_pixmap(QPixmap.fromImage(service._last_source_qimg))
    stencil.begin_area_placement()
    controller.desktop.toggle_stencil()
    assert not stencil.is_placing_area()
    assert controller.state.desktop_mode == ""
    assert controller.state.can_start


def test_snapshot_is_immutable_and_each_execution_transition_is_one_revision(application):
    controller, service = application
    prepare(controller, service)
    with pytest.raises(FrozenInstanceError):
        controller.state.preparation.can_start = False
    revision = controller.state_revision
    controller.refresh_preparation()
    assert controller.state_revision == revision
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.RUNNING))
    assert controller.state.drawing == "started"
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.COMPLETED))
    assert controller.state.drawing == "completed"
    assert controller.state_revision == revision + 2
    assert controller.desktop.hud_overlay._statuses["state"] == "completed"


