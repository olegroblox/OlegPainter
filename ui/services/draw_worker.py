"""Worker objects and QThread helpers extracted from painter_service.py.

`_Worker` runs a callable in a Qt thread and emits its result back; used by
the preview pipeline. `_AiWorker` adds cancellation/progress signalling for AI
inference. `thread_is_running` is the shared "is this QThread alive" check.
"""
from __future__ import annotations

import traceback

from PySide6.QtCore import QObject, QThread, Signal, Slot


class _Worker(QObject):
    finished = Signal()
    resultReady = Signal(object)
    error = Signal(str)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self._had_error = False
        self._result = None

    @Slot()
    def run(self):
        try:
            self._result = self._fn()
        except Exception as e:
            self._had_error = True
            tb = traceback.format_exc()
            self.error.emit(f"{e}\n{tb}")
        finally:
            self.finished.emit()
            if not self._had_error:
                self.resultReady.emit(self._result)


class _AiWorker(QObject):
    finished = Signal(object)
    error = Signal(str)
    cancelled = Signal()
    progress = Signal(int)

    def __init__(self, fn):
        super().__init__()
        self._fn = fn
        self._cancel_requested = False

    def cancel(self) -> None:
        self._cancel_requested = True

    @Slot()
    def run(self):
        if self._cancel_requested:
            self.cancelled.emit()
            return
        try:
            self.progress.emit(-1)
            result = self._fn()
        except Exception as e:
            tb = traceback.format_exc()
            self.error.emit(f"{e}\n{tb}")
            return
        if self._cancel_requested:
            self.cancelled.emit()
            return
        self.progress.emit(100)
        self.finished.emit(result)


def thread_is_running(thread: QThread | None) -> bool:
    """True if the given QThread is non-None and currently executing."""
    if thread is None:
        return False
    try:
        return bool(thread.isRunning())
    except Exception:
        return False
