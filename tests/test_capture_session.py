from threading import Event, Thread

import pytest

from infrastructure.capture_session import CaptureSession
from infrastructure.input_ownership import InputBusyError, InputOwnership


class Listener:
    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.alive = False
        self.stops = 0

    def start(self):
        self.alive = True

    def stop(self):
        self.stops += 1
        self.alive = False

    def is_alive(self):
        return self.alive


def test_old_lease_cannot_release_new_owner():
    ownership = InputOwnership()
    first = ownership.acquire(object(), "first")
    ownership.release(first)
    second = ownership.acquire(object(), "second")
    ownership.release(first)
    assert ownership.current is second
    ownership.release(second)


def test_real_pynput_callback_adapter_preserves_optional_injected_argument():
    # Constructing a native listener does not install a hook until start().
    from pynput import mouse, keyboard
    received = []
    session = CaptureSession("native callback contract", ownership=InputOwnership())
    pointer = session.listener(mouse.Listener, on_click=lambda x, y, button, down: received.append((x, y, down)))
    keys = session.listener(keyboard.Listener, on_press=lambda key: received.append(key))
    try:
        pointer.native.on_click(1, 2, mouse.Button.left, True, True)
        keys.native.on_press(keyboard.Key.esc, True)
        assert received == [(1, 2, True), keyboard.Key.esc]
    finally:
        session.close()


def test_session_stops_all_hooks_and_rejects_queued_callbacks():
    ownership = InputOwnership()
    session = CaptureSession("coordinates", ownership=ownership)
    received = []
    mouse = session.listener(Listener, on_click=received.append)
    keys = session.listener(Listener, on_press=received.append)
    mouse.start()
    keys.start()
    mouse.native.callbacks["on_click"]("before")
    session.close()
    keys.native.callbacks["on_press"]("late")
    session.close()
    assert received == ["before"]
    assert mouse.native.stops == keys.native.stops == 1
    assert ownership.current is None


def test_cancellation_does_not_block_but_waits_for_inflight_callback():
    ownership = InputOwnership()
    session = CaptureSession("slow sample", ownership=ownership)
    entered, finish, closed = Event(), Event(), Event()
    def callback():
        entered.set()
        assert finish.wait(2)
    worker = Thread(target=session.wrap(callback))
    worker.start()
    assert entered.wait(1)
    closer = Thread(target=lambda: (session.close(), closed.set()))
    closer.start()
    try:
        assert closed.wait(.5), "cancellation waited for the sampling callback"
        with pytest.raises(InputBusyError):
            ownership.acquire(object(), "drawing")
    finally:
        finish.set()
        worker.join(2)
        closer.join(2)
    assert not worker.is_alive() and not closer.is_alive()
    assert ownership.current is None


@pytest.mark.parametrize("failure", ["factory", "start", "callback"])
def test_setup_and_callback_failure_release_the_lease(failure):
    ownership = InputOwnership()
    session = CaptureSession("failure", ownership=ownership)
    def fail(*args, **kwargs):
        raise ValueError("failed")
    with pytest.raises(ValueError):
        if failure == "factory":
            session.listener(fail, on_click=lambda: None)
        else:
            listener = session.listener(Listener, on_click=fail)
            if failure == "start":
                listener.native.start = fail
                listener.start()
            else:
                listener.start()
                listener.native.callbacks["on_click"]()
    assert session.closed and ownership.current is None


def test_failed_native_stop_retains_lease_until_retry():
    ownership = InputOwnership()
    session = CaptureSession("capture", ownership=ownership)
    listener = session.listener(Listener, on_click=lambda: pytest.fail("stale callback"))
    listener.start()
    original = listener.native.stop
    listener.native.stop = lambda: (_ for _ in ()).throw(OSError("hook busy"))
    with pytest.raises(RuntimeError, match="hook busy"):
        session.close()
    assert listener.native.callbacks["on_click"]() is False
    with pytest.raises(InputBusyError):
        ownership.acquire(object(), "drawing")
    listener.native.stop = original
    session.close()
    assert ownership.current is None


def test_cancel_request_does_not_wait_for_native_teardown_lock():
    session = CaptureSession("capture", ownership=InputOwnership())
    entered, release, accepted = Event(), Event(), Event()
    def hold_lock():
        with session._lock:
            entered.set()
            assert release.wait(2)
    worker = Thread(target=hold_lock)
    worker.start()
    assert entered.wait(1)
    cancel = Thread(target=lambda: (session.request_cancel(), accepted.set()))
    cancel.start()
    try:
        assert accepted.wait(.3), "stop request waited for teardown lock"
        assert session.closed
    finally:
        release.set()
        worker.join(2)
        cancel.join(2)
        session.close()


