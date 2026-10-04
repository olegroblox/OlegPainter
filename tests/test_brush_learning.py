"""Calibration owns a snapshot and input until actual worker exit. No native input."""
from copy import deepcopy
from threading import Event, get_ident
import time

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication

from application.controller import ApplicationController
from engine.olegpainter.core import OlegPainter
from ui.services.painter_service import PainterService


def pump(predicate):
    end = time.monotonic() + 10  # the "changed" case takes ~4 s on a loaded machine
    while not predicate() and time.monotonic() < end:
        QApplication.processEvents()
        time.sleep(.002)
    assert predicate()
    QApplication.processEvents()


def calibration(engine):
    return engine._build_dynamic_brush_calibration_payload(
        [dict(value=v, point_reach_cells=r, stroke_reach_h_cells=r, stroke_reach_v_cells=r,
              half_width_cells=r, half_height_cells=r, area_cells=3.14*r*r,
              shape_hint="circle", confidence=.9, density=1., brush_px=1.) for v, r in ((.05, 1.), (1.2, 10.))],
        shape="circle", shape_confidence=.9,
        validation=dict(target_rect=None, outside_pixels=0, inside_coverage=1., passed=True))


@pytest.fixture
def service(monkeypatch):
    app = QApplication.instance() or QApplication([])
    # Patch the class, not just the live instance: learning creates an owned clone.
    monkeypatch.setattr(OlegPainter, "_mouse_up_with_settle", lambda *a, **k: True)
    svc = PainterService()
    svc.engine.draw_region = (100, 100, 200, 200)
    svc.engine.dynamic_brush_scratch_zone = (400, 100, 240, 240)
    svc.engine.dynamic_brush_coord = (30, 30)
    yield svc
    svc.shutdown()
    svc.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_learning_keeps_qt_alive_and_publishes_only_after_release(service, monkeypatch):
    entered, release, release_entered, release_done = Event(), Event(), Event(), Event()
    workers, changes, ticks = [], [], []
    old = service.engine.dynamic_brush_profile

    def learn(engine, **kwargs):
        workers.append((engine, get_ident()))
        entered.set()
        assert release.wait(3)
        return calibration(engine)

    def release_mouse(engine, button):
        release_entered.set()
        assert release_done.wait(3)
        return True

    monkeypatch.setattr(OlegPainter, "_run_dynamic_brush_calibration", learn)
    monkeypatch.setattr(OlegPainter, "_mouse_up_with_settle", release_mouse)
    service.dynamicBrushSettingsChanged.connect(lambda _: changes.append(get_ident()))
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    controller = ApplicationController(service)
    try:
        start = time.monotonic()
        service.start_brush_learning()
        assert time.monotonic() - start < .2
        pump(lambda: entered.is_set() and len(ticks) >= 4)
        assert not controller.state.can_edit_source and not controller.state.can_start
        assert service.engine.dynamic_brush_profile is old
        assert workers[0][0] is not service.engine and workers[0][1] != get_ident()
        with pytest.raises(RuntimeError):
            service.set_dynamic_brush_enabled(True)
        with pytest.raises(RuntimeError):
            service.start_brush_learning()
        release.set()
        pump(release_entered.is_set)
        assert service.brush_learning_active and service.engine.dynamic_brush_profile is old
        release_done.set()
        pump(lambda: not service.brush_learning_active)
        assert service.brush_learning_snapshot()["error"] == ""
        assert service.engine.dynamic_brush_profile["valid"]
        assert service.engine.dynamic_brush_calibration == service.engine.dynamic_brush_profile["cached_calibration"]
        assert changes and all(thread_id == get_ident() for thread_id in changes)
        assert controller.state.can_edit_source
    finally:
        release.set()
        release_done.set()
        timer.stop()
        controller.close(save=False)


