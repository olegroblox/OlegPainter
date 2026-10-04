"""Mixin extracted from ui/services/painter_service.py.

AI runtime / registry / model download / upscale / outpaint / bg-removal.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal, Slot, QTimer  # noqa: F401
from PySide6.QtGui import QImage  # noqa: F401
from PIL import Image, ImageQt  # noqa: F401
from pathlib import Path  # noqa: F401
from copy import deepcopy  # noqa: F401
import time, os, traceback, json, math, inspect  # noqa: F401
import logging
from typing import Callable

from engine.ai import (  # noqa: F401
    AiFeatureDisabled,
    AiModelDescriptor,
    AiModelRegistry,
    DisabledAiBackend,
    ModelTask,
    ai_deferred_message,
    describe_execution_provider,
)
from ui.i18n import tr  # noqa: F401

log = logging.getLogger("olegpainter.painter_service")


class AiServiceMixin:
    """AI runtime / registry / model download / upscale / outpaint / bg-removal."""

    def is_ai_enabled(self) -> bool:
        return bool(getattr(self.ai_backend, "is_enabled", lambda: False)())

    def get_ai_status(self) -> dict:
        status = getattr(self.ai_backend, "status", {})
        return dict(status) if isinstance(status, dict) else {"enabled": False, "reason": "deferred"}

    def _emit_ai_disabled(self, task_id: str | None = None) -> str:
        message = ai_deferred_message()
        if task_id:
            try:
                self.aiTaskFailed.emit(task_id, message)
            except Exception:
                log.debug('ignored exception in self.aiTaskFailed.emit(task_id, message)', exc_info=True)
        try:
            self.statusChanged.emit(f"info: {message}")
        except Exception:
            log.debug("ignored exception in self.statusChanged.emit(f'info: {message}')", exc_info=True)
        return message

    def _ai_task_label(self, task_id: str) -> str:
        labels = {
            ModelTask.BACKGROUND.value: tr("draw_ai_task_background"),
            ModelTask.UPSCALE.value: tr("draw_ai_task_upscale"),
            ModelTask.OUTPAINT.value: tr("draw_ai_task_outpaint"),
            "upscale_current": tr("draw_ai_task_upscale_current"),
        }
        return labels.get(task_id, task_id)

    def _cleanup_ai_task(self, *, force: bool = False) -> None:
        """
        Clear AI task references safely.
        Keep thread/worker objects alive while the thread is still running to
        avoid "QThread: Destroyed while thread is still running".
        """
        if not force:
            thread = self._ai_task_thread
            try:
                if thread is not None and self._thread_is_running(thread):
                    self._ai_task_id = None
                    self._ai_task_on_result = None
                    return
            except Exception:
                log.debug('ignored exception in if thread is not None and self._thread_is_running(thread): self._ai_task_id =...', exc_info=True)
        self._ai_task_thread = None
        self._ai_task_worker = None
        self._ai_task_id = None
        self._ai_task_on_result = None

    def _on_ai_task_thread_finished(self) -> None:
        self._cleanup_ai_task(force=True)

    def _start_ai_task(
        self,
        task_id: str,
        fn,
        *,
        on_result: Callable[[object], None] | None = None,
    ) -> bool:
        del fn, on_result
        self._emit_ai_disabled(task_id)
        return False

    def _on_ai_task_finished(self, task_id: str, result: object) -> None:
        if self._ai_task_id != task_id:
            return
        label = self._ai_task_label(task_id)
        try:
            self.aiTaskProgress.emit(task_id, 100)
        except Exception:
            log.debug('ignored exception in self.aiTaskProgress.emit(task_id, 100)', exc_info=True)
        on_result = self._ai_task_on_result
        on_result_error = None
        if callable(on_result):
            try:
                on_result(result)
            except Exception as exc:
                on_result_error = exc
                try:
                    self.logger.error("AI task on_result failed (%s): %s", task_id, exc)
                except Exception:
                    log.debug("ignored exception in self.logger.error('AI task on_result failed (%s): %s', task_id, exc)", exc_info=True)
        try:
            if on_result_error is not None:
                self.aiTaskFailed.emit(task_id, str(on_result_error))
                self.statusChanged.emit(
                    tr("draw_ai_task_status_failed").format(task=label, error=str(on_result_error))
                )
            else:
                self.aiTaskFinished.emit(task_id)
            if task_id != ModelTask.OUTPAINT.value and on_result_error is None:
                self.statusChanged.emit(tr("draw_ai_task_status_completed").format(task=label))
        except Exception:
            log.debug('ignored exception in if on_result_error is not None: self.aiTaskFailed.emit(task_id, str(on_result...', exc_info=True)
        self._cleanup_ai_task()

    def _on_ai_task_failed(self, task_id: str, message: str) -> None:
        if self._ai_task_id != task_id:
            return
        label = self._ai_task_label(task_id)
        try:
            self.aiTaskFailed.emit(task_id, message)
            self.statusChanged.emit(tr("draw_ai_task_status_failed").format(task=label, error=message))
        except Exception:
            log.debug('ignored exception in self.aiTaskFailed.emit(task_id, message)', exc_info=True)
        self._cleanup_ai_task()

    def _on_ai_task_cancelled(self, task_id: str) -> None:
        if self._ai_task_id != task_id:
            return
        label = self._ai_task_label(task_id)
        try:
            self.aiTaskCancelled.emit(task_id)
            self.statusChanged.emit(tr("draw_ai_task_status_cancelled").format(task=label))
        except Exception:
            log.debug('ignored exception in self.aiTaskCancelled.emit(task_id)', exc_info=True)
        self._cleanup_ai_task()

    def cancel_ai_task(self, task_id: str | None = None) -> None:
        if self._ai_task_worker is None:
            return
        if task_id is not None and self._ai_task_id != task_id:
            return
        try:
            self._ai_task_worker.cancel()
        except Exception:
            log.debug('ignored exception in self._ai_task_worker.cancel()', exc_info=True)

    def ai_registry_available(self) -> bool:
        return False

    def list_ai_models(self, task: str | ModelTask | None = None):
        return tuple()

    def get_ai_model(self, model_id: str) -> AiModelDescriptor | None:
        return None

    def ai_gpu_available(self) -> bool:
        return False

    def get_ai_selection(self) -> dict:
        engine = self.engine
        provider_key, provider_label = ("disabled", describe_execution_provider("disabled"))
        if hasattr(engine, 'get_ai_provider_info'):
            provider_key, provider_label = engine.get_ai_provider_info()
        return {
            'use_gpu': False,
            'provider': {
                'key': provider_key,
                'label': provider_label,
                'enabled': False,
                'reason': 'deferred',
            },
            'bg': {
                'model': getattr(engine, 'bg_ai_model_id', ''),
                'alias': getattr(engine, 'bg_ai_model_alias', '')
            },
            'upscale': {
                'model': getattr(engine, 'ai_upscale_model_id', ''),
                'alias': getattr(engine, 'ai_upscale_model_alias', ''),
                'scale': getattr(engine, 'ai_upscale_scale', None),
                'tile': getattr(engine, 'ai_upscale_tile', None),
                'denoise': getattr(engine, 'ai_upscale_denoise', None),
                'face_enhance': getattr(engine, 'ai_upscale_face_enhance', False)
            },
            'outpaint': {
                'model': getattr(engine, 'ai_outpaint_model_id', ''),
                'alias': getattr(engine, 'ai_outpaint_model_alias', ''),
                'mode': getattr(engine, 'ai_outpaint_mode', ''),
                'sd_license': getattr(engine, 'ai_outpaint_sd_license', False)
            }
        }

    def set_ai_use_gpu(self, enabled: bool):
        self.engine.ai_use_gpu = False
        if enabled:
            self._emit_ai_disabled()

    def set_ai_background_model(self, model_id: str):
        try:
            if hasattr(self.engine, 'set_ai_background_model'):
                self.engine.set_ai_background_model(model_id)
            else:
                setattr(self.engine, 'bg_ai_model_id', model_id)
        except Exception as exc:
            self.statusChanged.emit(f'error: set_bg_model failed: {exc}')

    def set_ai_background_parameters(self, *, threshold: float | None = None, softness: float | None = None) -> None:
        try:
            if threshold is not None:
                setattr(self.engine, 'bg_ai_threshold', float(threshold))
            if softness is not None:
                setattr(self.engine, 'bg_ai_softness', int(round(float(softness))))
        except Exception as exc:
            self.statusChanged.emit(f'error: set_bg_params failed: {exc}')

    def set_ai_upscale_model(self, model_id: str):
        try:
            if hasattr(self.engine, 'set_ai_upscale_model'):
                self.engine.set_ai_upscale_model(model_id)
            else:
                setattr(self.engine, 'ai_upscale_model_id', model_id)
        except Exception as exc:
            self.statusChanged.emit(f'error: set_upscale_model failed: {exc}')

    def set_ai_outpaint_model(self, model_id: str, mode: str | None = None):
        try:
            if hasattr(self.engine, 'set_ai_outpaint_model'):
                self.engine.set_ai_outpaint_model(model_id, mode)
            else:
                setattr(self.engine, 'ai_outpaint_model_id', model_id)
                if mode is not None:
                    setattr(self.engine, 'ai_outpaint_mode', mode)
        except Exception as exc:
            self.statusChanged.emit(f'error: set_outpaint_model failed: {exc}')

    def start_ai_download(self, model_id: str):
        message = self._emit_ai_disabled()
        self.aiDownloadFailed.emit(model_id, message)
        raise AiFeatureDisabled(message)

    def cancel_ai_download(self, model_id: str) -> None:
        worker = self._ai_downloads.get(model_id)
        if worker is not None:
            worker.cancel()

    def _on_ai_download_error(self, model_id: str, message: str) -> None:
        self.logger.error('AI model %s download failed: %s', model_id, message)
        self._ai_downloads.pop(model_id, None)
        self.aiDownloadFailed.emit(model_id, message)
        try:
            self.statusChanged.emit(f'error: ai download {model_id}: {message}')
        except Exception:
            log.debug("ignored exception in self.statusChanged.emit(f'error: ai download {model_id}: {message}')", exc_info=True)

    def _on_ai_download_completed(self, model_id: str, descriptor: AiModelDescriptor) -> None:
        self.logger.info('AI model %s ready', model_id)
        self._ai_downloads.pop(model_id, None)
        try:
            if hasattr(self.engine, '_refresh_ai_metadata'):
                self.engine._refresh_ai_metadata()  # type: ignore[attr-defined]
        except Exception:
            log.debug("ignored exception in if hasattr(self.engine, '_refresh_ai_metadata'): self.engine._refresh_ai_meta...", exc_info=True)
        self.aiDownloadFinished.emit(model_id)
        try:
            self.statusChanged.emit(f'info: ai model {model_id} installed')
        except Exception:
            log.debug("ignored exception in self.statusChanged.emit(f'info: ai model {model_id} installed')", exc_info=True)

    def _on_ai_download_cancelled(self, model_id: str) -> None:
        self.logger.info('AI model %s download cancelled', model_id)
        self._ai_downloads.pop(model_id, None)
        self.aiDownloadCancelled.emit(model_id)
        try:
            self.statusChanged.emit(f'info: ai download {model_id} cancelled')
        except Exception:
            log.debug("ignored exception in self.statusChanged.emit(f'info: ai download {model_id} cancelled')", exc_info=True)

    def compute_background_mask_ai(self):
        """Run AI background removal in a worker and rebuild preview after."""
        def task():
            pil = self._get_current_pil()
            try:
                return self.engine.compute_background_mask_ai(pil)   # with argument
            except TypeError:
                return self.engine.compute_background_mask_ai()      # no argument

        def apply_result(_result: object) -> None:
            self._rebuild_preview_if_possible()
            self._refresh_stencil_if_visible()

        self._start_ai_task(ModelTask.BACKGROUND.value, task, on_result=apply_result)

    def upscale_image_ai(self):
        """Run AI upscale and set the result as the new source image."""
        if not self._guard_image_mutation("running AI upscale"):
            return

        def task():
            pil = self._get_current_pil()
            try:
                return self.engine.upscale_image_ai(pil)   # with argument
            except TypeError:
                return self.engine.upscale_image_ai()      # no argument

        def apply_result(result: object) -> None:
            if isinstance(result, Image.Image):
                self._engine_set_image(result, origin="ai_upscale")
                self._emit_preview_from_pil(result)
            self._rebuild_preview_if_possible()
            self._refresh_stencil_if_visible()

        self._start_ai_task(ModelTask.UPSCALE.value, task, on_result=apply_result)

    def outpaint_image_ai(self, options: dict | None = None):
        """AI outpainting (edge completion)."""
        if not self._guard_image_mutation("running AI outpaint"):
            return
        settings = options or {}

        def task():
            pil = self._get_current_pil()
            try:
                return self.engine.outpaint_image_ai(pil, **settings)
            except TypeError:
                return self.engine.outpaint_image_ai(**settings)

        def apply_result(result: object) -> None:
            if isinstance(result, Image.Image):
                self._engine_set_image(result, origin="ai_outpaint")
                self._emit_preview_from_pil(result)
                self._rebuild_preview_if_possible()
                self._refresh_stencil_if_visible()
                try:
                    self.statusChanged.emit(
                        tr("draw_outpaint_status_completed").format(width=result.width, height=result.height)
                    )
                except Exception:
                    log.debug("ignored exception in self.statusChanged.emit(tr('draw_outpaint_status_completed').format(width=res...", exc_info=True)
            else:
                self._rebuild_preview_if_possible()

        self._start_ai_task(ModelTask.OUTPAINT.value, task, on_result=apply_result)

    def upscale_current_image_ai(self):
        """Run AI upscale for the current image and keep it as the source."""
        if not self._guard_image_mutation("running AI upscale on current image"):
            return

        def task():
            pil = self._get_current_pil()
            try:
                return self.engine.upscale_current_image_ai(pil)
            except TypeError:
                return self.engine.upscale_current_image_ai()

        def apply_result(result: object) -> None:
            if isinstance(result, Image.Image):
                self._engine_set_image(result, origin="ai_upscale_current")
                self._emit_preview_from_pil(result)
            self._rebuild_preview_if_possible()
            self._refresh_stencil_if_visible()

        self._start_ai_task("upscale_current", task, on_result=apply_result)

    def set_ai_model_by_name(self, name: str):
        """Switch RealESRGAN model by its display name, if known."""
        del name
        self._emit_ai_disabled()
