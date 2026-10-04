from dataclasses import replace
from types import SimpleNamespace

import pytest

from infrastructure.automation_target import TargetChanged, TargetWindowGuard, WindowIdentity
from infrastructure.automation_transport import AutomationTransport
from infrastructure.automation_transport import WindowsInputBackend as NativeBackend
from tests.helpers.input_transport import RecordingInputBackend


class WindowAPI:
    def __init__(self):
        self.windows = {
            10: WindowIdentity(10, 12, 100, "Paint"),
            11: WindowIdentity(11, 12, 100, "Dialog"),
            20: WindowIdentity(20, 22, 200, "Other"),
            30: WindowIdentity(30, 32, 300, "OlegPainter"),
        }
        self.hit = self.active = 10
        self.bounds = (0, 0, 800, 600)
        self.visible = True
        self.focus_calls = []

    def root(self, handle):
        return handle

    def owner_root(self, handle):
        return 10 if handle == 11 else handle

    def at(self, point):
        return self.hit

    def identity(self, handle):
        return self.windows[handle]

    def rect(self, handle):
        return self.bounds

    def available(self, handle):
        return self.visible and handle in self.windows

    def foreground(self):
        return self.active

    def focus(self, handle):
        self.focus_calls.append(handle)
        self.active = handle


@pytest.fixture
def bound():
    api = WindowAPI()
    guard = TargetWindowGuard(api, own_pid=300)
    guard.bind((100, 100, 50, 50))
    return guard, api


@pytest.mark.parametrize("change", ["closed", "hidden", "reused"])
def test_closed_or_minimized_target_stops_input(bound, change):
    guard, api = bound
    if change == "closed":
        del api.windows[10]
    elif change == "hidden":
        api.visible = False
    elif change == "reused":
        api.windows[10] = replace(api.windows[10], thread_id=999)
    with pytest.raises(TargetChanged):
        guard.check((110, 110))


@pytest.mark.parametrize("change", ["focus", "occluded", "moved", "other_same_pid"])
def test_selected_coordinates_are_used_as_is(bound, change):
    # INPUT-SIMPLE-001: перемещение окна, чужая точка и фокус не прерывают ввод.
    guard, api = bound
    if change == "focus":
        api.active = 20
    elif change == "occluded":
        api.hit = 20
    elif change == "moved":
        api.bounds = (1, 0, 801, 600)
    elif change == "other_same_pid":
        api.windows[20] = replace(api.windows[20], process_id=100)
        api.active = 20
    guard.check((110, 110))
    guard.check((5000, 5000))


def test_owned_color_dialog_is_allowed(bound):
    guard, api = bound
    api.hit = api.active = 11
    guard.check((110, 110))


@pytest.mark.parametrize("kind", ["own", "no_region"])
def test_binding_rejects_only_missing_region_and_own_window(kind):
    api = WindowAPI()
    if kind == "own":
        api.hit = 30
    guard = TargetWindowGuard(api, own_pid=300)
    with pytest.raises(TargetChanged):
        guard.bind(None if kind == "no_region" else (0, 0, 100, 100))


@pytest.mark.parametrize("kind", ["desktop", "missing"])
def test_binding_without_window_draws_at_coordinates(kind):
    api = WindowAPI()
    if kind == "desktop":
        api.windows[10] = replace(api.windows[10], class_name="WorkerW")
    else:
        api.hit = 0
    guard = TargetWindowGuard(api, own_pid=300)
    guard.bind((0, 0, 100, 100))
    assert guard.target is None
    guard.check((50, 50))


def test_focus_is_requested_but_refusal_does_not_block():
    api = WindowAPI()
    api.active = 20
    guard = TargetWindowGuard(api, own_pid=300)
    guard.bind((0, 0, 100, 100), focus=False)
    assert not api.focus_calls
    api.focus = lambda *_: (_ for _ in ()).throw(OSError("denied"))
    guard.bind((0, 0, 100, 100))
    guard.check()
    calls = []
    api.focus = lambda handle: calls.append(handle)
    guard.bind((0, 0, 100, 100))
    assert calls == [10]


