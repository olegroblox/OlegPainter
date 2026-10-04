"""Stop acceptance is immediate; stopped means all drawing/release work exited."""
import threading
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication

from engine.olegpainter.drawing_events import DrawingEvent, DrawingPhase
from ui.services.painter_service import PainterService


def test_stop_cancels_qt_picker_and_reports_teardown_failure_until_retry(service, monkeypatch):
    from PySide6.QtWidgets import QWidget
    from ui.overlays.controller import DesktopOverlayController
    from ui.overlays.region_pick import _BasePickOverlay
    monkeypatch.setattr(_BasePickOverlay, "_start_listeners", lambda self: None)
    workspace = QWidget()
    workspace.kalka_overlay = workspace.hud_overlay = None
    desktop = DesktopOverlayController(workspace)
    desktop.bind_service(service)
    service.engine.circle_params_calib = (100, 200, 30, 0)
    desktop.calibrate("ring")
    picker = desktop.picker
    picker._handle_event("down", (10, 20))
    stop_listeners = picker._stop_listeners
    def fail():
        raise OSError("native hook unavailable")
    monkeypatch.setattr(picker, "_stop_listeners", fail)
    states = []
    service.drawingStateChanged.connect(states.append)
    try:
        service.stop()
        pump_until(lambda: not service._stop_pending)
        assert states[-1] == "failed" and service._stop_error
        assert desktop.picker is picker and desktop.mode == "pick"
        assert service.engine.circle_params_calib == (100, 200, 30, 0)
        monkeypatch.setattr(picker, "_stop_listeners", stop_listeners)
        service.stop()
        pump_until(lambda: not service._stop_pending)
        assert states[-1] == "stopped" and not service._stop_error
        assert desktop.picker is None and desktop.mode == ""
        assert service.engine.circle_params_calib == (100, 200, 30, 0)
    finally:
        monkeypatch.setattr(picker, "_stop_listeners", stop_listeners)
        desktop.shutdown()
        workspace.close()
        workspace.deleteLater()


def test_stop_hides_capture_state_before_native_teardown_finishes(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    service.engine._begin_capture_session("HEX test")
    service.engine.is_waiting_for_hex_click = True
    stop = service.engine.stop_script
    def delayed_stop():
        entered.set()
        assert release.wait(2)
        stop()
    monkeypatch.setattr(service.engine, "stop_script", delayed_stop)
    updates = []
    service.captureStateChanged.connect(updates.append)
    service._emit_capture_state()
    assert updates[-1]["active"]
    try:
        service.stop()
        pump_until(entered.is_set)
        assert service._stop_pending and service.engine.is_waiting_for_hex_click
        service._emit_capture_state()
        assert not updates[-1]["active"]
        assert not service._compute_capture_state()["active"]
    finally:
        release.set()
        pump_until(lambda: not service._stop_pending)
    assert service.drawing_phase == DrawingPhase.STOPPED


def pump_until(predicate, timeout=3):
    deadline = time.monotonic() + timeout
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.001)
    assert predicate()
    QApplication.processEvents()


@pytest.fixture
def service(monkeypatch):
    app = QApplication.instance() or QApplication([])
    svc = PainterService()
    # Exercise real stop orchestration without ever sending native input.
    monkeypatch.setattr(svc.engine, "_mouse_up_with_settle", lambda *a, **k: True)
    monkeypatch.setattr(svc.engine, "reset_current_drawing", lambda: pytest.fail("stop rebuilt the image"))
    yield svc
    svc.shutdown()
    svc.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_stop_keeps_qt_alive_and_waits_for_actual_drawing_exit(service, monkeypatch):
    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    thread.start()
    service.engine.drawing_thread = thread
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.RUNNING))
    states, ticks = [], []
    service.drawingStateChanged.connect(states.append)
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    try:
        started = time.monotonic()
        service.stop()
        assert time.monotonic() - started < 0.2
        pump_until(lambda: len(ticks) >= 4)
        assert thread.is_alive()
        assert service._stop_pending and service.drawing_phase == DrawingPhase.STOPPING
        assert service.engine.drawing_thread is thread
        monkeypatch.setattr(service, "_start_in_worker", lambda *a, **k: pytest.fail("restart during stop"))
        service.start_pause()
        service.stop()  # repeated stop must not allocate another worker
        assert states == ["stopping"]
        release.set()
        pump_until(lambda: not service._stop_pending)
        assert states == ["stopping", "stopped"]
        assert service.engine.drawing_thread is None
        assert service.engine._reset_progress_on_next_start
        assert not service._is_drawing
    finally:
        release.set()
        thread.join(timeout=2)
        timer.stop()


