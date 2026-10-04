"""Palette edits must preserve calibration identity, preparation and saved data."""
from unittest.mock import patch

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from engine.olegpainter.core import OlegPainter, mouse
from ui.services.painter_service import PainterService
from infrastructure.input_target import is_external_capture_point


@pytest.fixture
def service():
    app = QApplication.instance() or QApplication([])
    service = PainterService()
    service.engine.manual_palette_coords = [
        dict(x=-120, y=80, rgb=[0, 0, 0], hex="#000000"),
        dict(x=-90, y=80, rgb=[238, 245, 252], hex="#EEF5FC"),
    ]
    service._on_engine_manual_palette_changed()
    yield service
    service.shutdown()
    service.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_edit_refreshes_color_cache_and_session_payload(service):
    cache = service.engine._get_manual_palette_cache()
    previous = service.manual_palette_snapshot()
    changed = []
    service.sessionStateChanged.connect(lambda *args: changed.append(args))
    service.edit_manual_palette("update", 1, previous["revision"], "-90", "80", " ffffff ")
    assert service.engine._get_manual_palette_cache() is not cache
    assert service.engine._get_manual_palette_cache()["entries"][1]["rgb"] == (255, 255, 255)
    assert service.snapshot_painter_config()["manual_palette_coords"][1] == dict(
        x=-90, y=80, rgb=[255, 255, 255], hex="#FFFFFF")
    assert changed
    assert service.manual_palette_snapshot()["revision"] > previous["revision"]


@pytest.mark.parametrize("x,y,color", [("1.2", "80", "FFFFFF"), ("1", "nan", "FFFFFF"),
                                        ("1", "80", "#GGFFFF"), ("1", "80", "white"),
                                        (str(2**31), "0", "FFFFFF"), ("-120", "80", "FFFFFF")])
def test_invalid_edit_leaves_existing_data_and_revision_untouched(service, x, y, color):
    previous = service.manual_palette_snapshot()
    with pytest.raises(ValueError):
        service.edit_manual_palette("update", 1, previous["revision"], x, y, color)
    assert service.manual_palette_snapshot() == previous


def test_stale_selection_cannot_delete_different_color(service):
    revision = service.manual_palette_snapshot()["revision"]
    service.edit_manual_palette("remove", 0, revision)
    with pytest.raises(ValueError, match="изменилась"):
        service.edit_manual_palette("remove", 0, revision)
    assert service.engine.manual_palette_coords[0]["hex"] == "#EEF5FC"


@pytest.mark.parametrize("field,value", [("_is_drawing", True), ("_stop_pending", True),
                                         ("desktop_interaction", "stencil"), ("_hotkey_capture_active", True)])
def test_edit_rejected_while_input_owned(service, field, value):
    previous = service.manual_palette_snapshot()
    setattr(service, field, value)
    try:
        with pytest.raises(RuntimeError):
            service.edit_manual_palette("clear", -1, previous["revision"])
        assert service.manual_palette_snapshot() == previous
    finally:
        setattr(service, field, "" if isinstance(value, str) else False)


def test_edit_rejected_during_background_preparation(service):
    previous = service.manual_palette_snapshot()
    marker = object()
    service._preview_thread = marker
    try:
        with patch.object(service, "_thread_is_running", side_effect=lambda thread: thread is marker):
            with pytest.raises(RuntimeError, match="подготовки"):
                service.edit_manual_palette("clear", -1, previous["revision"])
        assert service.manual_palette_snapshot() == previous
    finally:
        service._preview_thread = None


def test_append_capture_updates_repeated_coordinate_and_ignores_own_ui(service):
    from types import SimpleNamespace
    from threading import Event
    import time
    engine = service.engine
    def wait_for_count(count):
        deadline = time.monotonic() + 2
        while len(engine.manual_palette_coords) != count and time.monotonic() < deadline:
            time.sleep(.005)
        assert len(engine.manual_palette_coords) == count
    measured = Event()
    def sample(*_):
        measured.set()
        return (255, 255, 255)
    with patch("engine.olegpainter.core.mouse.Listener") as listener, patch(
        "engine.olegpainter.palette_capture.WindowSampleRequest.at",
        side_effect=lambda x, y: SimpleNamespace(point=(x, y)) if x < 0 else None,
    ), patch("engine.olegpainter.palette_capture.sample_window_pixel", side_effect=sample) as sampler:
        service.append_manual_palette_capture()
        callback = listener.call_args.kwargs["on_click"]
        engine.ignore_clicks_until = 0
        callback(-90, 80, mouse.Button.left, True)
        assert measured.wait(1)
        deadline = time.monotonic() + 2
        while engine.manual_palette_coords[1]["hex"] != "#FFFFFF" and time.monotonic() < deadline:
            time.sleep(.005)
        assert engine.manual_palette_coords[1]["hex"] == "#FFFFFF"
        assert len(engine.manual_palette_coords) == 2
        callback(100, 100, mouse.Button.left, True)
        sampler.assert_called_once()
        callback(-60, 80, mouse.Button.left, True)
        wait_for_count(3)
        engine.finish_manual_palette_capture()
        engine._palette_capture_work.thread.join(2)
    assert not engine.is_capturing_manual_palette


