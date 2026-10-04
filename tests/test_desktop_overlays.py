"""Desktop mode lifecycles, coordinate mapping and idle repaint regressions.

No global hooks or real drawing are started in this suite.
"""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest
from PySide6.QtCore import QObject, QPoint, QRect, Signal, QEvent, QCoreApplication, Qt
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication, QWidget

from ui.helpers.viewport_mapper import MonitorSnapshot
from ui.overlays.coordinates import DesktopCoordinates
from ui.overlays.controller import DesktopOverlayController
from ui.overlays.capture_guide import CaptureGuideOverlay
from ui.overlays.region_pick import RegionPickOverlay, TwoPointPickOverlay, _BasePickOverlay
from ui.overlays.region_pick import PointPickOverlay
from ui.overlays.calib_flash import CalibFlashOverlay
from ui.widgets.kalka_stencil import KalkaStencilOverlay
from ui.widgets.hud_overlay import HudOverlay


_app = QApplication.instance() or QApplication([])


def test_capture_hint_names_the_keys_that_really_finish_the_capture():
    # The main window is hidden during these captures: the on-screen hint is the
    # only way to learn how to save (layers used to say "button in OlegPainter").
    from ui.i18n import i18n
    from ui.overlays.controller import capture_hint
    keys = {"define_manual_palette": "F9", "define_app_layers": "F8", "stop": "F4",
            "record_pre_color_actions": "[", "record_post_color_actions": "Ctrl+F7"}
    assert i18n.current_language() == "ru"
    assert capture_hint("palette", None, keys) == "Кликайте по цветам — F9 сохранить, F4 стоп"
    assert capture_hint("layers", None, keys) == "Кликайте по кнопкам слоёв — F8 сохранить, F4 отмена"
    assert "[ (русская Х) сохранить" in capture_hint("extra", "pre", keys)
    assert "Ctrl+F7 сохранить" in capture_hint("extra", "post", keys)
    unbound = capture_hint("layers", None, {"stop": "F4"})
    assert "панели задач" in unbound and "F8" not in unbound
    assert capture_hint("hex", None, keys) == "Кликните по HEX-полю в программе"


@pytest.mark.parametrize("mode", ["stencil", "hud"])
def test_edit_surface_owns_input_until_mode_finishes(desktop, mode):
    from infrastructure.capture_session import automation_input
    from infrastructure.input_ownership import InputBusyError
    controller, _, _ = desktop
    assert controller.prepare(mode)
    with pytest.raises(InputBusyError):
        with automation_input.claim(object(), "drawing"):
            pytest.fail("drawing entered interactive screen mode")
    assert controller.prepare("save")
    assert automation_input.current is None


def test_picker_rejection_restores_idle_mode(desktop):
    from infrastructure.capture_session import automation_input
    controller, _, service = desktop
    messages = []
    service.statusChanged.connect(messages.append)
    with automation_input.claim(object(), "drawing"):
        service.screenCalibrationRequested.emit("ring")
        assert controller.picker is None
        assert controller.mode == ""
        assert "Ввод занят" in messages[-1]


def test_edit_surface_rejects_another_active_owner(desktop):
    from infrastructure.capture_session import automation_input
    controller, window, _ = desktop
    with automation_input.claim(object(), "learning"):
        window.hud_overlay.set_edit_mode(True)
        assert not window.hud_overlay.is_edit_mode()
        assert controller.mode == ""


def test_shutdown_keeps_failed_picker_available_for_retry(desktop):
    controller, _, service = desktop
    service.screenCalibrationRequested.emit("ring")
    picker = controller.picker
    with patch.object(picker, "_stop_listeners", side_effect=RuntimeError("hook stop failed")):
        with pytest.raises(RuntimeError, match="Повторите закрытие"):
            controller.shutdown()
        assert controller.picker is picker
        assert not picker._closed
        assert not controller._closing
        assert controller.service is service
    controller.shutdown()
    assert controller.picker is None
    assert controller.service is None
    from infrastructure.capture_session import automation_input
    assert automation_input.current is None


