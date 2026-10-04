"""Execution outcomes cannot be inferred from text or revive an obsolete run."""
import time
import threading
from dataclasses import replace

import numpy as np
import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from engine.olegpainter.core import OlegPainter
from engine.olegpainter.drawing_events import DrawingEvent, DrawingPhase
from ui.services.painter_service import PainterService


@pytest.fixture
def service(monkeypatch):
    app = QApplication.instance() or QApplication([])
    service = PainterService()
    monkeypatch.setattr(service, "_preflight_check", lambda: (True, ""))
    monkeypatch.setattr(service, "_dynamic_brush_should_prompt_learning", lambda: False)
    yield service
    service.shutdown()
    service.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def drain_until(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        QApplication.processEvents()
        time.sleep(0.001)
    assert predicate(), "worker did not finish"
    QApplication.processEvents()


def test_fast_completion_is_not_overwritten_by_worker_success(service, monkeypatch):
    states = []
    service.drawingStateChanged.connect(states.append)
    def draw():
        engine = service.engine
        engine._emit_drawing_event(DrawingPhase.RUNNING)
        engine._emit_drawing_event(DrawingPhase.COMPLETED)
        return True
    monkeypatch.setattr(service.engine, "draw_image", draw)
    service.start_pause()
    drain_until(lambda: service._worker_thread is None)
    assert states == ["started", "completed"]
    assert service.drawing_phase == DrawingPhase.COMPLETED
    assert not service._is_drawing


def test_start_command_exception_reaches_typed_state(service, monkeypatch):
    events, messages = [], []
    service.drawingEvent.connect(events.append)
    service.statusChanged.connect(messages.append)
    def fail():
        raise RuntimeError("device unavailable")
    monkeypatch.setattr(service.engine, "draw_image", fail)
    service.start_pause()
    drain_until(lambda: service._worker_thread is None)
    assert events[-1].phase == DrawingPhase.FAILED
    assert "device unavailable" in events[-1].error
    assert any("device unavailable" in message for message in messages)
    assert not service._is_drawing


def test_old_run_and_late_result_cannot_change_new_run(service, monkeypatch):
    callbacks = []
    monkeypatch.setattr(service, "_start_in_worker",
                        lambda fn, **kwargs: callbacks.append(kwargs) or True)
    service.start_pause()
    first = service._drawing_run_id
    service._on_drawing_event(DrawingEvent(first, DrawingPhase.COMPLETED))
    service.start_pause()
    second = service._drawing_run_id
    service._on_drawing_event(DrawingEvent(second, DrawingPhase.RUNNING))
    service._on_drawing_event(DrawingEvent(first, DrawingPhase.STOPPED))
    callbacks[0]["on_error"]("late failure")
    callbacks[0]["on_result"](False)
    assert second > first
    assert service.drawing_phase == DrawingPhase.RUNNING
    assert service._is_drawing
    service._on_drawing_event(DrawingEvent(second, DrawingPhase.COMPLETED))
    callbacks[1]["on_result"](True)
    service._on_drawing_event(DrawingEvent(second, DrawingPhase.RUNNING))
    assert service.drawing_phase == DrawingPhase.COMPLETED


def test_status_text_has_no_execution_side_effect(service):
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.RUNNING))
    for message in ("drawing_complete", "drawing_stopped", "Рисование завершено"):
        service._on_status_from_engine(message)
    assert service._is_drawing
    assert service.drawing_phase == DrawingPhase.RUNNING


def test_pause_and_resume_do_not_allocate_another_run(service, monkeypatch):
    callbacks = []
    monkeypatch.setattr(service, "_start_in_worker",
                        lambda fn, **kwargs: callbacks.append(kwargs) or True)
    service.start_pause()
    run_id = service._drawing_run_id
    service._on_drawing_event(DrawingEvent(run_id, DrawingPhase.RUNNING))
    service.start_pause()
    service._on_drawing_event(DrawingEvent(run_id, DrawingPhase.PAUSED))
    callbacks[-1]["on_result"](False)
    assert service._is_paused and service._is_drawing
    service.start_pause()
    service._on_drawing_event(DrawingEvent(run_id, DrawingPhase.RUNNING))
    assert not service._is_paused and service._is_drawing
    assert service._drawing_run_id == run_id


def test_stop_gate_rejects_completion_and_late_progress(service, monkeypatch):
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.RUNNING))
    service._stop_requested = True
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.COMPLETED))
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.STOPPED))
    monkeypatch.setattr(service, "_update_progress_metrics", lambda **_: pytest.fail("late progress"))
    service._on_progress_from_engine(100)
    assert service.drawing_phase == DrawingPhase.STOPPED