@pytest.mark.parametrize("operation", ["cancel", "stop", "shutdown", "changed"])
def test_cancel_close_and_stale_result_preserve_previous_profile(service, monkeypatch, operation):
    entered, release = Event(), Event()
    previous = {"valid": False, "sentinel": "previous"}
    service.engine.dynamic_brush_profile = deepcopy(previous)

    def learn(engine, **kwargs):
        entered.set()
        assert release.wait(3)
        return calibration(engine)

    monkeypatch.setattr(OlegPainter, "_run_dynamic_brush_calibration", learn)
    try:
        service.start_brush_learning()
        pump(entered.is_set)
        if operation == "cancel":
            service.cancel_brush_learning()
        elif operation == "stop":
            service.stop()
        elif operation == "shutdown":
            service.shutdown(wait=False)
            assert not service._shutdown_completed
        else:
            service.engine.draw_region = (20, 20, 200, 200)
        assert service.brush_learning_active
        release.set()
        pump(lambda: not service.brush_learning_active)
        assert service.engine.dynamic_brush_profile == previous
        if operation == "changed":
            assert "изменились" in service.brush_learning_snapshot()["error"]
        if operation == "shutdown":
            pump(lambda: service._shutdown_completed)
    finally:
        release.set()


def test_release_failure_attempts_both_buttons_and_rejects_profile(service, monkeypatch):
    monkeypatch.setattr(OlegPainter, "_run_dynamic_brush_calibration", lambda engine, **kw: calibration(engine))
    buttons = []
    monkeypatch.setattr(OlegPainter, "_mouse_up_with_settle", lambda engine, button: buttons.append(button) or False)
    service.start_brush_learning()
    pump(lambda: not service.brush_learning_active)
    assert buttons == ["left", "right"]
    assert service.engine.dynamic_brush_profile is None
    assert "отпустить" in service.brush_learning_snapshot()["error"]


@pytest.mark.parametrize("rect", [None, (1, 2, 0, 5), (1, 2, float("nan"), 5), (1, 2, 5)])
def test_invalid_scratch_never_defaults_to_drawing_area(service, monkeypatch, rect):
    monkeypatch.setattr(OlegPainter, "_run_dynamic_brush_calibration", lambda *a, **kw: pytest.fail("unsafe learning"))
    service.engine.dynamic_brush_scratch_zone = rect
    with pytest.raises(ValueError):
        service.start_brush_learning()
    assert not service.brush_learning_active
    if rect is None:
        assert service.engine.learn_dynamic_brush_profile() is False


def test_atomic_settings_validation_and_capture_invalidation(service):
    before = deepcopy(service.engine.get_dynamic_brush_settings())
    for patch in ({"enabled": "false"}, {"min_value": 3}, {"step_value": float("nan")}, {"control_mode": "oops"},
                  {"min_value": 0, "default_value": 0}):
        with pytest.raises(ValueError):
            service.update_brush_settings(patch)
        assert service.engine.get_dynamic_brush_settings() == before
    service.update_brush_settings(dict(min_value=1., max_value=10., default_value=2., step_value=1.))
    assert service.engine.dynamic_brush_min_value == 1
    service.engine.dynamic_brush_profile = {"valid": True}
    service.engine.dynamic_brush_calibration = {"old": True}
    service.apply_brush_capture("slider", (-200, 30, 100, 10))
    assert service.engine.dynamic_brush_slider_params == ("horizontal", 35, -100, -200)
    assert service.engine.dynamic_brush_profile is None and service.engine.dynamic_brush_calibration is None
    service.apply_brush_capture("point", (40, 50), 2.)
    service.apply_brush_capture("point", (50, 60), 2.)
    assert service.engine.dynamic_brush_points == [dict(x=50, y=60, value=2.)]


def test_cancel_event_stops_retry_verification_and_default_restore(service, monkeypatch):
    engine = service.engine
    calls = []
    monkeypatch.setattr(engine, "_dynamic_brush_v2_clean_spots", lambda *a: [(500, 100)] * 12)
    monkeypatch.setattr(engine, "_dynamic_brush_can_pick_calibration_color", lambda: False)

    def probe(*args, **kwargs):
        calls.append("probe")
        engine._drawing_cancel.set()
        return None

    monkeypatch.setattr(engine, "_dynamic_brush_v2_probe", probe)
    monkeypatch.setattr(engine, "_dynamic_brush_v2_verify", lambda *a, **k: pytest.fail("verify after cancel"))
    monkeypatch.setattr(engine, "_apply_dynamic_brush_value", lambda *a, **k: pytest.fail("input after cancel"))
    assert engine._run_dynamic_brush_calibration(calibration_region=engine.dynamic_brush_scratch_zone) is None
    assert calls == ["probe"]


