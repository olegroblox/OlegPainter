"""Cooperative shutdown while Qt keeps processing completion signals and paint."""
from threading import Thread

from PySide6.QtCore import QEventLoop, QThread, QTimer


class ShutdownLifecycleMixin:
    def shutdown(self, *, wait=True):
        """Start shutdown once; interactive callers use wait=False.

        The waiting adapter is for fixture/process cleanup. It drives the same
        state machine, without blocking Qt worker completion on QThread.wait().
        """
        if not QThread.isMainThread():
            raise RuntimeError("Shutdown must be requested on the Qt main thread")
        loop = QEventLoop()
        if wait:
            self.shutdownFinished.connect(loop.quit)
        try:
            if not getattr(self, "_shutdown_started", False):
                self._begin_shutdown()
            elif self._shutdown_completed and self._shutdown_error:
                # The workers have exited. Retrying only retries device cleanup.
                self._shutdown_completed = False
                self._shutdown_error = ""
                self._shutdown_release_thread = None
                self._shutdown_poll_timer.start()
            if wait and not self._shutdown_completed:
                loop.exec(QEventLoop.ExcludeUserInputEvents)
            if wait and self._shutdown_error:
                raise RuntimeError(self._shutdown_error)
            return self._shutdown_completed and not self._shutdown_error
        finally:
            if wait:
                self.shutdownFinished.disconnect(loop.quit)

    def _begin_shutdown(self):
        self._shutdown_started = self._is_shutting_down = True
        self._shutdown_completed = False
        self._shutdown_error = ""
        self._shutdown_release_thread = None
        self.engine.is_shutting_down = True
        self.engine.disable_stencil_autorestart = True
        self.engine.request_drawing_stop()
        self.cancel_brush_learning()
        self.end_hotkey_capture(self._hotkey_capture_token)
        self._unregister_global_hotkeys()
        for name in ("_stop_poll_timer", "_skipped_overlay_timer", "_capture_watch_timer",
                     "_stencil_sync_timer", "_preview_timer"):
            timer = getattr(self, name, None)
            if timer is not None:
                timer.stop()
        self._preview_pending = self._preview_pending_delay_ms = None
        self._pending_stencil_qimage = self._pending_stencil_area = None
        self._deferred_stencil_qimage = self._deferred_stencil_area = None
        self._stencil_refresh_deferred = False
        self.cancel_ai_task()
        for name in ("_worker_thread", "_preview_thread", "_ai_task_thread", "_stop_worker_thread"):
            thread = getattr(self, name, None)
            if self._thread_is_running(thread):
                thread.requestInterruption()
                thread.quit()
        for name in ("show_stencil_callback", "hide_stencil_callback", "stencil_image_changed_callback"):
            setattr(self.engine, name, None)
        if self._overlay is not None:
            self._hide_overlay_on_qt(teardown=True, cancel_edit=True)
        self._overlay = None
        self._set_overlay_visible_state(False)
        self._shutdown_poll_timer = QTimer(self)
        self._shutdown_poll_timer.setInterval(15)
        self._shutdown_poll_timer.timeout.connect(self._poll_shutdown)
        self._shutdown_poll_timer.start()
        self._poll_shutdown()

    def _poll_shutdown(self):
        if self._shutdown_completed:
            return
        if self.brush_learning_active:
            self._poll_brush_learning()
            if self.brush_learning_active:
                return
        # Keep all owning references until native work has actually stopped.
        for name in ("_worker_thread", "_preview_thread", "_ai_task_thread", "_stop_worker_thread"):
            if self._thread_is_running(getattr(self, name, None)):
                return
        drawing_thread = self.engine.drawing_thread
        if drawing_thread is not None and drawing_thread.is_alive():
            return
        if self._shutdown_release_thread is None:
            capture = getattr(self.engine, "_capture_session", None)
            if (self._runtime_initialized or self._compute_capture_state()["active"]
                    or (capture is not None and not capture.finished.is_set())):
                # This is the final release, after every input-producing worker
                # exited. Device/capture libraries may block; never run them on Qt.
                self._shutdown_release_thread = Thread(
                    target=self._release_shutdown_input, name="OlegPainter shutdown input")
                try:
                    self._shutdown_release_thread.start()
                except Exception as error:
                    self._shutdown_error = str(error)
                else:
                    return
        elif self._shutdown_release_thread.is_alive():
            return
        self._shutdown_poll_timer.stop()
        self.engine.drawing_thread = None
        self._worker_thread = self._worker = None
        self._preview_thread = self._preview_worker = None
        self._stop_worker_thread = self._stop_worker = None
        self._preview_active_revision = 0
        self._stop_pending = False
        self._cleanup_ai_task(force=True)
        self._shutdown_completed = True
        self.shutdownFinished.emit(self._shutdown_error)

    def _release_shutdown_input(self):
        try:
            if self._runtime_initialized:
                self.engine.stop_script()
            else:
                self.engine._cancel_active_captures()
                self.engine._end_capture_session(wait=True)
        except Exception as error:
            self._shutdown_error = str(error)