def test_capture_guide_not_created_if_previous_picker_cannot_stop(desktop):
    controller, _, service = desktop
    service.screenCalibrationRequested.emit("ring")
    picker = controller.picker
    with patch.object(picker, "_stop_listeners", side_effect=RuntimeError("hook stop failed")):
        service.captureStateChanged.emit({"active": True, "kind": "palette", "count": 1})
        assert controller.picker is picker
        assert controller.mode == "pick"
        assert controller.guide is None


def test_point_picker_consumes_click_and_finishes_on_release():
    from PySide6.QtCore import Qt
    values = []
    picker = PointPickOverlay("Size", values.append)
    try:
        assert not picker.testAttribute(Qt.WA_TransparentForMouseEvents)
        picker._handle_event("down", (-220, 120))
        assert not values
        picker._handle_event("up", (-220, 120))
        assert values == [(-220, 120)] and picker._closed
        picker._pending.put(("down", (500, 500)))
        picker._tick()
        assert values == [(-220, 120)]
    finally:
        picker.close()
        picker.deleteLater()


def snapshots():
    return [MonitorSnapshot("left", "left", (-2560, 0, 2560, 1440), (-2560, 0, 1280, 720), 2, 2),
            MonitorSnapshot("main", "main", (0, 0, 1920, 1080), (0, 0, 1920, 1080), 1, 1)]


def test_coordinates_preserve_origin_and_each_monitor_scale():
    coords = DesktopCoordinates(snapshots())
    assert coords.to_logical(-2400, 200) == (-2480, 100)
    assert coords.to_physical(-2480, 100) == (-2400, 200)
    assert coords.to_logical(300, 200) == (300, 200)
    # A point just outside the second screen uses its origin, not the first scale.
    assert coords.to_logical(1922, 500) == (1922, 500)
    items = [{"type": "line", "x1": -2400, "y1": 200, "x2": 300, "y2": 200},
             {"type": "rect", "x": -2400, "y": 200, "w": 400, "h": 200}]
    mapped = coords.logical_items(items)
    assert mapped[0] == {"type": "line", "x1": -2480, "y1": 100, "x2": 300, "y2": 200}
    assert (mapped[1]["w"], mapped[1]["h"]) == (200, 100)
    assert items[1]["w"] == 400
    with pytest.raises(RuntimeError):
        DesktopCoordinates([]).to_physical(10, 20)


@pytest.mark.parametrize("factory", [CaptureGuideOverlay, lambda: RegionPickOverlay("test", Mock())])
def test_idle_pointer_does_not_request_repaints(factory):
    with patch("ui.overlays.pointer_surface.QCursor.pos", return_value=QPoint(100, 100)):
        surface = factory()
        try:
            assert not surface._anim.isActive()
            surface.show()
            _app.processEvents()
            with patch.object(surface, "update") as repaint:
                for _ in range(120):
                    surface._tick()
                assert repaint.call_count == 0
                with patch("ui.overlays.pointer_surface.QCursor.pos", return_value=QPoint(120, 100)):
                    surface._tick()
                assert repaint.call_count == 1
            surface.hide()
            assert not surface._anim.isActive()
        finally:
            surface.close()
            surface.deleteLater()


def test_static_marks_have_only_a_single_expiry_timer():
    surface = CalibFlashOverlay([], "test", 6000)
    assert not hasattr(surface, "_anim")
    assert surface._timeout.isSingleShot()
    assert not surface._timeout.isActive()
    surface.show()
    assert surface._timeout.isActive()
    surface.close()
    assert not surface._timeout.isActive()


@pytest.mark.parametrize("action", ["cancel", "hide", "close"])
def test_picker_releases_listeners_and_cancels_once(action):
    done, cancel = Mock(), Mock()
    surface = RegionPickOverlay("test", done, cancel)
    with patch.object(surface, "_start_listeners"):
        surface.open()
    listeners = [Mock(), Mock()]
    surface._mouse_listener, surface._key_listener = listeners
    getattr(surface, action)()
    surface.cancel()
    assert not surface._anim.isActive()
    assert not surface._timeout.isActive()
    for listener in listeners:
        listener.stop.assert_called_once()
    cancel.assert_called_once()
    done.assert_not_called()
    surface.deleteLater()