def test_old_live_thread_prevents_restart_and_keeps_stop_gate(service, monkeypatch):
    class Thread:
        def is_alive(self):
            return True
    service.engine.drawing_thread = Thread()
    service._stop_requested = True
    monkeypatch.setattr(service, "_start_in_worker", lambda *a, **k: pytest.fail("started another worker"))
    service.start_pause()
    assert service._stop_requested
    assert service._drawing_run_id == 0
    service.engine.drawing_thread = None


@pytest.fixture
def engine(monkeypatch):
    events, releases = [], []
    engine = OlegPainter(status_callback=lambda _: None, drawing_event_callback=events.append)
    engine.drawing_run_id = 42
    engine.cluster_map = np.array([[0]])
    engine.drawn_mask = np.array([[False]])
    engine.color_palette = [("#000000", 0, 1, (0, 0, 0))]
    engine.telemetry_enabled = False
    engine.post_draw_repair_enabled = False
    monkeypatch.setattr(engine, "_refresh_screen_metrics", lambda: None)
    monkeypatch.setattr(engine, "_notify_post_draw_repair", lambda *_: None)
    monkeypatch.setattr(engine, "_semantic_split_phases", lambda: None)
    monkeypatch.setattr(engine, "_record_drawing_duration", lambda: None)
    monkeypatch.setattr(engine, "_mouse_up_with_settle", lambda button="left", **_: releases.append(button) or True)
    def paint():
        engine.drawn_mask[:] = True
    monkeypatch.setattr(engine, "_execute_drawing_sequentially", paint)
    return engine, events, releases


@pytest.mark.parametrize("outcome", ["complete", "stop_in_repair", "repair_error", "incomplete"])
def test_terminal_event_covers_repair_cancellation_and_failure(engine, monkeypatch, outcome):
    painter, events, releases = engine
    def repair(_):
        assert not events, "completion was published before repair"
        if outcome == "stop_in_repair":
            painter.stop_flag = True
        elif outcome == "repair_error":
            raise RuntimeError("repair failed")
    monkeypatch.setattr(painter, "_run_post_draw_repair", repair)
    if outcome == "incomplete":
        monkeypatch.setattr(painter, "_execute_drawing_sequentially", lambda: None)
    painter.draw_colors_thread()
    expected = {"complete": DrawingPhase.COMPLETED, "stop_in_repair": DrawingPhase.STOPPED,
                "repair_error": DrawingPhase.FAILED, "incomplete": DrawingPhase.FAILED}[outcome]
    assert len(events) == 1 and events[0].phase == expected
    assert events[0].run_id == 42
    assert not painter.drawing_enabled and painter.drawing_start_time is None
    if expected == DrawingPhase.FAILED:
        assert events[0].error
        assert releases == ["left", "right"]


def test_real_draw_command_emits_start_before_even_an_instant_thread(engine, monkeypatch):
    from engine.olegpainter import core
    painter, events, _ = engine
    painter.draw_region = (0, 0, 1, 1)
    painter.color_picking_method = "hex_field"
    painter.hex_input_coord = (2, 2)
    monkeypatch.setattr(painter, "_run_post_draw_repair", lambda _: None)
    monkeypatch.setattr(painter, "_dynamic_brush_requested_for_current_draw", lambda: False)
    class InstantThread:
        def __init__(self, *, target, args, **_):
            self.target, self.args = target, args
        def is_alive(self):
            return False
        def start(self):
            self.target(*self.args)
    monkeypatch.setattr(core.threading, "Thread", InstantThread)
    assert painter.draw_image() is True
    assert [event.phase for event in events] == [DrawingPhase.RUNNING, DrawingPhase.COMPLETED]
    assert all(event.run_id == 42 for event in events)


def test_thread_start_failure_does_not_turn_retry_into_pause(engine, monkeypatch):
    from engine.olegpainter import core
    painter, _, _ = engine
    painter.draw_region = (0, 0, 1, 1)
    painter.color_picking_method = "hex_field"
    painter.hex_input_coord = (2, 2)
    monkeypatch.setattr(painter, "_dynamic_brush_requested_for_current_draw", lambda: False)
    class FailedThread:
        def __init__(self, **_):
            pass
        def start(self):
            raise RuntimeError("thread unavailable")
    monkeypatch.setattr(core.threading, "Thread", FailedThread)
    with pytest.raises(RuntimeError, match="thread unavailable"):
        painter.draw_image()
    assert not painter.drawing_enabled
    assert painter.drawing_thread is None
    assert painter.drawing_start_time is None


