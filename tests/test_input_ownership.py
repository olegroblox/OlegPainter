"""Competing producers and stale stop cleanup must not share device ownership."""
from threading import Event, Thread
from unittest.mock import Mock

import pytest

from application.brush_learning import BrushLearningJob
from engine.olegpainter.core import OlegPainter
from engine.olegpainter.drawing_events import DrawingPhase
from engine.olegpainter import input_emulation
from infrastructure.input_ownership import InputBusyError, InputOwnership


def test_competing_owner_and_nested_run_are_rejected():
    inputs = InputOwnership()
    owner = object()
    with inputs.claim(owner, "рисование") as lease:
        assert inputs.current is lease
        for contender in (object(), owner):
            with pytest.raises(InputBusyError):
                with inputs.claim(contender, "обучение"):
                    pytest.fail("two producers acquired input")
        assert not inputs.cleanup(object(), lambda: pytest.fail("foreign release"))
    assert inputs.current is None


def test_release_error_without_message_still_blocks_new_input():
    inputs = InputOwnership()
    def release():
        raise RuntimeError()
    with pytest.raises(RuntimeError):
        inputs.cleanup(object(), release)
    with pytest.raises(InputBusyError, match="RuntimeError"):
        with inputs.claim(object(), "рисование"):
            pytest.fail("empty error message cleared the failure")
    assert inputs.cleanup(object(), lambda: None)
    with inputs.claim(object(), "рисование"):
        pass


def test_new_run_cannot_enter_while_idle_cleanup_waits_for_device():
    inputs, entered, proceed = InputOwnership(), Event(), Event()
    def release():
        entered.set()
        assert proceed.wait(3)
    worker = Thread(target=lambda: inputs.cleanup(object(), release))
    worker.start()
    try:
        assert entered.wait(3)
        with pytest.raises(InputBusyError, match="освобождается"):
            with inputs.claim(object(), "новая операция"):
                pytest.fail("cleanup still active")
    finally:
        proceed.set()
        worker.join(3)
    assert not worker.is_alive()
    with inputs.claim(object(), "новая операция"):
        pass


def test_release_failure_blocks_producers_until_successful_stop_cleanup():
    engine = OlegPainter()
    engine._mouse_up_with_settle = Mock(return_value=False)
    with pytest.raises(RuntimeError, match="отпустить"):
        with engine._owned_automation_input("рисование"):
            pass
    assert engine._mouse_up_with_settle.call_count == 2
    with pytest.raises(InputBusyError, match="Нажмите остановку"):
        with OlegPainter()._owned_automation_input("обучение"):
            pytest.fail("release error was ignored")
    engine._mouse_up_with_settle = Mock(return_value=True)
    engine.stop_script()
    engine.stop_flag = False
    engine._drawing_cancel.clear()
    with engine._owned_automation_input("рисование"):
        pass


def test_rejected_drawing_and_learning_do_not_release_active_owners_buttons(monkeypatch):
    held = object()
    releases, draws, events = [], [], []
    monkeypatch.setattr(OlegPainter, "_mouse_up_with_settle", lambda self, button: releases.append(button) or True)
    engine = OlegPainter(drawing_event_callback=events.append)
    engine._draw_colors = lambda: draws.append(True)
    job = BrushLearningJob(engine.get_config())
    monkeypatch.setattr(OlegPainter, "learn_dynamic_brush_profile", lambda self: pytest.fail("foreign learning"))
    with input_emulation.automation_input.claim(held, "активная операция"):
        engine.draw_colors_thread()
        job.run()
        engine.stop_script()  # even a late stop from the rejected engine is isolated
        assert not draws and not releases
    assert events[-1].phase == DrawingPhase.FAILED
    assert "Ввод занят" in events[-1].error
    assert "Ввод занят" in job.error and job.profile is None


def test_completion_waits_for_release_and_keeps_ownership_until_then():
    events, entered, proceed = [], Event(), Event()
    engine = OlegPainter(drawing_event_callback=events.append)
    engine._draw_colors = lambda: DrawingPhase.COMPLETED
    engine.drawing_progress_snapshot = lambda **kw: None
    def release(button):
        if button == "left":
            entered.set()
            assert proceed.wait(3)
        return True
    engine._mouse_up_with_settle = release
    worker = Thread(target=engine.draw_colors_thread)
    worker.start()
    try:
        assert entered.wait(3)
        assert not events
        with pytest.raises(InputBusyError):
            with input_emulation.automation_input.claim(object(), "другая операция"):
                pytest.fail("release has not finished")
    finally:
        proceed.set()
        worker.join(3)
    assert not worker.is_alive()
    assert events[-1].phase == DrawingPhase.COMPLETED
    assert input_emulation.automation_input.current is None
