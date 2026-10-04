"""Optional local AI: preferences, model installs and background jobs (AI-001).

Qt-free. The shell passes `notify`, which may be called from worker threads
and must only schedule a refresh on its own thread. All heavy work runs in
one job thread at a time; installs have their own threads, one per model.
Every job carries a cancel flag and the image revision it was started for, so
a late result for a replaced picture is dropped instead of applied.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional

from engine.ai import vision
from engine.ai.models import InstallCancelled, ModelStore
from engine.ai.runtime import DEVICES, SessionPool, best_gpu, directml_available, resolve_device
from infrastructure.documents import read_document, write_document

log = logging.getLogger("olegpainter.ai")

BACKGROUND_ORDER = ("bg.birefnet", "bg.isnet", "bg.u2netp")
JOB_KINDS = ("background", "select", "keep_selection", "erase_selection", "upscale", "lineart", "flatten", "depth")
_TASK_OF_JOB = {"background": "background", "select": "segment", "keep_selection": "segment",
                "erase_selection": "inpaint", "upscale": "upscale", "lineart": "lineart", "depth": "depth"}


@dataclass
class AiPreferences:
    enabled: bool = False
    device: str = "auto"
    background_model: str = ""
    depth_order: bool = False

    @classmethod
    def load(cls, path: Path) -> "AiPreferences":
        try:
            raw = read_document(path)
        except FileNotFoundError:
            return cls()
        except Exception:
            log.warning("AI preferences unreadable; using defaults", exc_info=True)
            return cls()
        prefs = cls()
        prefs.enabled = bool(raw.get("enabled", False))
        prefs.device = raw.get("device") if raw.get("device") in DEVICES else "auto"
        prefs.background_model = str(raw.get("background_model") or "")
        prefs.depth_order = bool(raw.get("depth_order", False))
        return prefs

    def save(self, path: Path) -> None:
        write_document(path, {"version": 1, **asdict(self)})


class AiUnavailable(RuntimeError):
    pass


class AiCenter:
    def __init__(self, prefs_path: Path, store: ModelStore | None = None,
                 notify: Callable[[], None] = lambda: None):
        self.prefs_path = Path(prefs_path)
        self.store = store or ModelStore()
        self.prefs = AiPreferences.load(self.prefs_path)
        self.pool = SessionPool()
        self._notify = notify
        self._lock = threading.RLock()
        self._installs: dict[str, dict] = {}
        self._job: Optional[dict] = None
        self._message = ""
        self._error = False
        self._message_model = None
        self._selector = None          # vision.ObjectSelector for the current picture
        self._selector_key = None
        self.selection_points: list[tuple[float, float, bool]] = []
        self.selection_mask = None     # bool array at source size, or None
        self._depth_cache: tuple[object, object] | None = None

    # ----- preferences ------------------------------------------------------
    @property
    def device(self) -> str:
        return resolve_device(self.prefs.device)

    def set_enabled(self, enabled: bool) -> None:
        with self._lock:
            self.prefs.enabled = bool(enabled)
            if not enabled:
                self.cancel_job()
                self.pool.clear()   # free GPU/RAM right away
                self.clear_selection()
            self.prefs.save(self.prefs_path)
        self._notify()

    def set_device(self, device: str) -> None:
        if device not in DEVICES:
            raise ValueError("Неизвестное устройство для AI.")
        with self._lock:
            self.prefs.device = device
            self.pool.clear()
            self._selector = None
            self.prefs.save(self.prefs_path)
        self._notify()

    def set_background_model(self, model_id: str) -> None:
        if self.store.get(model_id).task != "background":
            raise ValueError("Это не модель удаления фона.")
        with self._lock:
            self.prefs.background_model = model_id
            self.prefs.save(self.prefs_path)
        self._notify()

    def set_depth_order(self, enabled: bool) -> None:
        with self._lock:
            self.prefs.depth_order = bool(enabled)
            self.prefs.save(self.prefs_path)
        self._notify()

    def background_model(self) -> str | None:
        """The chosen installed background model, else the best installed one."""
        chosen = self.prefs.background_model
        if chosen and chosen in self.store.catalog and self.store.is_installed(chosen):
            return chosen
        return next((m for m in BACKGROUND_ORDER if m in self.store.catalog and self.store.is_installed(m)), None)

    def installed_for(self, task: str) -> str | None:
        if task == "background":
            return self.background_model()
        return next((i for i, info in self.store.catalog.items() if info.task == task and self.store.is_installed(i)), None)

    def usable(self, task: str) -> bool:
        return self.prefs.enabled and self.installed_for(task) is not None

    # ----- installs -----------------------------------------------------------
    def install(self, model_id: str) -> None:
        info = self.store.get(model_id)
        with self._lock:
            if model_id in self._installs:
                return
            state = {"done": 0, "total": info.download_size, "cancel": False, "error": ""}
            self._installs[model_id] = state

        def progress(done, total):
            state["done"], state["total"] = done, total
            now = time.monotonic()
            if now - state.get("tick", 0) > 0.25:
                state["tick"] = now
                self._notify()

        def run():
            try:
                self.store.install(model_id, progress, lambda: state["cancel"])
                self._set_message("Модель установлена: {title}.", model=model_id)
            except InstallCancelled:
                self._set_message("Установка отменена. Скачанная часть сохранена — можно продолжить позже.")
            except Exception as exc:
                log.warning("Model install failed: %s", model_id, exc_info=True)
                self._set_message(f"Не удалось установить модель: {exc}", error=True)
            finally:
                with self._lock:
                    self._installs.pop(model_id, None)
                self._notify()

        threading.Thread(target=run, name=f"AI install {model_id}", daemon=True).start()
        self._notify()

    def cancel_install(self, model_id: str) -> None:
        with self._lock:
            state = self._installs.get(model_id)
            if state is not None:
                state["cancel"] = True

    def remove(self, model_id: str) -> None:
        with self._lock:
            if model_id in self._installs:
                raise RuntimeError("Сначала отмените установку.")
            if self._job is not None:
                raise RuntimeError("Дождитесь окончания задачи AI.")
            self.pool.clear()
            self._selector = None
            self.store.remove(model_id)
        self._set_message("Модель удалена.")
        self._notify()

    # ----- jobs ---------------------------------------------------------------------
    def busy(self) -> bool:
        with self._lock:
            return self._job is not None

    def start(self, kind: str, image, image_key, params: dict,
              done: Callable[[str, object, object], None]) -> None:
        """Run one tool on `image` in the background. `done(kind, result, key)`
        is called from the worker thread only for a job that was not cancelled."""
        if kind not in JOB_KINDS:
            raise ValueError(f"Неизвестная задача AI: {kind}")
        if not self.prefs.enabled:
            raise AiUnavailable("Включите AI на странице «AI».")
        task = _TASK_OF_JOB.get(kind)
        if task is not None and self.installed_for(task) is None:
            raise AiUnavailable("Нужная модель не установлена. Установите её на странице «AI».")
        if kind in ("keep_selection", "erase_selection") and self.selection_mask is None:
            raise AiUnavailable("Сначала выберите объект щелчком по картинке.")
        if image is None:
            raise AiUnavailable("Сначала откройте картинку.")
        with self._lock:
            if self._job is not None:
                raise AiUnavailable("AI уже занят — дождитесь окончания или отмените задачу.")
            job = {"kind": kind, "cancel": False, "started": time.monotonic()}
            self._job = job
        self._set_message(_PROGRESS_TEXT.get(kind, "AI работает…"))

        def run():
            try:
                result = self._compute(kind, image, image_key, params, lambda: job["cancel"])
                if not job["cancel"]:
                    done(kind, result, image_key)
                    self._set_message(_DONE_TEXT.get(kind, ""))
            except Exception as exc:
                if not job["cancel"]:
                    log.warning("AI job %s failed", kind, exc_info=True)
                    self._set_message(f"AI: не получилось — {_readable(exc)}", error=True)
            finally:
                with self._lock:
                    if self._job is job:
                        self._job = None
                self._notify()

        threading.Thread(target=run, name=f"AI {kind}", daemon=True).start()
        self._notify()

    def cancel_job(self) -> None:
        with self._lock:
            if self._job is not None:
                self._job["cancel"] = True
                self._job = None
                self._message, self._error, self._message_model = "Задача AI отменена.", False, None
        self._notify()

    def _compute(self, kind, image, image_key, params, cancelled):
        device, store, pool = self.device, self.store, self.pool
        if kind == "background":
            mask = vision.foreground_mask(pool, store, device, self.background_model(), image)
            return vision.apply_mask(image, mask, threshold=float(params.get("threshold", 0.5)))
        if kind == "select":
            if self._selector is None or self._selector_key != image_key:
                self._selector = vision.ObjectSelector(pool, store, device, self.installed_for("segment"), image)
                self._selector_key = image_key
                self.selection_points = []
            points = list(self.selection_points) + [(float(params["x"]) * image.width,
                                                     float(params["y"]) * image.height,
                                                     bool(params.get("positive", True)))]
            mask = self._selector.mask(points)
            with self._lock:
                self.selection_points, self.selection_mask = points, mask
            return mask
        if kind == "keep_selection":
            return vision.apply_mask(image, self.selection_mask.astype("float32"), threshold=0.5)
        if kind == "erase_selection":
            return vision.erase(pool, store, device, self.installed_for("inpaint"), image, self.selection_mask)
        if kind == "upscale":
            return vision.upscale(store, self.installed_for("upscale"), image, scale=int(params.get("scale", 2)),
                                  style=str(params.get("style", "photo")), cancelled=cancelled)
        if kind == "lineart":
            return vision.line_art(pool, store, device, self.installed_for("lineart"), image)
        if kind == "flatten":
            return vision.flatten(image, strength=int(params.get("strength", 2)))
        if kind == "depth":
            return self.depth(image, image_key)
        raise ValueError(kind)

    # ----- selection ------------------------------------------------------------------
    def clear_selection(self) -> None:
        with self._lock:
            self.selection_points, self.selection_mask = [], None
        self._notify()

    def forget_image(self) -> None:
        """The picture changed: its selection and cached analysis are stale."""
        with self._lock:
            self._selector = None
            self._selector_key = None
            self.selection_points, self.selection_mask = [], None
            self._depth_cache = None

    # ----- analysis used by the drawing engine ------------------------------------
    def foreground(self, image):
        """Object probability for the engine's semantic order (None when unusable)."""
        if not self.usable("background"):
            return None
        return vision.foreground_mask(self.pool, self.store, self.device, self.background_model(), image)

    def depth(self, image, image_key=None):
        if not self.usable("depth"):
            return None
        key = image_key if image_key is not None else id(image)
        cache = self._depth_cache
        if cache is not None and cache[0] == key:
            return cache[1]
        result = vision.depth_map(self.pool, self.store, self.device, self.installed_for("depth"), image)
        self._depth_cache = (key, result)
        return result

    # ----- view -------------------------------------------------------------------------
    def _set_message(self, text: str, *, error: bool = False, model: str | None = None) -> None:
        # `model` fills {title} in the viewer's language at snapshot time.
        with self._lock:
            self._message, self._error, self._message_model = text, error, model
        self._notify()

    def snapshot(self, language: str = "ru") -> dict:
        gpu = best_gpu()
        with self._lock:
            installs = {k: dict(v) for k, v in self._installs.items()}
            job = dict(self._job) if self._job else None
            message, error, message_model = self._message, self._error, self._message_model
            has_selection = self.selection_mask is not None
            clicks = len(self.selection_points)
        if message_model in self.store.catalog:
            message = message.replace("{title}", self.store.get(message_model).text("title", language))
        models = []
        for model_id, info in self.store.catalog.items():
            install = installs.get(model_id)
            models.append({
                "id": model_id, "task": info.task,
                "title": info.text("title", language), "description": info.text("description", language),
                "license": info.license_name, "license_url": info.license_url,
                "size_mb": round(info.download_size / 2**20),
                "recommended": info.recommended,
                "installed": self.store.is_installed(model_id),
                "installing": install is not None,
                "progress": (install["done"] / install["total"]) if install and install["total"] else 0.0,
            })
        device = self.device
        return {
            "enabled": self.prefs.enabled,
            "device": self.prefs.device,
            "device_used": device,
            "gpu_name": gpu.name if gpu and directml_available() else "",
            "background_model": self.background_model() or "",
            "depth_order": self.prefs.depth_order,
            "models": models,
            "busy": job is not None,
            "job": job["kind"] if job else "",
            "message": message, "error": error,
            "has_selection": has_selection, "selection_clicks": clicks,
            "tools": {task: self.usable(task) for task in ("background", "segment", "inpaint", "upscale", "lineart", "depth")},
            "models_folder": str(self.store.root),
        }


