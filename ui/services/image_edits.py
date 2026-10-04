"""Picture processing in one place (IMAGE-EDIT-001/002): the inserted original, how
its background goes away, edits on top and a history to step back through them.

Before, three mechanisms removed a background without knowing of each other: the
colour key in «Удаление фона», the cutout of a snapshot and the AI model — the
switch showed «off» while the background was already gone. Now one method is
current: «none», «auto» (cutout without AI, application/cutout.py), «ai» (the
chosen background model) or «color» (the engine's colour key, nothing is
replaced). A new method always starts from the picture with its background.
Every change of the picture is one step of «Отменить / Повторить», and
«Вернуть исходник» brings back the picture as it was inserted.
"""
from __future__ import annotations

import logging
import threading

from PIL import Image

from application import image_filters

log = logging.getLogger(__name__)

BACKGROUND_METHODS = ("none", "auto", "ai", "color")
AUTO_BACKGROUND = ("off", "auto", "ai")
HISTORY_STEPS = 20
HISTORY_BYTES = 400 * 2**20          # older steps are dropped beyond this


def _bytes(image) -> int:
    return image.width * image.height * 4 if isinstance(image, Image.Image) else 0


class ImageEditsMixin:
    _picture_original: Image.Image | None = None
    _picture_edited = False
    _background_method = "none"          # the replacing method in effect: none / auto / ai
    _background_base: Image.Image | None = None   # the picture with its background
    _background_base_edited = False      # whether that picture already was an edit
    _background_job = None               # (kind, token) of the job removing the background
    _edit_job = None                     # token of a filter running in a worker
    auto_background = "off"              # what happens to new pictures: off / auto / ai
    auto_colors = False                  # new pictures get their colour count (COLORS-AUTO-001)
    _colors_pending = False              # a new picture waits for its colour count
    _insert_note = ""                    # what the last new picture got, for the caller's message

    def _init_image_edits(self) -> None:
        self._history: list[dict] = []
        self._future: list[dict] = []
        # An AI job that failed or was cancelled never calls back: notice it ending.
        self.aiChanged.connect(self._check_background_job)

    # ----- state -------------------------------------------------------------------
    @property
    def background_method(self) -> str:
        if bool(getattr(self.engine, "background_removal_enabled", False)):
            return "color"
        return self._background_method

    @property
    def background_busy(self) -> bool:
        return self._background_job is not None

    @property
    def edit_busy(self) -> bool:
        return self._edit_job is not None or self._background_job is not None

    @property
    def has_picture_edits(self) -> bool:
        return self._picture_original is not None and self._picture_edited

    def edit_history(self) -> dict:
        return dict(can_undo=bool(self._history) and not self.edit_busy,
                    can_redo=bool(self._future) and not self.edit_busy,
                    undo_label=self._history[-1]["label"] if self._history else "",
                    redo_label=self._future[-1]["label"] if self._future else "")

    def picture_original_qimage(self):
        """The inserted picture for the viewer's «Оригинал» while an edit is shown (else None)."""
        if not self.has_picture_edits:
            return None
        cached = getattr(self, "_picture_original_qimg", None)
        if cached is None or cached[0] is not self._picture_original:
            from ui.services.image_utils import pil_to_qimage
            cached = (self._picture_original, pil_to_qimage(self._picture_original))
            self._picture_original_qimg = cached
        return cached[1]

    def set_auto_background(self, value: str) -> None:
        if value not in AUTO_BACKGROUND:
            raise ValueError("Неизвестный способ для новых картинок.")
        self.auto_background = value
        self.aiChanged.emit()

    def set_auto_colors(self, enabled: bool) -> None:
        self.auto_colors = bool(enabled)
        self.aiChanged.emit()

    def apply_recommended_colors(self) -> tuple[str, int]:
        """Colour or black-and-white mode and the number of clearly different colours
        of the picture (engine.recommend_color_count). Returns (mode, count)."""
        result = self.recommend_color_count()
        count = int(result.get("count", 0) or 0) if isinstance(result, dict) else 0
        if count < 1:
            raise RuntimeError("Не удалось посчитать цвета: сначала откройте картинку.")
        if result.get("mode") == "bw":
            self.set_mode("bw")
            return "bw", count
        self.set_mode("color")
        if int(getattr(self.engine, "k_clusters", 0) or 0) != count:
            self.set_k_clusters(count)
        return "color", count

    def _insert_colors_note(self) -> str:
        """The colour count of a new picture, once its automatic background is done."""
        if not self._colors_pending:
            return ""
        self._colors_pending = False
        try:
            mode, count = self.apply_recommended_colors()
        except Exception as error:                # the user's count stays
            log.info("automatic colour count not applied: %s", error)
            return ""
        return ("Картинка чёрно-белая: включён чёрно-белый режим." if mode == "bw"
                else f"Цветов подобрано автоматически: {count}.")

    # ----- history ----------------------------------------------------------------------
    def _snapshot(self, label: str) -> dict:
        source = getattr(self.engine, "source_pil_image", None)
        return dict(label=label, ref=source, image=source.copy() if isinstance(source, Image.Image) else None,
                    method=self._background_method, base=self._background_base,
                    base_edited=self._background_base_edited, edited=self._picture_edited,
                    color_key=bool(getattr(self.engine, "background_removal_enabled", False)))

    def _push_history(self, step: dict) -> None:
        self._history.append(step)
        self._future.clear()
        total = 0
        for index in range(len(self._history) - 1, -1, -1):
            total += _bytes(self._history[index]["image"]) + _bytes(self._history[index]["base"])
            if total > HISTORY_BYTES or len(self._history) - index > HISTORY_STEPS:
                del self._history[:index + 1]
                break

    def _drop_unchanged_step(self, step: dict) -> None:
        """A step whose action failed before the picture changed is no step at all."""
        if self._history and self._history[-1] is step and step["ref"] is getattr(self.engine, "source_pil_image", None):
            self._history.pop()

    def _restore_step(self, step: dict) -> bool:
        if step["image"] is None or not self._apply_edited_picture(step["image"].copy(), step["label"], record=False):
            return False
        self._background_method, self._background_base = step["method"], step["base"]
        self._background_base_edited, self._picture_edited = step["base_edited"], step["edited"]
        if bool(getattr(self.engine, "background_removal_enabled", False)) != step["color_key"]:
            self.set_background_removal_state(enabled=step["color_key"], invalidate=True)
        self.ai.forget_image()
        return True

    def undo_edit(self) -> bool:
        """«Отменить»: the picture one step back (any edit, the background included)."""
        if self.edit_busy:
            raise RuntimeError("Картинка ещё обрабатывается. Дождитесь окончания.")
        if not self._history:
            self.statusChanged.emit("warn: Отменять нечего.")
            return False
        step = self._history.pop()
        current = self._snapshot(step["label"])
        if not self._restore_step(step):
            self._history.append(step)
            return False
        self._future.append(current)
        self.statusChanged.emit("Отменено: " + step["label"] + ".")
        self.aiChanged.emit()
        return True

    def redo_edit(self) -> bool:
        """«Повторить»: the step that was undone."""
        if self.edit_busy:
            raise RuntimeError("Картинка ещё обрабатывается. Дождитесь окончания.")
        if not self._future:
            self.statusChanged.emit("warn: Повторять нечего.")
            return False
        step = self._future.pop()
        current = self._snapshot(step["label"])
        if not self._restore_step(step):
            self._future.append(step)
            return False
        self._history.append(current)
        self.statusChanged.emit("Повторено: " + step["label"] + ".")
        self.aiChanged.emit()
        return True

    # ----- new pictures and edits --------------------------------------------------------
    def _picture_inserted(self) -> None:
        """A file, a paste, a link or a snapshot replaced the picture."""
        self.cancel_background_job()
        self._edit_job = None
        source = getattr(self.engine, "source_pil_image", None)
        self._picture_original = source.copy() if isinstance(source, Image.Image) else None
        self._picture_edited = False
        self._background_method, self._background_base, self._background_base_edited = "none", None, False
        self._history.clear()
        self._future.clear()
        self.ai.forget_image()
        # The colours are counted on the picture that will be drawn: after its background goes.
        self._colors_pending = bool(self.auto_colors) and self._picture_original is not None
        self._insert_note = ""
        refused = False
        if self.auto_background != "off" and self._picture_original is not None:
            try:
                self.set_background_method(self.auto_background, automatic=True)
            except Exception as error:                # the picture stays as inserted
                log.info("automatic background removal not started: %s", error)
                self.statusChanged.emit("warn: " + str(error) + " Картинка вставлена с фоном.")
                refused = True
            # Removing the background of a new picture is part of inserting it.
            self._history.clear()
        if self._background_job is None:
            self._insert_note = self._insert_colors_note()
            if self._insert_note and not refused:
                self.statusChanged.emit(self._insert_note)
        self.aiChanged.emit()

    def _apply_edited_picture(self, image: Image.Image, origin: str, *, record: bool = True) -> bool:
        """Replace the picture by an edit of it, keeping the inserted original."""
        if not self._guard_image_mutation("an image edit"):
            return False
        if record:
            self._push_history(self._snapshot(origin))
        self._engine_set_image(image.convert("RGBA"), origin=origin)
        self._set_session_image_source("clipboard_cache", None)
        revision = self._mark_render_dirty("image_edit")
        self.schedule_stencil_sync("image_edit", revision, force_hide=True)
        self._rebuild_preview_if_possible()
        self._emit_session_state_changed("image-edit", sections=("image", "painter"))
        self._picture_edited = True
        self.aiChanged.emit()
        return True

    def restore_original(self) -> bool:
        """«Вернуть исходник»: the picture as it was inserted, without any edit (one step)."""
        if self._picture_original is None or not self._picture_edited:
            self.statusChanged.emit("warn: Картинка не менялась — возвращать нечего.")
            return False
        if self.edit_busy:
            raise RuntimeError("Картинка ещё обрабатывается. Дождитесь окончания.")
        if not self._apply_edited_picture(self._picture_original.copy(), "Исходная картинка"):
            return False
        self._picture_edited = False
        self._background_method, self._background_base, self._background_base_edited = "none", None, False
        self.ai.forget_image()
        self.statusChanged.emit("Исходная картинка возвращена. «Отменить» вернёт правки.")
        self.aiChanged.emit()
        return True

    # ----- filters and colour adjustments (IMAGE-EDIT-002) --------------------------------------
    def _current_picture(self) -> Image.Image:
        source = getattr(self.engine, "source_pil_image", None)
        if not isinstance(source, Image.Image):
            raise ValueError("Сначала откройте картинку.")
        return source

    def _run_edit(self, label: str, work) -> bool:
        if self.edit_busy or self.ai.busy():
            raise RuntimeError("Картинка ещё обрабатывается. Дождитесь окончания.")
        if not self._guard_image_mutation("an image edit"):
            return False
        picture = self._current_picture().copy()
        token = object()
        self._edit_job = token
        self.aiChanged.emit()

        def run():
            try:
                result = (work(picture), None)
            except Exception as error:      # reported on the Qt thread
                result = (None, error)
            self._invoke_on_qt(lambda: self._edit_ready(token, label, *result))

        threading.Thread(target=run, name="image-edit", daemon=True).start()
        return True

    def _edit_ready(self, token, label, picture, error) -> None:
        if token is not self._edit_job:
            return
        self._edit_job = None
        if error is not None or picture is None:
            known = isinstance(error, ValueError)
            log.warning("Image edit failed (%s): %s", label, error, exc_info=not known)
            self.statusChanged.emit("warn: " + (str(error) if known else label + ": не получилось."))
            self.aiChanged.emit()
            return
        if self._apply_edited_picture(picture, label):
            self.statusChanged.emit("Готово: " + label + ". Отменить — Ctrl+Z в «Обработке».")

    def apply_filter(self, filter_id: str) -> bool:
        if filter_id not in image_filters.FILTERS:
            raise ValueError("Неизвестный фильтр.")
        label = image_filters.FILTERS[filter_id][1]
        return self._run_edit(label, lambda picture: image_filters.apply_filter(picture, filter_id))

    def apply_adjustments(self, values: dict) -> bool:
        if image_filters.is_neutral(values):
            return False
        image_filters.adjust(Image.new("RGBA", (1, 1)), values)          # validates before the worker
        return self._run_edit("Цвет и свет", lambda picture: image_filters.adjust(picture, values))

    def adjustment_preview(self, values: dict):
        """A quick small picture with the slider values, for the live preview."""
        if image_filters.is_neutral(values):
            return None
        picture = self._current_picture()
        small = picture.copy()
        small.thumbnail((640, 640))
        from ui.services.image_utils import pil_to_qimage
        return pil_to_qimage(image_filters.adjust(small, values))

    # ----- background ------------------------------------------------------------------
    def set_background_method(self, method: str, *, automatic: bool = False) -> bool:
        """Make `method` the way the background goes; always from the picture with it."""
        if method not in BACKGROUND_METHODS:
            raise ValueError("Неизвестный способ убрать фон.")
        if self.edit_busy or self.ai.busy():
            raise RuntimeError("Картинка ещё обрабатывается. Дождитесь окончания.")
        if not self._guard_image_mutation("changing the background"):
            return False
        source = getattr(self.engine, "source_pil_image", None)
        if not isinstance(source, Image.Image):
            raise ValueError("Сначала откройте картинку.")
        if method == "ai":
            if not self.ai.prefs.enabled:
                raise RuntimeError("Нейросети выключены: включите их на странице «AI».")
            if not self.ai.usable("background"):
                raise RuntimeError("Нет модели удаления фона: установите её на странице «AI».")
        step = self._snapshot({"none": "Фон оставлен", "auto": "Фон убран автоматически", "ai": "Фон убран нейросетью",
                               "color": "Фон по цвету"}[method])
        self._push_history(step)
        replaced = self._background_method in ("auto", "ai")
        base = self._background_base if replaced else source.copy()
        base_edited = self._background_base_edited if replaced else self._picture_edited
        if replaced:
            # the previous method's picture is dropped; the next one starts from the full picture
            if not self._apply_edited_picture(base.copy(), "Картинка с фоном", record=False):
                self._drop_unchanged_step(step)
                return False
            self._picture_edited = base_edited
        self._background_method, self._background_base, self._background_base_edited = "none", None, False
        if bool(getattr(self.engine, "background_removal_enabled", False)) != (method == "color"):
            self.set_background_removal_state(enabled=method == "color", invalidate=True)
        if method == "auto":
            self._start_cutout(base, base_edited, step, automatic=automatic)
        elif method == "ai":
            self._start_ai_background(base, base_edited, step, automatic=automatic)
        self.aiChanged.emit()
        return True

    def _start_cutout(self, base: Image.Image, base_edited: bool, step: dict, *, automatic: bool) -> None:
        from application import cutout
        job = ("auto", object(), step)
        self._background_job = job
        self.statusChanged.emit("Убираю фон…")

        def work():
            try:
                result = (cutout.cut_out(base), None)
            except Exception as error:      # reported on the Qt thread; the picture stays as it was
                result = (None, error)
            self._invoke_on_qt(lambda: self._background_ready(job, base, base_edited, *result, automatic=automatic))

        threading.Thread(target=work, name="image-cutout", daemon=True).start()

    def _start_ai_background(self, base: Image.Image, base_edited: bool, step: dict, *, automatic: bool) -> None:
        job = ("ai", object(), step)
        self._background_job = job

        def done(_kind, result, _key):
            picture, error = (result, None) if isinstance(result, Image.Image) else (None, RuntimeError("Нейросеть не убрала фон."))
            self._invoke_on_qt(lambda: self._background_ready(job, base, base_edited, picture, error, automatic=automatic))

        try:
            self.ai.start("background", base, self._ai_key(), {}, done)
        except Exception:
            self._background_job = None
            self._drop_unchanged_step(step)
            raise

    def _check_background_job(self) -> None:
        job = self._background_job
        if job is not None and job[0] == "ai" and not self.ai.busy():
            # the result would have arrived first (queued before this notification)
            self._background_job = None
            self._drop_unchanged_step(job[2])
            self._insert_colors_note()
            self.statusChanged.emit("warn: Нейросеть не убрала фон. Картинка осталась с фоном.")
            self.aiChanged.emit()

    def _background_ready(self, job, base, base_edited, picture, error, *, automatic: bool) -> None:
        if job is not self._background_job:
            return                                # replaced by a newer picture or job
        self._background_job = None
        method = job[0]
        if error is not None or picture is None:
            known = isinstance(error, (ValueError, RuntimeError))
            log.warning("Background not removed (%s): %s", method, error, exc_info=not known)
            self._drop_unchanged_step(job[2])
            self._insert_colors_note()
            self.statusChanged.emit("warn: " + (str(error) if known and error else "Фон убрать не удалось.")
                                    + " Картинка осталась с фоном.")
            self.aiChanged.emit()
            return
        if not self._apply_edited_picture(picture, "Без фона", record=False):
            self._insert_colors_note()
            self.aiChanged.emit()
            return
        self._background_method, self._background_base, self._background_base_edited = method, base, base_edited
        self.ai.forget_image()
        how = "автоматически" if method == "auto" else "нейросетью"
        colours = self._insert_colors_note()
        self.statusChanged.emit(("Фон убран " + how + " при вставке. " if automatic else "Фон убран " + how + ". ")
                                + "Вернуть его: «Обработка» → «Оставить»." + (" " + colours if colours else ""))
        self.aiChanged.emit()

    def cancel_background_job(self) -> None:
        """A newer picture wins over a background removal still running."""
        job, self._background_job = self._background_job, None
        if job is not None and job[0] == "ai":
            self.ai.cancel_job()