def test_native_capture_ownership_is_read_only_and_rejects_missing_window():
    with patch("win32gui.WindowFromPoint", return_value=123), patch(
        "win32process.GetWindowThreadProcessId", return_value=(1, 44)
    ), patch("infrastructure.input_target.os.getpid", return_value=44):
        assert not is_external_capture_point(-100, 200)
    with patch("win32gui.WindowFromPoint", return_value=0):
        assert not is_external_capture_point(-100, 200)


def test_failed_listener_start_preserves_palette_and_reports_failure(service):
    previous = list(service.engine.manual_palette_coords)
    with patch("engine.olegpainter.core.mouse.Listener", side_effect=OSError("input unavailable")):
        with pytest.raises(RuntimeError, match="Не удалось"):
            service.append_manual_palette_capture()
    assert service.engine.manual_palette_coords == previous
    assert not service.engine.is_capturing_manual_palette


def test_failed_worker_start_restores_idle_capture_state(service):
    previous = list(service.engine.manual_palette_coords)
    with patch("infrastructure.capture_session.Thread.start", side_effect=RuntimeError("no thread")):
        with pytest.raises(RuntimeError):
            service.append_manual_palette_capture()
    assert service.engine.manual_palette_coords == previous
    assert not service.engine.is_capturing_manual_palette
    assert service.engine._capture_session.finished.is_set()


def test_palette_limit_reserves_pending_colors_and_drains_accepted_updates(service):
    from threading import Event
    from types import SimpleNamespace
    engine = service.engine
    engine.max_manual_palette_colors = 3
    entered, release = Event(), Event()
    measured = []
    def sample(request, cancelled):
        entered.set()
        assert release.wait(2)
        measured.append(request.point)
        return (255, 255, 255)
    with patch("engine.olegpainter.core.mouse.Listener") as listener, patch(
        "engine.olegpainter.palette_capture.WindowSampleRequest.at",
        side_effect=lambda x, y: SimpleNamespace(point=(x, y)),
    ), patch("engine.olegpainter.palette_capture.sample_window_pixel", side_effect=sample):
        service.append_manual_palette_capture()
        callback = listener.call_args.kwargs["on_click"]
        engine.ignore_clicks_until = 0
        try:
            callback(-60, 80, mouse.Button.left, True)
            assert entered.wait(1)
            callback(-90, 80, mouse.Button.left, True)
            callback(-30, 80, mouse.Button.left, True)
        finally:
            release.set()
            engine._palette_capture_work.thread.join(2)
    assert measured == [(-60, 80), (-90, 80)]
    assert len(engine.manual_palette_coords) == 3
    assert engine.manual_palette_coords[1]["hex"] == "#FFFFFF"
    assert not engine.is_capturing_manual_palette
    assert engine._capture_session.finished.is_set()


def test_hotkey_capture_preserves_palette_like_qml_button(service):
    previous = list(service.engine.manual_palette_coords)
    with patch("engine.olegpainter.core.mouse.Listener"):
        service.toggle_manual_palette_capture()
        assert service.engine.is_capturing_manual_palette
        assert service.engine.manual_palette_coords == previous
        service.engine.finish_manual_palette_capture()


def test_config_round_trip_retains_corrected_palette(service):
    service.edit_manual_palette("update", 1, service.manual_palette_snapshot()["revision"], "-90", "80", "FFFFFF")
    config = service.snapshot_painter_config()
    restored = OlegPainter()
    restored.load_config(config)
    assert restored.manual_palette_coords == service.engine.manual_palette_coords