def test_small_region_retries_and_valid_region_returns_physical_pixels():
    done, cancel = Mock(), Mock()
    surface = RegionPickOverlay("test", done, cancel)
    surface._handle_event("down", (-2000, 300))
    surface._handle_event("up", (-1999, 301))
    assert surface._first_phys is None
    done.assert_not_called()
    surface._handle_event("down", (-2000, 300))
    surface._handle_event("up", (-2200, 500))
    done.assert_called_once_with((-2200, 300, 200, 200))
    cancel.assert_not_called()
    surface.deleteLater()


def test_click_position_comes_from_event_without_querying_cursor():
    surface = TwoPointPickOverlay("1", "2", Mock())
    with patch("pynput.mouse.Listener") as mouse, patch("pynput.keyboard.Listener"):
        surface._start_listeners()
        callback = mouse.call_args.kwargs["on_click"]
        callback(-2011, 312, SimpleNamespace(name="left"), True)
        assert surface._pending.get_nowait() == ("down", (-2011, 312))
    surface.cancel()
    surface.deleteLater()


class Service(QObject):
    captureStateChanged = Signal(object)
    drawingStateChanged = Signal(str)
    screenCalibrationRequested = Signal(str)
    brushCalibrationRequested = Signal(object)
    statusChanged = Signal(str)

    def __init__(self):
        super().__init__()
        self.engine = SimpleNamespace(manual_palette_coords=[], target_app_layer_coords=[],
                                      circle_params_calib=None, slider_params_calib=None)
        self.engine.layer_capture_points = lambda: list(self.engine.target_app_layer_coords)
        self.cancel_active_captures = Mock()
        self._emit_session_state_changed = Mock()


@pytest.fixture
def desktop():
    window = QWidget()
    window.kalka_overlay = KalkaStencilOverlay()
    window.hud_overlay = HudOverlay()
    controller = DesktopOverlayController(window)
    service = Service()
    controller.bind_service(service)
    with patch.object(_BasePickOverlay, "_start_listeners"):
        yield controller, window, service
    controller.shutdown()
    window.kalka_overlay.close()
    window.hud_overlay.close()
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


def test_capture_switch_replaces_points_even_with_equal_count(desktop):
    controller, window, service = desktop
    service.engine.manual_palette_coords = [(100, 120)]
    service.engine.target_app_layer_coords = [(300, 320)]
    window.hud_overlay.set_edit_mode(True)
    service.captureStateChanged.emit({"active": True, "kind": "palette", "count": 1})
    assert not window.hud_overlay.is_edit_mode()
    assert controller.guide._points == [{"x": 100, "y": 120, "label": "1"}]
    service.captureStateChanged.emit({"active": True, "kind": "layers", "count": 1})
    assert controller.guide._points == [{"x": 300, "y": 320, "label": "1"}]
    service.captureStateChanged.emit({"active": False})
    assert not controller.guide.isVisible()
    assert not controller.guide._anim.isActive()


def test_edit_modes_are_exclusive_and_cancel_pending_calibration(desktop):
    controller, window, service = desktop
    service.screenCalibrationRequested.emit("ring")
    picker = controller.picker
    window.hud_overlay.set_edit_mode(True)
    assert picker._closed
    assert controller.picker is None
    window.kalka_overlay.set_edit_mode(True)
    assert not window.hud_overlay.is_edit_mode()
    assert window.kalka_overlay.is_edit_mode()
    service.drawingStateChanged.emit("started")
    assert not window.kalka_overlay.is_edit_mode()
    assert window.kalka_overlay._state.window.click_through