def test_clean_spots_use_available_area_without_clipping_probe_windows(service, monkeypatch):
    from PIL import Image
    import math
    monkeypatch.setattr("engine.olegpainter.dynamic_brush.capture_screen", lambda **kw: Image.new("RGB", (752, 618), "white"))
    engine = service.engine
    rect = (-1000, 200, 752, 618)
    half = engine._dynamic_brush_patch_half(region=rect)
    points = engine._dynamic_brush_v2_clean_spots(rect, half, 11)
    assert len(points) >= 6  # five base probes and at least one verification
    for i, (x, y) in enumerate(points):
        assert rect[0]+half <= x <= rect[0]+rect[2]-half
        assert rect[1]+half <= y <= rect[1]+rect[3]-half
        assert all(math.dist((x, y), other) >= half*1.4 for other in points[:i])


@pytest.mark.parametrize("operation", ["click", "drag"])
def test_cancel_during_control_press_releases_and_stops_remaining_input(service, monkeypatch, operation):
    engine, events = service.engine, []
    monkeypatch.setattr(engine, "_click_abs", lambda *point: events.append(("move", point)))
    monkeypatch.setattr(engine, "_mouse_up_with_settle", lambda *a: events.append(("up",)) or True)

    def down(*args, **kwargs):
        events.append(("down",))
        engine._drawing_cancel.set()

    monkeypatch.setattr(engine._input.backend, "button", lambda button, pressed: down() if pressed else None)
    if operation == "click":
        engine._ui_click_at(50, 60, clicks=3, settle=0)
    else:
        engine._ui_drag(50, 60, 800, 900, settle=0)
    assert events == [("move", (50, 60)), ("down",), ("up",)]


def test_cancel_after_select_all_does_not_type_or_submit_size(service, monkeypatch):
    engine, keys = service.engine, []
    monkeypatch.setattr(engine, "_ui_click_at", lambda *a, **k: None)

    def send(key):
        keys.append(key)
        engine._drawing_cancel.set()

    monkeypatch.setattr(engine._input.backend, "send_keys", send)
    monkeypatch.setattr(engine._input.backend, "write_text", lambda *a: pytest.fail("typing after cancel"))
    assert not engine._apply_dynamic_brush_value(.5, force=True)
    assert keys == ["ctrl+a"]


def test_verification_requires_new_stamp_even_for_existing_preset(service, monkeypatch):
    engine = service.engine
    engine.brush_size = 1
    engine.dynamic_brush_control_mode = "points"
    engine.dynamic_brush_points = [dict(x=0, y=0, value=v) for v in (1., 2., 3.)]
    samples = [dict(value=v, point_reach_cells=r) for v, r in ((1., 2.), (2., 4.), (3., 8.))]
    probed = []
    monkeypatch.setattr(engine, "_dynamic_brush_v2_probe", lambda *a, **k: probed.append(a) or None)
    result = engine._dynamic_brush_v2_verify(samples, iter([(400, 300)]), 50)
    assert probed and not result["passed"]


def test_capture_images_park_cursor_outside_probe_before_and_after(service, monkeypatch):
    from PIL import Image
    from PIL import ImageDraw
    engine, events = service.engine, []
    before = Image.new("RGB", (80, 80), "white")
    after = before.copy()
    ImageDraw.Draw(after).ellipse((30, 30, 50, 50), fill="black")
    images = iter((before, after))
    monkeypatch.setattr(engine, "_click_abs", lambda x, y: events.append(("park", x, y)))
    monkeypatch.setattr(engine, "_grab_patch", lambda *a: events.append(("capture",)) or next(images))
    monkeypatch.setattr(engine, "_dynamic_brush_execute_probe", lambda *a, **k: events.append(("stamp",)) or True)
    result = engine._dynamic_brush_v2_stamp_and_measure(500, 500, 40, region_rect=(400, 400, 200, 200))
    assert result and result["radius_px"] > 8
    assert events == [("park", 401, 401), ("capture",), ("stamp",), ("park", 401, 401), ("capture",)]


def test_slider_range_can_be_edited_after_switching_to_text(service):
    service.update_brush_settings(dict(control_mode="slider", min_value=0., max_value=1., default_value=0., step_value=.01))
    service.set_dynamic_brush_control_mode("text")
    assert service.engine.dynamic_brush_control_mode == "text"
    service.update_brush_settings(dict(text_auto=False))  # a manually given range must be valid
    with pytest.raises(ValueError, match="положительный"):
        service.start_brush_learning()
    assert not service.brush_learning_active
    service.update_brush_settings(dict(min_value=1., max_value=20., default_value=2., step_value=1.))
    assert service.engine.dynamic_brush_min_value == 1.