_PROGRESS_TEXT = {
    "background": "Убираем фон…",
    "select": "Выделяем объект…",
    "keep_selection": "Оставляем только выделенное…",
    "erase_selection": "Стираем выделенное и дорисовываем фон…",
    "upscale": "Увеличиваем картинку…",
    "lineart": "Делаем контуры…",
    "flatten": "Упрощаем картинку…",
    "depth": "Оцениваем глубину…",
}


_DONE_TEXT = {
    "background": "Фон убран. Не понравилось — «Вернуть исходник».",
    "select": "Объект выделен. Щёлкните ещё, чтобы уточнить, или выберите действие.",
    "keep_selection": "Остался только выделенный объект.",
    "erase_selection": "Объект стёрт, фон дорисован.",
    "upscale": "Картинка увеличена.",
    "lineart": "Готово: рисуются только линии.",
    "flatten": "Картинка упрощена.",
    "depth": "",
}


def _readable(exc: Exception) -> str:
    # onnxruntime decodes a localized (cp1251) DirectML/CUDA error as UTF-8 and
    # raises UnicodeDecodeError: the user only needs to know the device failed.
    if isinstance(exc, UnicodeDecodeError):
        return "видеокарта не справилась. Выберите «Процессор» в поле «Где считать» и повторите."
    if isinstance(exc, MemoryError):
        return "не хватило памяти. Закройте лишние программы или выберите модель поменьше."
    try:
        text = str(exc)
    except Exception:
        text = ""
    return text.strip().splitlines()[0][:200] if text.strip() else type(exc).__name__


class EngineAiBackend:
    """What the drawing engine sees (engine.ai_backend). Only the semantic
    order asks it for analysis; everything visible goes through AiCenter."""

    def __init__(self, center: AiCenter):
        self.center = center

    @property
    def status(self) -> dict:
        return {"enabled": self.center.prefs.enabled, "reason": "" if self.center.prefs.enabled else "off",
                "message": "" if self.center.prefs.enabled else "AI выключен на странице «AI»."}

    def is_enabled(self) -> bool:
        return bool(self.center.prefs.enabled)

    def list_models(self, _task=None):
        return ()

    def provider_info(self):
        device = self.center.device
        return device, ("DirectML" if device == "gpu" else "CPU")

    def foreground(self, image):
        return self.center.foreground(image)

    def depth(self, image):
        return self.center.depth(image) if self.center.prefs.depth_order else None
