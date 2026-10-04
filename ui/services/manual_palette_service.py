"""Mixin extracted from ui/services/painter_service.py.

Manual palette / mix / extra-actions: emit signals, engine bridges, capture flow.
"""
from __future__ import annotations

import logging
import re
import time  # noqa: F401

from PySide6.QtCore import QObject, QThread, Signal, Slot, QTimer  # noqa: F401

try:
    from ui.helpers.global_hotkey import GlobalHotkeyManager, HotkeyRegistrationError  # noqa: F401
except Exception:
    GlobalHotkeyManager = None  # type: ignore
    HotkeyRegistrationError = RuntimeError  # type: ignore

from ui.helpers.hotkey_definitions import (  # noqa: F401
    HOTKEY_DEFINITIONS,
    HotkeyDefinition,
    default_hotkey_profile,
    definition_for_code,
    definitions_by_scope,
    scopes as hotkey_scopes,
)
from ui.i18n import tr  # noqa: F401

log = logging.getLogger("olegpainter.painter_service")


class ManualPaletteServiceMixin:
    """Manual palette / mix / extra-actions: emit signals, engine bridges, capture flow."""

    def _emit_manual_palette_changed(self, coords=None):
        self._manual_palette_revision = getattr(self, "_manual_palette_revision", 0) + 1
        if coords is None:
            coords = getattr(self.engine, 'manual_palette_coords', None) if hasattr(self, 'engine') else None
        try:
            count = len(coords or [])
        except Exception:
            count = 0
        try:
            self.manualPaletteChanged.emit(int(count))
        except Exception:
            log.debug('ignored exception in self.manualPaletteChanged.emit(int(count))', exc_info=True)
        self._emit_capture_state()
        self._emit_session_state_changed("manual-palette", sections=("painter",))

    def _on_engine_manual_palette_changed(self, coords=None):
        if not QThread.isMainThread():
            self._invoke_on_qt(lambda: self._on_engine_manual_palette_changed(coords))
            return
        if self._is_shutting_down:
            return
        self._emit_manual_palette_changed(coords)
        if self._manual_palette_affects_preview():
            self._invalidate_heavy_preview(strict=True)

    def _emit_manual_mix_canvas_changed(self, rgb=None):
        if rgb is None and hasattr(self, 'engine'):
            rgb = getattr(self.engine, 'manual_mix_canvas_rgb', (255, 255, 255))
        try:
            if rgb is None:
                rgb_tuple = (255, 255, 255)
            else:
                rgb_tuple = tuple(int(max(0, min(255, int(v)))) for v in rgb)
        except Exception:
            rgb_tuple = (255, 255, 255)
        try:
            self.manualMixCanvasChanged.emit(rgb_tuple)
        except Exception:
            log.debug('ignored exception in self.manualMixCanvasChanged.emit(rgb_tuple)', exc_info=True)
        self._emit_session_state_changed("manual-mix-canvas", sections=("painter",))

    def _on_engine_manual_mix_canvas_changed(self, rgb=None):
        if not QThread.isMainThread():
            self._invoke_on_qt(lambda: self._on_engine_manual_mix_canvas_changed(rgb))
            return
        if self._is_shutting_down:
            return
        self._emit_manual_mix_canvas_changed(rgb)

    def manual_mix_snapshot(self):
        from application.color_mixing import valid_alpha_slider
        engine = self.engine
        return dict(enabled=bool(engine.manual_palette_mix_enabled), alpha=engine.manual_palette_mix_alpha,
                    canvas_hex="#{:02X}{:02X}{:02X}".format(*engine.manual_mix_canvas_rgb),
                    calibrated=valid_alpha_slider(engine.alpha_slider_params))

    def update_manual_mix(self, patch):
        from application.color_mixing import validate_mix_patch
        self._require_manual_palette_edit(getattr(self, "_manual_palette_revision", 0))
        if self.brush_learning_active:
            raise RuntimeError("Дождитесь завершения обучения кисти.")
        values = validate_mix_patch(patch)
        engine = self.engine
        attributes = {"enabled": "manual_palette_mix_enabled", "alpha": "manual_palette_mix_alpha",
                      "canvas_rgb": "manual_mix_canvas_rgb"}
        changed = {key: value for key, value in values.items() if getattr(engine, attributes[key]) != value}
        if not changed:
            return
        for key, value in changed.items():
            setattr(engine, attributes[key], value)
        engine._invalidate_manual_palette_cache()
        if "canvas_rgb" in changed:
            self._emit_manual_mix_canvas_changed()
        self._emit_session_state_changed("manual-mix", sections=("painter",))
        if self._manual_palette_affects_preview():
            self._invalidate_heavy_preview(strict=True)

    def _emit_extra_actions_changed(self, summary=None):
        if summary is None:
            getter = getattr(self.engine, "get_extra_actions_summary", None)
            if callable(getter):
                try:
                    summary = getter()
                except Exception:
                    summary = None
        if summary is None:
            summary = {}
        try:
            self.extraActionsChanged.emit(summary)
        except Exception:
            log.debug('ignored exception in self.extraActionsChanged.emit(summary)', exc_info=True)
        self._emit_capture_state()
        self._emit_session_state_changed("extra-actions", sections=("painter",))

    def _on_engine_extra_actions_changed(self, summary=None):
        self._emit_extra_actions_changed(summary)

    def set_manual_mix_enabled(self, enabled: bool):
        try:
            engine = self.engine
            if hasattr(engine, "manual_palette_mix_enabled"):
                previous_state = bool(getattr(engine, "manual_palette_mix_enabled", False))
                new_state = bool(enabled)
                engine.manual_palette_mix_enabled = new_state
                if previous_state != new_state:
                    # Manual mix affects clustering plan, so rebuild palette when the toggle changes.
                    self._invalidate_visuals(self._INVALIDATE_RENDER)
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_manual_mix_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: set_manual_mix_enabled failed: {e}')", exc_info=True)

    def set_palette_rotation_direction(self, direction: str):
        """HSV palette ring navigation direction ('cw' or 'ccw'). Used by color_picking
        when picking from the on-screen palette ring."""
        try:
            engine = self.engine
            value = "ccw" if str(direction).strip().lower() == "ccw" else "cw"
            if getattr(engine, "palette_rotation_direction_calib", None) != value:
                engine.palette_rotation_direction_calib = value
                self._emit_session_state_changed("palette-rotation", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_palette_rotation_direction failed: {e}")
            except Exception:
                log.debug("ignored exception emitting palette rotation error", exc_info=True)

    def set_manual_mix_alpha(self, value: float):
        try:
            if hasattr(self.engine, "manual_palette_mix_alpha"):
                alpha = max(0.0, min(1.0, float(value)))
                self.engine.manual_palette_mix_alpha = alpha
                self._emit_session_state_changed("manual-mix-alpha", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_manual_mix_alpha failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: set_manual_mix_alpha failed: {e}')", exc_info=True)
            return
        if bool(getattr(self.engine, "manual_palette_mix_enabled", False)):
            self._invalidate_visuals(self._INVALIDATE_RENDER)

    def capture_manual_mix_canvas_color(self):
        try:
            starter = getattr(self.engine, "start_manual_mix_background_capture", None)
            if callable(starter):
                starter()
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: capture_manual_mix_canvas_color failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: capture_manual_mix_canvas_color failed: {e}')", exc_info=True)

    def start_manual_palette_capture(self):
        if self._reject_input_during_brush_learning():
            return
        if self._thread_is_running(self._preview_thread):
            self.statusChanged.emit("warn: Дождитесь завершения подготовки изображения перед захватом палитры.")
            return
        try:
            if hasattr(self.engine, "start_manual_palette_capture"):
                self.engine.start_manual_palette_capture()
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: start_manual_palette_capture failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: start_manual_palette_capture failed: {e}')", exc_info=True)

    def manual_palette_snapshot(self):
        cache = self.engine._get_manual_palette_cache()
        by_index = cache["by_index"]
        entries = []
        for index, raw in enumerate(self.engine.manual_palette_coords):
            item = by_index.get(index)
            raw = raw if isinstance(raw, dict) else {}
            entries.append(dict(index=index, x=str(raw.get("x", "")), y=str(raw.get("y", "")),
                                hex=item["hex"] if item else "", valid=item is not None))
        return dict(revision=getattr(self, "_manual_palette_revision", 0), entries=entries,
                    maximum=self.engine.max_manual_palette_colors)

    def _require_manual_palette_edit(self, revision):
        if (self._is_shutting_down or self._is_drawing or self._stop_pending
                or self._compute_capture_state()["active"] or self.desktop_interaction
                or self._hotkey_capture_active or self._thread_is_running(self._worker_thread)):
            raise RuntimeError("Сначала завершите рисование или активный экранный инструмент.")
        if self._thread_is_running(self._preview_thread):
            raise RuntimeError("Дождитесь завершения подготовки изображения.")
        if revision != getattr(self, "_manual_palette_revision", 0):
            raise ValueError("Палитра изменилась. Загрузите актуальный образец перед сохранением.")

    def edit_manual_palette(self, operation, index, revision, x="", y="", hex_color=""):
        """Validated edits share engine cache invalidation, preview and session signals."""
        self._require_manual_palette_edit(revision)
        entries = list(self.engine.manual_palette_coords)
        if operation not in ("update", "remove", "clear"):
            raise ValueError("Неизвестная операция палитры.")
        if operation != "clear" and not 0 <= index < len(entries):
            raise ValueError("Образец палитры больше не существует.")
        if operation == "clear":
            entries = []
        elif operation == "remove":
            entries.pop(index)
        else:
            coordinates = []
            for value in (x, y):
                value = str(value).strip()
                if not re.fullmatch(r"-?\d+", value) or not -(2**31) <= int(value) < 2**31:
                    raise ValueError("Координаты должны быть целыми экранными пикселями.")
                coordinates.append(int(value))
            color = str(hex_color).strip().removeprefix("#")
            if not re.fullmatch(r"[0-9a-fA-F]{6}", color):
                raise ValueError("Введите цвет в формате #RRGGBB, например #FFFFFF.")
            x, y = coordinates
            if any(i != index and isinstance(item, dict) and item.get("x") == x and item.get("y") == y
                   for i, item in enumerate(entries)):
                raise ValueError("Эта координата уже есть в палитре. Выберите существующий образец.")
            entries[index] = dict(x=x, y=y, hex="#" + color.upper(),
                                  rgb=[int(color[n:n + 2], 16) for n in (0, 2, 4)])
        self.engine.manual_palette_coords = entries
        self.engine._invalidate_manual_palette_cache()
        self._on_engine_manual_palette_changed()

    def append_manual_palette_capture(self):
        self._require_manual_palette_edit(getattr(self, "_manual_palette_revision", 0))
        if len(self.engine.manual_palette_coords) >= self.engine.max_manual_palette_colors:
            raise ValueError("Палитра заполнена. Удалите ненужный образец перед добавлением.")
        self.start_manual_palette_capture()
        if not self.engine.is_capturing_manual_palette:
            raise RuntimeError("Не удалось начать захват палитры. Проверьте доступность ввода.")

    def toggle_manual_palette_capture(self):
        if self._reject_input_during_brush_learning():
            return
        if self._should_bounce_toggle("manual_palette"):
            return
        try:
            capturing = bool(getattr(self.engine, "is_capturing_manual_palette", False))
        except Exception:
            capturing = False
        try:
            if capturing:
                if hasattr(self.engine, "finish_manual_palette_capture"):
                    self.engine.finish_manual_palette_capture()
            else:
                self.append_manual_palette_capture()
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: toggle_manual_palette_capture failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: toggle_manual_palette_capture failed: {e}')", exc_info=True)

    def start_extra_actions_capture(self, slot: str = "pre"):
        if self._reject_input_during_brush_learning():
            return
        slot_key = self._normalize_actions_slot(slot)
        try:
            starter = getattr(self.engine, "start_extra_actions_capture", None)
            if callable(starter):
                starter(slot=slot_key)
                self._emit_capture_state()
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: start_extra_actions_capture[{slot_key}] failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: start_extra_actions_capture[{slot_key}] fail...", exc_info=True)
        finally:
            self._emit_extra_actions_changed()

    def toggle_extra_actions_capture(self, slot: str = "pre"):
        if self._reject_input_during_brush_learning():
            return
        slot_key = self._normalize_actions_slot(slot)
        if self._should_bounce_toggle(f"extra_actions_{slot_key}"):
            return
        try:
            capturing = bool(getattr(self.engine, "is_capturing_extra_actions", False))
        except Exception:
            capturing = False
        raw_active_slot = getattr(self.engine, "active_extra_actions_slot", None)
        active_slot = self._normalize_actions_slot(raw_active_slot) if raw_active_slot else None
        try:
            if capturing and active_slot == slot_key:
                finisher = getattr(self.engine, "finish_extra_actions_capture", None)
                if callable(finisher):
                    finisher()
            else:
                starter = getattr(self.engine, "start_extra_actions_capture", None)
                if callable(starter):
                    starter(slot=slot_key)
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: toggle_extra_actions_capture[{slot_key}] failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: toggle_extra_actions_capture[{slot_key}] fai...", exc_info=True)
        finally:
            self._emit_extra_actions_changed()

    def set_extra_actions_enabled(self, enabled: bool, slot: str = "pre"):
        slot_key = self._normalize_actions_slot(slot)
        try:
            setter = getattr(self.engine, "set_extra_actions_enabled", None)
            if callable(setter):
                setter(bool(enabled), slot_key)
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_extra_actions_enabled[{slot_key}] failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: set_extra_actions_enabled[{slot_key}] failed...", exc_info=True)
        finally:
            self._emit_extra_actions_changed()