def test_native_backend_guards_every_producing_command_without_blocking_release(monkeypatch):
    import keyboard
    # conftest replaces the default device factory. Use the actual class saved by
    # the module under test before that fixture runs.
    backend = NativeBackend()
    checks, events = [], []
    def reject(*_):
        checks.append(True)
        raise TargetChanged("lost")
    backend._target = SimpleNamespace(check=reject, api=SimpleNamespace(cursor=lambda: (0, 0)))
    monkeypatch.setattr(backend, "_send_mouse", lambda *args: events.append(args))
    monkeypatch.setattr(keyboard, "release", lambda key: events.append(("release", key)))
    for action in (lambda: backend.move(1, 2), lambda: backend.button("left", True),
                   lambda: backend.send_keys("ctrl+a"), lambda: backend.write_text("1"),
                   lambda: backend.key("shift", True)):
        with pytest.raises(TargetChanged):
            action()
    assert len(checks) == 5 and not events
    backend.button("left", False)
    backend.key("shift", False)
    assert len(events) == 2


def test_target_loss_in_wait_latches_failure_and_releases_engine(monkeypatch):
    from engine.olegpainter.core import OlegPainter
    from engine.olegpainter.drawing_events import DrawingPhase
    events = []
    engine = OlegPainter(drawing_event_callback=events.append)
    engine.pen_button_delay = engine.mouse_release_settle = 0
    def lost():
        raise TargetChanged("Окно рисования закрыто")
    monkeypatch.setattr(engine._input.backend, "check_target", lost)
    def draw():
        engine._input.button_down()
        engine._sleep_with_abort(1)
        pytest.fail("wait did not detect lost target")
    monkeypatch.setattr(engine, "_draw_colors", draw)
    engine.draw_colors_thread()
    assert events[-1].phase == DrawingPhase.FAILED
    assert "закрыто" in events[-1].error
    assert engine._input.backend.events == [("down", "left"), ("up", "left"), ("up", "right")]
    assert not engine._input._active


def test_recorded_action_failure_names_actions_in_final_error(monkeypatch):
    from engine.olegpainter.core import OlegPainter
    from engine.olegpainter.drawing_events import DrawingPhase
    from engine.olegpainter.translations import tr
    events, statuses = [], []
    engine = OlegPainter(drawing_event_callback=events.append, status_callback=statuses.append)
    engine.pen_button_delay = engine.mouse_release_settle = 0
    engine._actions_bucket("pre").update(enabled=True, events=[
        {"device": "mouse", "action": "click", "x": 1317, "y": 145, "button": "left"}])
    def outside(*_):
        raise TargetChanged("Окно рисования закрыто.")
    monkeypatch.setattr(engine._input.backend, "move", outside)
    monkeypatch.setattr(engine, "_draw_colors", lambda: engine._play_actions_sequence("pre"))
    engine.draw_colors_thread()
    expected = tr("status_error_extra_actions_playback_slot", slot=engine._actions_slot_label("pre"),
                  reason="Окно рисования закрыто.")
    assert events[-1].phase == DrawingPhase.FAILED
    assert events[-1].error == expected
    assert "error: " + expected in statuses
    assert not engine._input._active


def test_transport_never_resumes_after_context_loss_even_if_focus_returns():
    backend = RecordingInputBackend()
    transport = AutomationTransport(lambda: False, backend)
    transport.begin((0, 0, 100, 100))
    backend.check_target = lambda: (_ for _ in ()).throw(TargetChanged("lost"))
    with pytest.raises(TargetChanged):
        transport.check_target()
    backend.check_target = lambda: None
    with pytest.raises(TargetChanged):
        transport.move(1, 2)
    transport.button_up()
    transport.end()
    assert backend.events == [("up", "left")]


def test_live_target_is_rechecked_only_after_a_short_interval(bound, monkeypatch):
    guard, api = bound
    clock = [100.0]
    monkeypatch.setattr("infrastructure.automation_target.time.perf_counter", lambda: clock[0])
    guard.check()
    del api.windows[10]
    guard.check()  # within RECHECK_AFTER of a successful check: no system call
    clock[0] += guard.RECHECK_AFTER * 1.5
    with pytest.raises(TargetChanged):
        guard.check()
