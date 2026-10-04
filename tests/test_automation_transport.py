import ast
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from infrastructure.automation_transport import (
    AutomationTransport, InputCancelled, WindowsInputBackend, absolute_pixel,
)
from tests.helpers.input_transport import RecordingInputBackend


def test_cancel_blocks_all_producing_commands_but_allows_releases():
    cancelled = Event()
    backend = RecordingInputBackend()
    transport = AutomationTransport(cancelled.is_set, backend)
    transport.button_down()
    cancelled.set()
    for action in (
        lambda: transport.move(50, 70), lambda: transport.button_down(),
        lambda: transport.send_keys("ctrl+a"), lambda: transport.write_text("123"),
        lambda: transport.key_down("shift"),
    ):
        with pytest.raises(InputCancelled):
            action()
    transport.button_up()
    transport.key_up("shift")
    assert backend.events == [("down", "left"), ("up", "left"), ("key_up", "shift")]
    assert transport.failure is None


def test_cancel_between_characters_does_not_type_remaining_text():
    cancelled = Event()
    backend = RecordingInputBackend()
    def write(character):
        backend.events.append(character)
        cancelled.set()
    backend.write_text = write
    transport = AutomationTransport(cancelled.is_set, backend)
    with pytest.raises(InputCancelled):
        transport.write_text("ABC123")
    assert backend.events == ["A"]


def test_device_failure_is_latched_even_if_caller_catches_it():
    backend = RecordingInputBackend()
    def fail_move(*point):
        raise OSError("device disconnected")
    backend.move = fail_move
    transport = AutomationTransport(lambda: False, backend)
    with pytest.raises(OSError, match="disconnected"):
        transport.move(20, 30)
    with pytest.raises(OSError, match="disconnected"):
        transport.button_down()
    transport.button_up()
    assert backend.events == [("up", "left")]
    transport.begin()
    transport.button_down()
    assert backend.events[-1] == ("down", "left")


@pytest.mark.parametrize("origin,extent", [(0, 1920), (0, 1440), (-2560, 4480), (-2160, 4320)])
def test_every_physical_pixel_round_trips_through_absolute_mapping(origin, extent):
    for pixel in range(origin, origin + extent):
        normalized = absolute_pixel(pixel, origin, extent)
        assert 0 <= normalized <= 65535
        assert (normalized * extent // 65536) + origin == pixel
    for pixel in (origin - 1, origin + extent):
        with pytest.raises(ValueError):
            absolute_pixel(pixel, origin, extent)


def test_native_backend_reports_driver_rejection_and_uses_one_virtual_mapping(monkeypatch):
    import interception
    from interception.constants import MouseFlag
    strokes = []
    success = True
    def send(device, stroke):
        strokes.append(stroke)
        return SimpleNamespace(succeeded=success)
    monkeypatch.setattr(interception.inputs, "_g_context", SimpleNamespace(valid=True, mouse=11, send=send))
    backend = WindowsInputBackend()
    # Test device flags/result in isolation from native window inspection.
    backend._target = SimpleNamespace(check=lambda *_: None, api=SimpleNamespace(cursor=lambda: (0, 0)))
    backend._desktop = (-1920, 0, 4480, 1440)
    backend.move(-100, 90)
    assert strokes[-1].flags == int(MouseFlag.MOUSE_MOVE_ABSOLUTE | MouseFlag.MOUSE_VIRTUAL_DESKTOP | MouseFlag.MOUSE_MOVE_NOCOALESCE)
    assert strokes[-1].x == absolute_pixel(-100, -1920, 4480)
    backend.button("left", True)
    assert strokes[-1].flags == 0 and strokes[-1].x == strokes[-1].y == 0
    success = False
    with pytest.raises(RuntimeError, match="отклонил"):
        backend.button("left", False)


def test_engine_has_no_direct_native_event_senders():
    # Future strategies must cross the same cancellation/error boundary.
    prohibited = {
        "interception": {"click", "move_to", "move_relative", "mouse_down", "mouse_up"},
        "keyboard": {"send", "write", "press", "release"},
    }
    root = Path(__file__).resolve().parents[1] / "engine" / "olegpainter"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8-sig"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                assert node.attr not in prohibited.get(node.value.id, set()), f"{path.name}:{node.lineno}"
            if isinstance(node, ast.Attribute):
                assert node.attr != "_g_context", f"{path.name}:{node.lineno} bypasses transport"


def test_swallowed_device_failure_still_publishes_failed_drawing(monkeypatch):
    from engine.olegpainter.core import OlegPainter
    from engine.olegpainter.drawing_events import DrawingPhase
    events = []
    engine = OlegPainter(drawing_event_callback=events.append)
    engine.pen_button_delay = 0
    def fail_move(*args):
        raise OSError("mouse disconnected")
    monkeypatch.setattr(engine._input.backend, "move", fail_move)
    def draw():
        try:
            engine._move_abs(10, 20)
        except OSError:
            pass  # Legacy strategy catches its local failure.
        return DrawingPhase.COMPLETED
    monkeypatch.setattr(engine, "_draw_colors", draw)
    engine.draw_colors_thread()
    assert events[-1].phase == DrawingPhase.FAILED
    assert "disconnected" in events[-1].error
    assert engine._input.backend.events == [("up", "left"), ("up", "right")]


def test_cancelled_input_publishes_stopped_and_releases_buttons(monkeypatch):
    from engine.olegpainter.core import OlegPainter
    from engine.olegpainter.drawing_events import DrawingPhase
    events = []
    engine = OlegPainter(drawing_event_callback=events.append)
    engine._drawing_cancel.set()
    monkeypatch.setattr(engine, "_draw_colors", lambda: engine._move_abs(10, 20))
    engine.draw_colors_thread()
    assert events[-1].phase == DrawingPhase.STOPPED
    assert not events[-1].error
    assert engine._input.backend.events == [("up", "left"), ("up", "right")]