def test_calibration_result_saved_and_cancel_leaves_previous_value(desktop):
    controller, _, service = desktop
    service.screenCalibrationRequested.emit("ring")
    picker = controller.picker
    picker._handle_event("down", (-2000, 300))
    picker._handle_event("down", (-1900, 300))
    assert controller.picker is None
    assert service.engine.circle_params_calib == (-2000, 300, 100.0, 0.0)
    service._emit_session_state_changed.assert_called_once()
    service.screenCalibrationRequested.emit("ring")
    controller.picker.cancel()
    assert service.engine.circle_params_calib == (-2000, 300, 100.0, 0.0)
    service.screenCalibrationRequested.emit("slider")
    picker = controller.picker
    picker._handle_event("down", (100, 200))
    picker._handle_event("up", (120, 400))
    assert service.engine.slider_params_calib == ("vertical", 110.0, 200.0, 400.0)


def test_stop_cancels_partial_picker_without_publishing_or_reentering_engine_stop(desktop):
    controller, _, service = desktop
    previous = (-2000, 300, 100.0, 0.0)
    service.engine.circle_params_calib = previous
    service.screenCalibrationRequested.emit("ring")
    picker = controller.picker
    picker._handle_event("down", (100, 200))
    service.cancel_active_captures.reset_mock()
    service.drawingStateChanged.emit("stopping")
    assert picker._closed and not picker.isVisible()
    assert controller.picker is None and controller.mode == ""
    assert picker._capture_session.finished.is_set()
    assert service.engine.circle_params_calib == previous
    service._emit_session_state_changed.assert_not_called()
    service.cancel_active_captures.assert_not_called()


@pytest.mark.parametrize("mode", ["hud", "stencil"])
def test_stop_exits_screen_editor_and_releases_its_input(desktop, mode):
    controller, window, service = desktop
    overlay = window.hud_overlay if mode == "hud" else window.kalka_overlay
    overlay.set_edit_mode(True)
    session = controller._input_session
    assert session is not None
    service.drawingStateChanged.emit("stopping")
    assert not overlay.is_edit_mode()
    assert controller.mode == "" and session.finished.is_set()


def test_drawing_blocks_calibration_and_shutdown_releases_picker(desktop):
    controller, _, service = desktop
    service._is_drawing = True
    service.screenCalibrationRequested.emit("ring")
    assert controller.picker is None
    service._is_drawing = False
    service.screenCalibrationRequested.emit("ring")
    picker = controller.picker
    controller.shutdown()
    assert picker._closed
    assert not picker._anim.isActive()
    assert controller.service is None


def test_f1_repeat_cancel_restores_geometry_and_does_not_schedule_capture(desktop):
    _, window, _ = desktop
    k = window.kalka_overlay
    image = QImage(200, 100, QImage.Format_ARGB32)
    image.fill(0xFF446688)
    k.set_source_pixmap(QPixmap.fromImage(image))
    k.setGeometry(130, 140, 400, 300)
    k.show()
    k.set_edit_mode(True)
    geometry = QRect(k.geometry())
    k.begin_area_placement(SimpleNamespace(logical_rect=(0, 0, 1920, 1080)))
    k.begin_area_placement(SimpleNamespace(logical_rect=(0, 0, 1920, 1080)))
    k.request_save()
    assert not k._content_timer.isActive()
    k.cancel_area_placement()
    assert not k._placement_active
    assert k.geometry() == geometry
    assert k.is_edit_mode()


def test_repeated_passthrough_does_not_recreate_visible_window():
    surface = CaptureGuideOverlay()
    surface.show()
    with patch.object(surface, "setWindowFlag") as change_flag:
        surface.set_passthrough(True)
        surface.set_passthrough(True)
    change_flag.assert_not_called()
    assert surface.isVisible()
    surface.close()
    surface.deleteLater()


