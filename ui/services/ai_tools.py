"""Local AI tools on the service (AI-001).

AiCenter does the work in its own threads; this mixin starts jobs for the
current picture and applies results on the Qt thread through the same path
as a pasted image, so preview, stencil and session persistence stay one path.
The inserted picture is kept by ImageEditsMixin for «Вернуть исходник»;
the background goes through its methods (IMAGE-EDIT-001).
"""
from __future__ import annotations

import logging

import numpy as np
from PIL import Image
from PySide6.QtGui import QImage

from application.ai import AiCenter, AiUnavailable, EngineAiBackend

log = logging.getLogger("olegpainter.ai.service")

_EDIT_LABELS = {
    "background": "AI: фон убран",
    "keep_selection": "AI: только выделенный объект",
    "erase_selection": "AI: объект стёрт",
    "upscale": "AI: увеличено",
    "lineart": "AI: контуры",
    "flatten": "AI: упрощено",
}


class AiToolsMixin:
    def _init_ai_tools(self) -> None:
        from ui.helpers.config_store import ConfigStore
        # Same folder rule as profiles (honours OLEGPAINTER_CONFIG_DIR in tests/previews).
        self.ai = AiCenter(ConfigStore().base_dir / "ai.json",
                           notify=lambda: self._invoke_on_qt(self.aiChanged.emit))
        self.engine.ai_backend = EngineAiBackend(self.ai)
        self.ai_backend = self.engine.ai_backend
        self._ai_image_key = None

    # ----- current picture ------------------------------------------------------
    def _ai_source(self) -> Image.Image | None:
        image = getattr(self.engine, "source_pil_image", None)
        return image.copy() if isinstance(image, Image.Image) else None

    def _ai_key(self):
        return getattr(self, "_session_image_revision", 0)

    # ----- commands -------------------------------------------------------------
    def ai_run(self, kind: str, params: dict | None = None) -> None:
        if not self._guard_image_mutation("an AI edit"):
            raise AiUnavailable("Сейчас нельзя менять картинку: идёт рисунок или обучение кисти.")
        if kind == "background":                  # one background state for all methods
            self.set_background_method("ai")
            return
        image = self._ai_source()
        self.ai.start(kind, image, self._ai_key(), dict(params or {}), self._ai_result_ready)

    def ai_select(self, x: float, y: float, positive: bool = True) -> None:
        """Click on the preview (normalized 0..1 source coordinates)."""
        if not (0.0 <= x <= 1.0 and 0.0 <= y <= 1.0):
            return
        self.ai_run("select", {"x": x, "y": y, "positive": positive})

    def ai_restore_original(self) -> bool:
        return self.restore_original()

    def ai_has_original(self) -> bool:
        return self.has_picture_edits

    # ----- results (worker thread -> Qt thread) ----------------------------------
    def _ai_result_ready(self, kind, result, key) -> None:
        self._invoke_on_qt(lambda: self._ai_apply_result(kind, result, key))

    def _ai_apply_result(self, kind, result, key) -> None:
        if key != self._ai_key():
            self.statusChanged.emit("warn: картинка сменилась, результат AI отброшен.")
            return
        if kind == "select":
            self.aiChanged.emit()
            self.previewStateChanged.emit()
            return
        if kind == "depth":
            return
        if not isinstance(result, Image.Image):
            return
        if self._ai_apply_image(result, _EDIT_LABELS.get(kind, "AI")):
            self.ai.forget_image()
            self.statusChanged.emit(_EDIT_LABELS.get(kind, "AI") + ".")

    def _ai_apply_image(self, image: Image.Image, origin: str) -> bool:
        return self._apply_edited_picture(image, origin)

    # ----- selection preview ---------------------------------------------------------
    def ai_selection_preview(self) -> QImage | None:
        """The source with the selected object tinted, for the preview pane."""
        mask = self.ai.selection_mask
        image = self._ai_source()
        if mask is None or image is None or mask.shape != (image.height, image.width):
            return None
        rgba = np.array(image.convert("RGBA"), dtype=np.float32)
        tint = np.array([255, 210, 30], dtype=np.float32)
        rgba[..., :3] = np.where(mask[..., None], rgba[..., :3] * 0.45 + tint * 0.55, rgba[..., :3] * 0.55)
        rgba[..., 3] = 255
        data = np.ascontiguousarray(rgba.astype(np.uint8))
        qimage = QImage(data.data, image.width, image.height, image.width * 4, QImage.Format.Format_RGBA8888)
        return qimage.copy()
