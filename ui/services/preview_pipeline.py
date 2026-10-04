"""Mixin extracted from ui/services/painter_service.py.

Preview pipeline: scheduling, debounce, worker lifecycle, image commit.
"""
from __future__ import annotations

import logging
import time  # noqa: F401

from PySide6.QtCore import QObject, QRect, QThread, Signal, Slot, QTimer, Qt  # noqa: F401
from PySide6.QtGui import QImage, QPixmap  # noqa: F401
from PySide6.QtWidgets import QWidget  # noqa: F401
from PIL import Image, ImageQt  # noqa: F401
from pathlib import Path  # noqa: F401

from engine.olegpainter.viewport_state import (  # noqa: F401
    VIEWPORT_UNSET,
    ViewportState,
    normalize_area_rect,
    normalize_desktop_rect,
)
from ui.services._sentinel import _UNSET  # noqa: F401
from ui.services.draw_worker import _Worker, thread_is_running  # noqa: F401
from ui.services.image_utils import pil_to_qimage  # noqa: F401
from ui.i18n import tr  # noqa: F401

log = logging.getLogger("olegpainter.painter_service")


class PreviewPipelineMixin:
    """Preview pipeline: scheduling, debounce, worker lifecycle, image commit."""

    def _invalidate_heavy_preview(self, *, strict: bool = True) -> None:
        self._mark_render_dirty(self._INVALIDATE_RENDER)
        self._schedule_preview_rebuild(strict, delay_ms=self._PREVIEW_HEAVY_DEBOUNCE_MS)

    def _manual_palette_affects_preview(self) -> bool:
        engine = getattr(self, "engine", None)
        if engine is None:
            return False
        try:
            current_method = str(getattr(engine, "color_picking_method", "") or "").strip().lower()
        except Exception:
            current_method = ""
        if current_method == "manual_palette":
            return True
        try:
            return bool(getattr(engine, "manual_palette_mix_enabled", False))
        except Exception:
            return False

    def request_preview_refresh(self) -> None:
        self._invalidate_visuals(self._INVALIDATE_RENDER)

    def _commit_preview_from_qimage(self, qimg: QImage, revision: int) -> None:
        if getattr(self, "_is_shutting_down", False):
            return
        revision = int(revision or 0)
        if revision and revision < self._render_revision:
            return
        self._preview_display_revision = max(self._preview_display_revision, revision)
        self._last_qimg = qimg.copy() if not qimg.isNull() else None
        self.previewReady.emit(qimg)
        if qimg.isNull():
            self.schedule_stencil_sync("preview-null", revision, force_hide=True)
            return
        self.schedule_stencil_sync("preview-ready", revision, image=qimg)

    def _on_preview_from_engine(self, pil_img):
        qimg = pil_to_qimage(pil_img) if pil_img is not None else QImage()
        revision = int(self._preview_active_revision or self._render_revision or 0)

        def _apply():
            self._commit_preview_from_qimage(qimg, revision)

        self._invoke_on_qt(_apply)

    def _can_prepare_preview(self) -> bool:
        eng = self.engine
        if getattr(eng, "draw_region", None) is None:
            return False
        if self._last_qimg is None:
            for attr in ("source_pil_image", "IMAGE_PATH", "quantized_preview_image"):
                if getattr(eng, attr, None):
                    break
            else:
                return False
        return True

    def _schedule_preview_rebuild(self, strict: bool, *, delay_ms: int | None = None) -> None:
        if not QThread.isMainThread():
            self._invoke_on_qt(lambda: self._schedule_preview_rebuild(strict, delay_ms=delay_ms))
            return
        if getattr(self, "_is_shutting_down", False):
            return
        if not self._can_prepare_preview():
            return
        self._preview_requested_revision = max(self._preview_requested_revision, self._render_revision)
        if strict:
            self._preview_pending = "strict"
        elif self._preview_pending is None:
            self._preview_pending = "soft"
        self.previewStateChanged.emit()
        if delay_ms is not None:
            delay_value = max(0, int(delay_ms))
            current_delay = int(self._preview_pending_delay_ms or 0)
            self._preview_pending_delay_ms = max(current_delay, delay_value)
        active_delay = self._preview_pending_delay_ms
        if self._preview_timer is None:
            self._preview_timer = QTimer(self)
            self._preview_timer.setSingleShot(True)
            self._preview_timer.timeout.connect(self._run_preview_rebuild)
        delay = active_delay if active_delay is not None else (
            self._PREVIEW_DEBOUNCE_STRICT_MS if strict else self._PREVIEW_DEBOUNCE_MS
        )
        if strict:
            ignore_until = getattr(self.engine, "ignore_clicks_until", None)
            now = time.time()
            if ignore_until and ignore_until > now:
                delay = max(delay, int((ignore_until - now) * 1000) + 50)
        self._preview_timer.start(int(delay))

    def _run_preview_rebuild(self) -> None:
        if getattr(self, "_is_shutting_down", False):
            return
        pending = self._preview_pending
        if not pending:
            return
        if self._compute_capture_state()["active"]:
            # A preview mutates engine preparation and cancels engine captures.
            # Preserve this request; capture completion resumes it once.
            return
        if not self._can_prepare_preview():
            self._preview_pending = None
            self._preview_pending_delay_ms = None
            self.previewStateChanged.emit()
            return
        if self._is_drawing or self.brush_learning_active or self._thread_is_running(self._worker_thread):
            if self._preview_timer is not None:
                self._preview_timer.start(self._PREVIEW_RETRY_MS)
            return
        # A stopped native thread may still have its finished signal queued.
        # Keep ownership until that signal has retired this particular worker.
        if self._preview_thread is not None:
            if self._preview_timer is not None:
                self._preview_timer.start(self._PREVIEW_RETRY_MS)
            return
        if pending == "strict":
            ignore_until = getattr(self.engine, "ignore_clicks_until", None)
            if ignore_until and ignore_until > time.time():
                if self._preview_timer is not None:
                    self._preview_timer.start(self._PREVIEW_RETRY_MS)
                return
        self._preview_pending = None
        self._preview_pending_delay_ms = None
        self._start_preview_worker(strict=(pending == "strict"))

    def _start_preview_worker(self, strict: bool) -> None:
        if self._is_shutting_down:
            return
        if self._preview_thread is not None:
            if strict:
                self._preview_pending = "strict"
            elif self._preview_pending is None:
                self._preview_pending = "soft"
            if self._preview_timer is not None:
                self._preview_timer.start(self._PREVIEW_RETRY_MS)
            return

        def _task():
            if QThread.currentThread().isInterruptionRequested():
                return
            if strict:
                self._invoke_engine_preprocess()
            if QThread.currentThread().isInterruptionRequested():
                return
            if hasattr(self.engine, "prepare_image_and_palette"):
                self.engine.prepare_image_and_palette()

        self._preview_active_revision = max(self._preview_requested_revision, self._render_revision)
        self._preview_thread = QThread()
        self._preview_worker = _Worker(_task)
        self._preview_worker.moveToThread(self._preview_thread)
        self._preview_thread.started.connect(self._preview_worker.run)
        self._preview_worker.finished.connect(self._preview_thread.quit)
        self._preview_worker.finished.connect(self._preview_worker.deleteLater)
        self._preview_worker.error.connect(self._on_preview_worker_error)
        thread = self._preview_thread
        # Lifecycle cleanup must still reach Qt while shutdown rejects normal
        # engine/UI callbacks through _invoke_on_qt.
        thread.finished.connect(lambda: QTimer.singleShot(0, self,
            lambda: self._on_preview_worker_finished(thread)))
        self._preview_thread.start()
        self.previewStateChanged.emit()

    def _on_preview_worker_error(self, message: str) -> None:
        if self._is_shutting_down:
            return
        try:
            self.statusChanged.emit(f"error: preview worker failed: {message}")
        except Exception:
            log.debug("ignored exception in self.statusChanged.emit(f'error: preview worker failed: {message}')", exc_info=True)
        self.schedule_stencil_sync("preview-error", self._preview_active_revision or self._render_revision, force_hide=True)

    def _on_preview_worker_finished(self, thread: QThread) -> None:
        if thread is not self._preview_thread:
            thread.deleteLater()
            return
        self._preview_thread = None
        self._preview_worker = None
        self._preview_active_revision = 0
        # Delete only after the matching completion reaches the UI thread.
        # Carry identity explicitly: sender() can be None in a PySide callback.
        thread.deleteLater()
        if self._preview_pending:
            self._schedule_preview_rebuild(
                self._preview_pending == "strict",
                delay_ms=self._preview_pending_delay_ms,
            )
        self.previewStateChanged.emit()

    def _rebuild_preview_if_possible(self):
        self._schedule_preview_rebuild(strict=False)

    def _rebuild_preview_strict(self):
        self._schedule_preview_rebuild(strict=True)

    def _emit_preview_from_pil(self, pil_img: Image.Image):
        if getattr(self, "_is_shutting_down", False):
            return
        qimg = pil_to_qimage(pil_img)
        self._invoke_on_qt(
            lambda: self._commit_preview_from_qimage(
                qimg,
                max(self._preview_display_revision, self._render_revision, 1),
            )
        )