def test_hud_migrates_old_layout_and_preserves_hidden_fields_when_hidden():
    hud = HudOverlay()
    hud.restore_state({"positions": {"status": [0.8, 0.1]}, "hidden_blocks": ["colors"],
                       "scale": 1.2, "opacity": 0.85, "visible": False})
    state = hud.export_state()
    assert state["layout_version"] == 2
    assert state["panel_position"] == [0.8, 0.1]
    assert set(state["hidden_blocks"]) == {"colors", "layers", "palette", "hotkeys", "speed", "image", "settings"}
    hud._vis_checks["image"].setChecked(True)
    state = hud.export_state()
    restored = HudOverlay()
    restored.restore_state(state)
    assert restored.export_state()["hidden_blocks"] == state["hidden_blocks"]
    assert not restored._blocks["image"].isHidden()
    hud.deleteLater()
    restored.deleteLater()


def test_hud_toolbar_fits_and_escape_finishes_edit(desktop):
    from PySide6.QtTest import QTest
    from PySide6.QtCore import Qt

    _, window, _ = desktop
    hud = window.hud_overlay
    hud.set_edit_mode(True)
    hud.setGeometry(0, 0, 800, 600)
    _app.processEvents()
    assert hud.rect().contains(hud._toolbar.geometry())
    assert hud.rect().contains(hud._panel.geometry())
    for check in hud._vis_checks.values():
        check.setChecked(True)
    _app.processEvents()
    assert hud.rect().contains(hud._panel.geometry())
    QTest.keyClick(hud, Qt.Key_Escape)
    assert not hud.is_edit_mode()
    assert hud.is_passthrough()


def test_hud_repeated_stats_do_not_schedule_layout_or_repaint(desktop):
    _, window, _ = desktop
    hud = window.hud_overlay
    stats = {"percent": 42, "elapsed_seconds": 120, "eta_seconds": 150,
             "colors_total": 20, "colors_done": 8}
    hud.update_stats(stats)
    _app.processEvents()
    with patch.object(hud._blocks["progress"]._bar, "update") as repaint:
        hud.update_stats(stats)
        repaint.assert_not_called()
    assert not hud._geom_timer.isActive()


def test_palette_capture_guide_marks_dictionary_coordinates(desktop):
    controller, _, service = desktop
    service.engine.manual_palette_coords = [{"x": 350, "y": 170, "rgb": [0, 0, 0]}]
    controller._capture_changed({"active": True, "kind": "palette", "count": 1})
    assert controller.guide._points == [{"x": 350, "y": 170, "label": "1"}]
    assert controller.guide._compact_marks


def test_dense_palette_marks_leave_centres_and_neighbouring_swatches_uncovered():
    guide = CaptureGuideOverlay()
    try:
        guide.show()  # Offscreen fixture; no listener or input device.
        guide.setGeometry(0, 0, 640, 360)
        guide.coordinates = DesktopCoordinates([MonitorSnapshot("test", "test", (0, 0, 640, 360), (0, 0, 640, 360), 1, 1)])
        guide._cursor = QPoint(600, 300)
        guide.active_screen_rect = lambda: QRect(0, 0, 640, 360)
        guide.set_state("Палитра", 13, [dict(x=100 + (i % 10) * 30, y=150 + (i // 10) * 30, label=str(i + 1))
                                      for i in range(13)], compact_marks=True)
        frame = QImage(640, 360, QImage.Format_ARGB32_Premultiplied)
        frame.fill(Qt.transparent)
        with patch("ui.overlays.capture_guide.draw_label") as labels:
            guide.render(frame)
            labels.assert_not_called()
        assert frame.pixelColor(100, 150).alpha() == 0  # Actual swatch stays visible.
        assert frame.pixelColor(106, 150).alpha() > 0  # Small ring still identifies the point.
        assert frame.pixelColor(114, 164).alpha() == 0  # Former badge position.
        assert frame.pixelColor(160, 180).alpha() == 0  # Centre of colour 13.
        assert guide.testAttribute(Qt.WA_TransparentForMouseEvents)
        assert guide.windowFlags() & Qt.WindowTransparentForInput
        guide.set_state("Слои", 1, [dict(x=100, y=150, label="1")])
        with patch("ui.overlays.capture_guide.draw_label") as labels:
            guide.render(frame)
            assert labels.call_count == 2  # Sequence order and cursor coordinates still available.
    finally:
        guide.close()
        guide.deleteLater()