def test_progress_is_captured_before_qt_queue_and_old_run_is_rejected(service, monkeypatch):
    engine = service.engine
    engine.mode = "color"
    engine.cluster_map = np.zeros((1, 10), dtype=int)
    engine.drawn_mask = np.zeros((1, 10), dtype=bool)
    engine.drawn_mask[0, :3] = True
    queued, stats = [], []
    service.drawingStatsChanged.connect(stats.append)
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.RUNNING))
    monkeypatch.setattr(service, "_invoke_on_qt", queued.append)
    producer = threading.Thread(target=service._on_progress_from_engine)
    producer.start()
    producer.join()
    assert len(queued) == 1
    engine.drawn_mask[:] = True
    queued.pop()()
    assert stats[-1]["percent"] == 30  # producer values, not mutable engine

    service._progress_capture_last = 0
    producer = threading.Thread(target=service._on_progress_from_engine)
    producer.start()
    producer.join()
    assert len(queued) == 1
    service._drawing_run_id = engine.drawing_run_id = 1
    before = len(stats)
    queued.pop()()
    assert len(stats) == before  # queued run 0 cannot update run 1


def test_completion_updates_hud_and_scale_and_restart_resets_eta(service, monkeypatch):
    from ui.widgets.hud_overlay import HudOverlay
    hud = HudOverlay()
    stats, percents = [], []
    service.drawingStatsChanged.connect(stats.append)
    service.drawingStatsChanged.connect(hud.update_stats)
    service.progressChanged.connect(percents.append)
    monkeypatch.setattr(service, "_start_in_worker", lambda *args, **kwargs: None)
    try:
        service.start_pause()
        sample = replace(service.engine.drawing_progress_snapshot(), done_pixels=100, total_pixels=100,
                         elapsed_seconds=12.5, colors_total=1, colors_done=0)
        service._on_drawing_event(DrawingEvent(1, DrawingPhase.RUNNING, progress=sample))
        assert stats[-1]["percent"] == 99
        service._on_drawing_event(DrawingEvent(1, DrawingPhase.PAUSED, progress=sample))
        assert stats[-1]["percent"] == 99  # pause cannot announce completion
        service._on_drawing_event(DrawingEvent(1, DrawingPhase.COMPLETED, progress=sample))
        assert percents[-1] == stats[-1]["percent"] == 100
        assert hud._last_stats == stats[-1]
        assert stats[-1]["colors_done"] == 1
        assert stats[-1]["colors_remaining"] == 0
        assert stats[-1]["elapsed_seconds"] == 12.5
        assert stats[-1]["phase"] == "completed"
        assert stats[-1]["eta_seconds"] == 0
        service._eta_samples = [(12.5, 100, 1)]
        service._eta_pred_history = [(1, 15)]
        service.start_pause()
        next_sample = replace(sample, run_id=2, done_pixels=0, elapsed_seconds=0)
        service._on_drawing_event(DrawingEvent(2, DrawingPhase.RUNNING, progress=next_sample))
        assert percents[-1] == stats[-1]["percent"] == 0
        assert not service._eta_samples and not service._eta_pred_history
        service._on_drawing_progress(sample)
        assert stats[-1]["run_id"] == 2 and stats[-1]["percent"] == 0
    finally:
        service.drawingStatsChanged.disconnect(hud.update_stats)
        hud.deleteLater()


def test_progress_older_than_pause_is_rejected_and_failure_keeps_partial_result(service):
    stats = []
    service.drawingStatsChanged.connect(stats.append)
    sample = replace(service.engine.drawing_progress_snapshot(), done_pixels=40, total_pixels=100,
                     elapsed_seconds=2, colors_total=3, colors_done=1)
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.RUNNING, progress=sample))
    newer = replace(sample, captured_at=sample.captured_at + 1, done_pixels=60)
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.PAUSED, progress=newer))
    before = len(stats)
    service._on_drawing_progress(sample)
    assert len(stats) == before
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.FAILED, "device", progress=newer))
    assert stats[-1]["percent"] == 60
    assert stats[-1]["phase"] == "failed"
    assert stats[-1]["eta_seconds"] is None
    assert stats[-1]["colors_done"] == 1


def test_engine_terminal_snapshot_preserves_elapsed_before_cleanup(engine, monkeypatch):
    painter, events, _ = engine
    painter.drawing_start_time = time.time() - 5
    monkeypatch.setattr(painter, "_run_post_draw_repair", lambda _: None)
    painter.draw_colors_thread()
    assert painter.drawing_start_time is None
    assert events[-1].progress.elapsed_seconds >= 5
    assert events[-1].progress.run_id == 42


def test_progress_capture_failure_still_cleans_up_and_reports_terminal_error(engine, monkeypatch):
    painter, events, releases = engine
    monkeypatch.setattr(painter, "_draw_colors", lambda: DrawingPhase.COMPLETED)
    def fail_snapshot(**kwargs):
        raise RuntimeError("invalid progress data")
    monkeypatch.setattr(painter, "drawing_progress_snapshot", fail_snapshot)
    painter.drawing_enabled = True
    painter.drawing_start_time = time.time()
    painter.draw_colors_thread()
    assert events[-1].phase == DrawingPhase.FAILED
    assert "invalid progress data" in events[-1].error
    assert events[-1].progress is None
    assert not painter.drawing_enabled and painter.drawing_start_time is None
    assert releases == ["left", "right"]