def test_cancel_request_rejects_input_without_stopping_native_hooks():
    ownership = InputOwnership()
    session = CaptureSession("capture", ownership=ownership)
    received = []
    listener = session.listener(Listener, on_click=received.append)
    listener.start()
    entered, finish = Event(), Event()
    worker = session.start_worker(lambda: (entered.set(), finish.wait(2)))
    assert entered.wait(1)
    try:
        session.request_cancel()
        assert session.closed and session.cancelled.is_set()
        assert listener.native.stops == 0 and listener.native.alive
        assert listener.native.callbacks["on_click"]("late") is False
        assert not session.publish(lambda: received.append("late result"))
        finish.set()
        worker.join(2)
        assert not worker.is_alive()
        assert ownership.current is not None and not session.finished.is_set()
        with pytest.raises(InputBusyError):
            ownership.acquire(object(), "drawing")
        assert received == []
    finally:
        finish.set()
        worker.join(2)
        session.close()
    assert listener.native.stops == 1
    assert session.finished.is_set() and ownership.current is None


CAPTURES = ["start_hex_coord_capture", "start_app_layer_coord_capture",
            "start_manual_palette_capture", "start_outline_fill_coord_capture",
            "start_manual_mix_background_capture", "start_extra_actions_capture"]


@pytest.fixture
def engine(monkeypatch):
    from engine.olegpainter import core
    monkeypatch.setattr(core.mouse, "Listener", Listener)
    result = core.OlegPainter(status_callback=lambda *_: None)
    yield result
    result._cancel_active_captures()
    result._end_capture_session(wait=True)


@pytest.mark.parametrize("method", CAPTURES)
def test_all_engine_captures_reserve_input_and_release_on_cancel(engine, method):
    from engine.olegpainter.input_emulation import automation_input
    getattr(engine, method)()
    assert automation_input.current is not None
    with pytest.raises(InputBusyError):
        with automation_input.claim(object(), "drawing"):
            pytest.fail("capture and drawing overlapped")
    engine._cancel_active_captures()
    assert engine._capture_session.finished.wait(1)
    assert automation_input.current is None


@pytest.mark.parametrize("method", CAPTURES)
def test_all_engine_captures_reject_another_worker_even_while_paused(engine, method):
    from engine.olegpainter.input_emulation import automation_input
    engine.drawing_enabled = False
    engine.target_app_layer_coords = [(12, 34)]
    with automation_input.claim(object(), "paused drawing"):
        with pytest.raises(InputBusyError):
            getattr(engine, method)()
        assert engine.target_app_layer_coords == [(12, 34)]


def test_hex_late_callback_cannot_mutate_next_capture(engine, monkeypatch):
    from engine.olegpainter import core
    monkeypatch.setattr(engine, "_capture_point", lambda fallback: fallback)
    engine.start_hex_coord_capture()
    old = engine.mouse_listener_hex.native.callbacks["on_click"]
    engine._cancel_active_captures()
    engine.start_hex_coord_capture()
    fresh = engine.mouse_listener_hex.native.callbacks["on_click"]
    old(10, 20, core.mouse.Button.left, True)
    assert engine.hex_input_coord is None
    assert engine.is_waiting_for_hex_click
    fresh(30, 40, core.mouse.Button.left, True)
    assert engine.hex_input_coord == (30, 40)
    assert not engine.is_waiting_for_hex_click
    from engine.olegpainter.input_emulation import automation_input
    assert automation_input.current is None


def test_engine_stop_waits_for_capture_callback_before_releasing_buttons(engine):
    engine._begin_capture_session("slow capture")
    entered, finish, stopped = Event(), Event(), Event()
    def capture():
        entered.set()
        assert finish.wait(2)
    callback = Thread(target=engine._capture_session.wrap(capture))
    callback.start()
    assert entered.wait(1)
    stop = Thread(target=lambda: (engine.stop_script(), stopped.set()))
    stop.start()
    try:
        assert not stopped.wait(.05)
        assert engine._input.backend.events == []
    finally:
        finish.set()
        callback.join(2)
        stop.join(2)
    assert stopped.is_set()
    assert engine._input.backend.events == [("up", "left"), ("up", "right")]


def test_engine_listener_failure_clears_capture_and_reports_error(engine, monkeypatch):
    from engine.olegpainter import core
    from engine.olegpainter.input_emulation import automation_input
    messages = []
    engine.status_callback = messages.append
    monkeypatch.setattr(engine, "_capture_point", lambda **_: (_ for _ in ()).throw(ValueError("bad sample")))
    engine.start_hex_coord_capture()
    with pytest.raises(ValueError, match="bad sample"):
        engine.mouse_listener_hex.native.callbacks["on_click"](1, 2, core.mouse.Button.left, True)
    assert not engine.is_waiting_for_hex_click
    assert automation_input.current is None
    assert messages[-1] == "Ошибка захвата ввода: bad sample"
