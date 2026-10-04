"""Mixin extracted from ui/services/painter_service.py.

Drawing lifecycle: start/pause/stop, status/progress callbacks from engine.
"""
from __future__ import annotations

import logging
import time  # noqa: F401

from PySide6.QtCore import QObject, QThread, Signal, Slot, QTimer  # noqa: F401

from ui.services._sentinel import _UNSET  # noqa: F401
from ui.services.draw_worker import _Worker, thread_is_running  # noqa: F401
from ui.i18n import tr  # noqa: F401
from engine.olegpainter.drawing_events import DrawingEvent, DrawingPhase, DrawingProgress

log = logging.getLogger("olegpainter.painter_service")


class DrawingLifecycleMixin:
    """Drawing lifecycle: start/pause/stop, status/progress callbacks from engine."""

    def start_pause(self):
        try:
            if getattr(self, "_is_shutting_down", False) or self._close_pending:
                return
            if getattr(self, "_hotkey_capture_active", False):
                self.statusChanged.emit("warn: Сначала завершите назначение горячей клавиши.")
                return
            if self._stop_pending:
                return
            if self._thread_is_running(self._worker_thread):
                self.statusChanged.emit("warn: Предыдущая команда рисования ещё выполняется — подождите секунду.")
                return
            action = "start"
            if self._is_drawing:
                action = "pause" if not self._is_paused else "resume"

            if action == "start":
                thread = self.engine.drawing_thread
                if thread is not None and thread.is_alive():
                    self.statusChanged.emit("warn: Прошлый рисунок ещё останавливается — подождите секунду.")
                    return
                ok, msg = self._preflight_check()
                if not ok:
                    self.statusChanged.emit("error: " + msg)
                    self._drawing_terminal = False
                    self._on_drawing_event(DrawingEvent(self._drawing_run_id, DrawingPhase.STOPPED))
                    return
                if self._dynamic_brush_should_prompt_learning():
                    self.request_dynamic_brush_learning(auto_start=True, reason="start")
                    self._drawing_terminal = False
                    self._on_drawing_event(DrawingEvent(self._drawing_run_id, DrawingPhase.STOPPED))
                    return

            if action == "start":
                self._drawing_run_id += 1
                self.engine.begin_drawing_run(self._drawing_run_id)
                self._drawing_terminal = False
                self._percent_floor = 0
                self._eta_samples = []
                self._eta_pred_history = []
                self._progress_capture_last = 0.0
                self._last_drawing_progress = None
                self._progress_applied_at = 0.0
            self._stop_requested = False
            run_id = self._drawing_run_id

            def _handle_draw_result(result):
                # Success/pause are engine events, never inferred from a late
                # worker result. False on initial start means preflight rejection.
                if action == "start" and not result:
                    self._on_drawing_event(DrawingEvent(run_id, DrawingPhase.STOPPED))

            self._start_in_worker(
                self.engine.draw_image,
                on_result=_handle_draw_result,
                on_error=lambda error: self._on_drawing_event(
                    DrawingEvent(run_id, DrawingPhase.FAILED, error)),
            )
        except Exception as e:
            self._on_drawing_event(DrawingEvent(self._drawing_run_id, DrawingPhase.FAILED, str(e)))

    def _on_drawing_event(self, event: DrawingEvent):
        if not QThread.isMainThread():
            self._invoke_on_qt(lambda: self._on_drawing_event(event))
            return
        if self._stop_pending and event.phase != DrawingPhase.STOPPING:
            if event.run_id == self._drawing_run_id and event.error:
                self._stop_error = event.error
            return
        if (getattr(self, "_is_shutting_down", False)
                or event.run_id != self._drawing_run_id or self._drawing_terminal):
            return
        if (getattr(self, "_stop_requested", False)
                and event.phase not in (DrawingPhase.STOPPING, DrawingPhase.STOPPED, DrawingPhase.FAILED)):
            return
        if event.phase == DrawingPhase.STOPPING:
            self._is_drawing, self._is_paused = True, False
        elif event.phase == DrawingPhase.RUNNING:
            if not self._is_drawing:
                self._started_at = time.time()
                self._progress_snapshot = {"percent": None, "eta_text": None}
                self._progress_last_emit = 0.0
            self._is_drawing, self._is_paused = True, False
        elif event.phase == DrawingPhase.PAUSED:
            self._is_drawing, self._is_paused = True, True
        elif event.phase.terminal:
            self._drawing_terminal = True
            self._is_drawing, self._is_paused = False, False
            self._started_at = None
        self.drawing_phase = event.phase
        progress = event.progress or self._last_drawing_progress or DrawingProgress(
            event.run_id, time.monotonic(), 0, 0, 0.0, 0, 0, None, 0, 0, True)
        self._last_drawing_progress = progress
        self._progress_applied_at = max(self._progress_applied_at, progress.captured_at)
        self._update_progress_metrics(force=True, sample=progress, phase=event.phase)
        self.drawingEvent.emit(event)
        self.drawingStateChanged.emit(event.phase.value)
        if event.error:
            self.statusChanged.emit("error: " + event.error)

    def stop(self):
        if self.brush_learning_active:
            self.cancel_brush_learning()
            return
        if self._stop_pending or getattr(self, "_is_shutting_down", False):
            return
        self._stop_requested = True
        self._stop_pending = True
        self._stop_error = ""
        self._drawing_terminal = False
        self.engine.request_drawing_stop()
        self._on_drawing_event(DrawingEvent(self._drawing_run_id, DrawingPhase.STOPPING))
        # Cancellation is already latched. Device release/capture teardown can
        # block in system libraries, so those operations get their own worker.
        thread = QThread()
        worker = _Worker(self.engine.stop_script)
        self._stop_worker_thread, self._stop_worker = thread, worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(thread.quit)
        worker.finished.connect(worker.deleteLater)
        worker.error.connect(self._on_stop_error)
        thread.finished.connect(thread.deleteLater)
        thread.finished.connect(self._on_stop_worker_finished)
        try:
            thread.start()
        except Exception as exc:
            self._stop_error = str(exc)
            self._stop_worker_thread = self._stop_worker = None
            worker.deleteLater()
            thread.deleteLater()
        self._stop_poll_timer.start()

    def _on_stop_error(self, error):
        self._stop_error = str(error)

    def _on_stop_worker_finished(self):
        if self.sender() is not self._stop_worker_thread:
            return
        self._stop_worker_thread = self._stop_worker = None
        self._poll_drawing_stop()

    def _poll_drawing_stop(self):
        if not self._stop_pending or getattr(self, "_is_shutting_down", False):
            return
        if self._worker_thread is not None or self._stop_worker_thread is not None:
            return
        thread = self.engine.drawing_thread
        if thread is not None and thread.is_alive():
            return
        self._stop_poll_timer.stop()
        self.engine.finish_drawing_stop()
        self._stop_pending = False
        phase = DrawingPhase.FAILED if self._stop_error else DrawingPhase.STOPPED
        self._on_drawing_event(DrawingEvent(self._drawing_run_id, phase, self._stop_error))

    def _on_status_from_engine(self, text: str):
        # Engine callbacks fire on the engine's daemon thread; marshal onto the
        # GUI thread so all state mutation and signal emits happen there.
        if not QThread.isMainThread():
            self._invoke_on_qt(lambda: self._on_status_from_engine(text))
            return
        s = str(text or "")
        # Фильтруем шум из RealESRGAN/Vulkan и проценты (прогресс и так есть)
        low = s.lower()
        noisy_keys = ("queuec=", "queueg=", "queuet=", "bugsbn", "bugbliz", "bugcopc", "bugihfa",
                    "fp16-p/s", "int8-p/s", "subgroup=", "basic=1", "vote=1", "ballot=1", "shuffle=1")
        if any(k in low for k in noisy_keys):
            return
        stripped = s.strip()
        def _is_percent_line(x: str) -> bool:
            t = x.strip()
            if not t.endswith("%"): return False
            t = t[:-1].replace(",", ".")
            try:
                float(t); return True
            except Exception:
                return False
        if _is_percent_line(stripped):
            return
        if "SetProcessDpiAwarenessContext() failed" in s or "Qt's default DPI awareness context" in s:
            return
        self.statusChanged.emit(s)

    def _on_progress_from_engine(self, *args, **kwargs):
        # Capture at the producer. A queued callback must never read a newer run.
        now = time.monotonic()
        if not args and now - self._progress_capture_last < self._progress_emit_min_interval:
            return
        self._progress_capture_last = now
        sample = self.engine.drawing_progress_snapshot()
        hints = self._parse_progress_args(args)
        self._on_drawing_progress(sample, hints)

    def _on_drawing_progress(self, sample, hints=(None, None, None)):
        if not QThread.isMainThread():
            self._invoke_on_qt(lambda: self._on_drawing_progress(sample, hints))
            return
        if (self._is_shutting_down or self._stop_requested or self._drawing_terminal
                or sample.run_id != self._drawing_run_id
                or sample.captured_at < self._progress_applied_at
                or not self._is_drawing):
            return
        self._last_drawing_progress = sample
        self._progress_applied_at = sample.captured_at
        percent_hint, done_hint, total_hint = hints
        self._update_progress_metrics(
            force=True, sample=sample, phase=self.drawing_phase,
            percent_override=percent_hint,
            done_override=done_hint,
            total_override=total_hint,
        )

    def start(self):
        """Force a start if not already drawing; otherwise no-op."""
        try:
            if not getattr(self, "_is_drawing", False):
                return self.start_pause()
            if getattr(self, "_is_paused", False):
                # if paused, resume by toggling
                return self.start_pause()
        except Exception:
            log.debug("ignored exception in if not getattr(self, '_is_drawing', False): return self.start_pause()", exc_info=True)

    def pause(self):
        """Force a pause if currently drawing and not paused."""
        try:
            if getattr(self, "_is_drawing", False) and not getattr(self, "_is_paused", False):
                return self.start_pause()
        except Exception:
            log.debug("ignored exception in if getattr(self, '_is_drawing', False) and (not getattr(self, '_is_paused', F...", exc_info=True)