def test_stop_during_start_command_cannot_be_undone_by_late_start(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    draw = service.engine.draw_image
    def delayed_start():
        entered.set()
        assert release.wait(2)
        return draw()
    monkeypatch.setattr(service, "_preflight_check", lambda: (True, ""))
    monkeypatch.setattr(service, "_dynamic_brush_should_prompt_learning", lambda: False)
    monkeypatch.setattr(service.engine, "draw_image", delayed_start)
    states = []
    service.drawingStateChanged.connect(states.append)
    try:
        service.start_pause()
        pump_until(entered.is_set)
        service.stop()
        QApplication.processEvents()
        assert service._stop_pending
        release.set()
        pump_until(lambda: not service._stop_pending)
        assert states == ["stopping", "stopped"]
        assert service.engine.drawing_thread is None
        assert service._worker_thread is None
    finally:
        release.set()


def test_device_release_is_background_work_and_failure_is_reported(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    callers, messages = [], []
    service.statusChanged.connect(messages.append)
    def release_input():
        callers.append(threading.get_ident())
        entered.set()
        assert release.wait(2)
        raise RuntimeError("device release failed")
    monkeypatch.setattr(service.engine, "stop_script", release_input)
    try:
        service.stop()
        pump_until(entered.is_set)
        assert service._stop_pending
        assert callers != [threading.get_ident()]
        assert service._stop_worker_thread is not None
        release.set()
        pump_until(lambda: not service._stop_pending)
        assert service.drawing_phase == DrawingPhase.FAILED
        assert any("device release failed" in message for message in messages)
    finally:
        release.set()


def test_cancellation_survives_old_boolean_reset(service):
    service.engine.request_drawing_stop()
    service.engine.stop_flag = False
    assert service.engine._automation_cancelled()
    assert service.engine.draw_image() is False
    service.engine.finish_drawing_stop()
    assert not service.engine._automation_cancelled()


def test_capture_failure_still_releases_both_buttons_and_reports_driver_failure(service, monkeypatch):
    released, messages = [], []
    def fail_capture():
        raise RuntimeError("capture teardown failed")
    def release_button(button):
        released.append(button)
        return button != "left"
    monkeypatch.setattr(service.engine, "_cancel_active_captures", fail_capture)
    monkeypatch.setattr(service.engine, "_mouse_up_with_settle", release_button)
    service.statusChanged.connect(messages.append)
    service.stop()
    pump_until(lambda: not service._stop_pending)
    assert released == ["left", "right"]
    assert service.drawing_phase == DrawingPhase.FAILED
    assert any("capture teardown failed" in message and "отпустить" in message and "left" in message for message in messages)


def test_stop_does_not_recompute_and_next_start_resets_only_progress(service, monkeypatch):
    import numpy as np
    engine = service.engine
    engine.cluster_map = np.array([[0, 0]])
    engine.drawn_mask = np.array([[True, False]])
    engine.current_color_index = 1
    cluster_map = engine.cluster_map
    monkeypatch.setattr(engine, "prepare_image_and_palette", lambda: pytest.fail("unexpected preprocessing"))
    service.stop()
    pump_until(lambda: not service._stop_pending)
    assert engine.cluster_map is cluster_map
    assert engine.drawn_mask[0, 0]
    engine.begin_drawing_run(1)
    # Missing calibration rejects drawing after resetting progress: no input.
    engine.color_picking_method = "hex_field"
    engine.hex_input_coord = None
    assert engine.draw_image() is False
    assert engine.cluster_map is cluster_map
    assert not engine.drawn_mask.any()
    assert engine.current_color_index == 0


def test_shutdown_waits_for_owned_input_and_drawing_workers(service):
    release = threading.Event()
    thread = threading.Thread(target=release.wait, daemon=True)
    thread.start()
    service.engine.drawing_thread = thread
    service.stop()
    timer = threading.Timer(0.03, release.set)
    timer.start()
    try:
        service.shutdown()
        assert not thread.is_alive()
        assert not service._thread_is_running(service._stop_worker_thread)
        assert not service._stop_poll_timer.isActive()
    finally:
        release.set()
        timer.join(timeout=1)
        thread.join(timeout=1)


def test_async_shutdown_keeps_events_alive_until_drawing_exits(service, monkeypatch):
    release = threading.Event()
    drawing = threading.Thread(target=release.wait, daemon=True)
    drawing.start()
    service.engine.drawing_thread = drawing
    finished, ticks, input_calls = [], [], []
    service.shutdownFinished.connect(finished.append)
    monkeypatch.setattr(service, "_runtime_initialized", True)
    monkeypatch.setattr(service.engine, "stop_script",
                        lambda: input_calls.append((drawing.is_alive(), threading.get_ident())))
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(1))
    try:
        timer.start()
        started = time.monotonic()
        assert not service.shutdown(wait=False)
        assert time.monotonic() - started < 0.2
        pump_until(lambda: len(ticks) >= 4)
        assert service.engine._automation_cancelled()
        assert not service.shutdown(wait=False)
        assert drawing.is_alive() and not finished and not input_calls
        release.set()
        pump_until(lambda: service._shutdown_completed)
        assert finished == [""]
        assert len(input_calls) == 1
        assert not input_calls[0][0]
        assert input_calls[0][1] != threading.get_ident()
        assert service.engine.drawing_thread is None
        assert not service._shutdown_poll_timer.isActive()
    finally:
        release.set()
        drawing.join(timeout=1)
        timer.stop()


def test_shutdown_discards_queued_engine_callbacks(service):
    calls = []
    worker = threading.Thread(target=lambda: service._invoke_on_qt(lambda: calls.append(True)))
    worker.start()
    worker.join(timeout=1)
    assert not worker.is_alive()
    assert service.shutdown(wait=False)
    QApplication.processEvents()
    assert not calls
