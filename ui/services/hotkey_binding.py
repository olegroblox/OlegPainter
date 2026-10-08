"""Mixin extracted from ui/services/painter_service.py.

Hotkey CRUD + global hotkey registration through GlobalHotkeyManager.
"""
from __future__ import annotations

import logging
import time  # noqa: F401

from PySide6.QtCore import QObject, QThread, Signal, Slot, QTimer, QCoreApplication, Qt  # noqa: F401
from PySide6.QtGui import QKeySequence

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
from engine.olegpainter.drawing_events import DrawingPhase
from infrastructure.capture_session import CaptureSession

log = logging.getLogger("olegpainter.painter_service")


class HotkeyBindingMixin:
    """Hotkey CRUD + global hotkey registration through GlobalHotkeyManager."""

    def set_close_pending(self, pending):
        self._close_pending = bool(pending)
        if pending:
            self.cancel_brush_learning()
        self._hotkey_capture_epoch += 1
        if self._hotkey_manager is not None:
            self._hotkey_manager.set_suppression_enabled(
                not pending and not self._hotkey_capture_active)

    def hotkey_capture_state(self):
        return {"active": self._hotkey_capture_active, "token": self._hotkey_capture_token}

    def begin_hotkey_capture(self) -> int:
        if self.brush_learning_active:
            raise ValueError("Дождитесь завершения обучения кисти.")
        if self._is_shutting_down or self._close_pending:
            raise ValueError("Программа закрывается.")
        if self._hotkey_capture_active:
            raise ValueError("Уже идёт назначение другой клавиши.")
        if (self.drawing_phase in (DrawingPhase.RUNNING, DrawingPhase.PAUSED, DrawingPhase.STOPPING)
                or self._worker_thread is not None):
            raise ValueError("Остановите рисование, прежде чем менять горячие клавиши.")
        self._hotkey_input_session = CaptureSession("назначение горячей клавиши")
        try:
            if self._hotkey_manager is not None:
                self._hotkey_manager.set_suppression_enabled(False)
        except Exception:
            self._hotkey_input_session.close()
            raise
        self._hotkey_capture_epoch += 1
        self._hotkey_capture_token += 1
        self._hotkey_capture_active = True
        self._hotkey_capture_deadline = time.monotonic() + 30
        self._hotkey_capture_timer.start(30000)
        self.hotkeyCaptureChanged.emit(self.hotkey_capture_state())
        return self._hotkey_capture_token

    def end_hotkey_capture(self, token: int) -> bool:
        if not self._hotkey_capture_active or token != self._hotkey_capture_token:
            return False
        self._hotkey_capture_timer.stop()
        if self._hotkey_manager is not None:
            self._hotkey_manager.set_suppression_enabled(True)
        self._hotkey_capture_active = False
        self._hotkey_input_session.close()
        self._hotkey_capture_epoch += 1
        self.hotkeyCaptureChanged.emit(self.hotkey_capture_state())
        return True

    def _expire_hotkey_capture(self):
        if not self._hotkey_capture_active:
            return
        remaining = self._hotkey_capture_deadline - time.monotonic()
        if remaining > 0:
            self._hotkey_capture_timer.start(max(1, int(remaining * 1000) + 1))
        else:
            self.end_hotkey_capture(self._hotkey_capture_token)

    def commit_hotkey_capture(self, token: int, code: str, sequence: str):
        if not self._hotkey_capture_active or token != self._hotkey_capture_token:
            return False, "Назначение клавиши уже завершено. Нажмите кнопку ещё раз."
        try:
            if time.monotonic() >= self._hotkey_capture_deadline:
                return False, "Время назначения клавиши истекло. Нажмите кнопку ещё раз."
            return self.set_hotkey(code, sequence)
        finally:
            self.end_hotkey_capture(token)

    def get_hotkeys(self):
        snapshot = {"global": {}, "app": {}}
        for definition in HOTKEY_DEFINITIONS:
            snapshot.setdefault(definition.scope, {})
            snapshot[definition.scope][definition.code] = self._hotkeys.get(definition.code, "")
        return {scope: dict(values) for scope, values in snapshot.items()}

    def set_hotkey(self, code: str, sequence: str):
        try:
            definition = definition_for_code(code)
        except KeyError:
            return False, f"Неизвестное действие горячей клавиши: {code}"
        try:
            problem = self._user_hotkey_problem(definition, sequence)
        except ValueError as error:
            return False, f"Не удалось изменить горячие клавиши: {error}"
        if problem:
            return False, problem
        return self.apply_hotkeys({definition.scope: {code: sequence}})

    def _user_hotkey_problem(self, definition, sequence) -> str:
        """Rules for a key the user assigns; saved profiles still load as they are."""
        normalized = self._normalize_sequence(sequence)
        if not normalized:
            # Drawing and learning minimise the window: this key is the only way to stop them.
            return ("Клавишу «Стоп» нельзя отключить: пока идёт рисунок, окно свёрнуто, и остановить его можно только ею."
                    if definition.code == "stop" else "")
        if definition.scope != "global":
            return ""
        combo = QKeySequence(normalized)[0]
        if combo.keyboardModifiers() & (Qt.ControlModifier | Qt.AltModifier | Qt.MetaModifier):
            return ""
        if Qt.Key_F1.value <= combo.key().value <= Qt.Key_F35.value or combo.key() in (Qt.Key_Pause, Qt.Key_ScrollLock):
            return ""
        # A plain or Shift+ key is not swallowed: it fires while typing in a chat or a game.
        return (f"{normalized} сработает, пока вы печатаете в чате или другой программе. "
                "Для глобальной клавиши выберите F1–F12 или сочетание с Ctrl или Alt.")

    def reset_hotkey(self, code: str):
        try:
            definition = definition_for_code(code)
        except KeyError:
            return False, f"Неизвестное действие горячей клавиши: {code}"
        return self.apply_hotkeys({definition.scope: {code: definition.default}})

    def reset_all_hotkeys(self):
        mapping = {}
        for item in HOTKEY_DEFINITIONS:
            mapping.setdefault(item.scope, {})[item.code] = item.default
        return self.apply_hotkeys(mapping)

    def register_global_hotkeys(self, enable_ctrl_v_global: bool = False):
        """Register system-wide hotkeys (works even when app is minimized)."""
        _ = enable_ctrl_v_global  # compatibility flag; paste hotkey is app scoped now
        self._register_global_hotkeys()

    def _emit_hotkeys_changed(self):
        try:
            self.hotkeysChanged.emit(self.get_hotkeys())
        except Exception:
            log.debug('ignored exception in self.hotkeysChanged.emit(self.get_hotkeys())', exc_info=True)

    def _register_global_hotkeys(self, profile=None):
        candidate = self._hotkeys if profile is None else profile
        if getattr(self, "_headless_qt_platform", False):
            self._using_native_hotkeys = False
            return True, ""
        manager = self._hotkey_manager
        if manager is None or not manager.available:
            message = "Глобальные горячие клавиши недоступны: нет доступа к системе."
            self.statusChanged.emit("warn: " + message)
            return False, message

        callbacks = self._global_action_map()
        generation = getattr(self, "_hotkey_generation", 0) + 1
        bindings = []
        for definition in definitions_by_scope("global"):
            seq = candidate.get(definition.code, "")
            if not seq:
                continue
            target = callbacks.get(definition.code)
            if target is None:
                return False, f"No handler for hotkey: {definition.code}"

            def handler(fn=target, expected=generation, action=definition.code):
                capture_epoch = getattr(self, "_hotkey_capture_epoch", 0)
                def invoke():
                    if (expected == self._hotkey_generation
                            and capture_epoch == getattr(self, "_hotkey_capture_epoch", 0)
                            and not getattr(self, "_hotkey_capture_active", False)
                            and not self._close_pending
                            and (not self.brush_learning_active or action == "stop")
                            and not getattr(self, "_is_shutting_down", False)):
                        # The shell may wrap screen tools (move its window aside and
                        # bring it back afterwards), the same as for its own buttons.
                        launcher = getattr(self, "hotkey_action_launcher", None)
                        if launcher is not None:
                            launcher(action, fn)
                        else:
                            fn()
                self._invoke_on_qt(invoke)

            bindings.append((seq, handler))
        try:
            ids = manager.replace(bindings)
        except Exception as exc:
            message = f"Не удалось назначить горячие клавиши: {exc}"
            self.statusChanged.emit("warn: " + message)
            return False, message
        self._hotkey_generation = generation
        self._registered_hotkey_ids = ids
        self._using_native_hotkeys = bool(ids)
        return True, ""

    def _refresh_hotkeys_async(self):
        if getattr(self, "_headless_qt_platform", False):
            return
        try:
            app = QCoreApplication.instance()
        except Exception:
            app = None
        if app is None:
            try:
                self._register_global_hotkeys()
            except Exception:
                log.debug('ignored exception in self._register_global_hotkeys()', exc_info=True)
            return
        try:
            QTimer.singleShot(0, self._register_global_hotkeys)
        except Exception:
            try:
                self._register_global_hotkeys()
            except Exception:
                log.debug('ignored exception in self._register_global_hotkeys()', exc_info=True)

    def _unregister_global_hotkeys(self):
        if self._hotkey_manager and getattr(self._hotkey_manager, "available", False):
            try:
                self._hotkey_manager.clear()
            except Exception:
                log.debug('ignored exception in self._hotkey_manager.clear()', exc_info=True)
        self._registered_hotkey_ids = []
        self._using_native_hotkeys = False

    def apply_hotkeys(self, mapping):
        """Merge provided mapping with current bindings and re-register global hotkeys."""
        if self.brush_learning_active:
            return False, "Дождитесь завершения обучения кисти."
        try:
            if not isinstance(mapping, dict):
                return False, "Неверный формат горячих клавиш."

            incoming: dict[str, dict[str, str]] = {}
            for scope, assignments in mapping.items():
                if isinstance(assignments, dict):
                    incoming[scope] = dict(assignments)
                else:
                    incoming[scope] = assignments  # type: ignore[assignment]

            legacy_global = incoming.get("global")
            if isinstance(legacy_global, dict) and "paste_clipboard" in legacy_global:
                seq = legacy_global.pop("paste_clipboard")
                target = incoming.setdefault("app", {})
                if isinstance(target, dict):
                    target.setdefault("paste_clipboard", seq)
                incoming["global"] = legacy_global

            new_profile = dict(self._hotkeys)
            for scope, assignments in incoming.items():
                if scope not in ("global", "app"):
                    return False, f"Неизвестная группа горячих клавиш: {scope}"
                if not isinstance(assignments, dict):
                    return False, f"Неверный формат группы горячих клавиш: {scope}"
                for code, seq in assignments.items():
                    try:
                        definition = definition_for_code(code)
                    except KeyError:
                        return False, f"Неизвестное действие горячей клавиши: {code}"
                    if definition.scope != scope:
                        return False, f"Неверная группа для горячей клавиши: {code}"
                    normalized = self._normalize_sequence(seq)
                    new_profile[code] = normalized

            for definition in HOTKEY_DEFINITIONS:
                new_profile.setdefault(definition.code, self._normalize_sequence(definition.default))

            # App and global actions overlap whenever our window is focused.
            seen = {}
            for definition in HOTKEY_DEFINITIONS:
                seq = new_profile.get(definition.code, "")
                if not seq:
                    continue
                key = seq.casefold()
                other = seen.get(key)
                if other:
                    label = tr(other.label_key) if other.label_key else other.label
                    return False, f"Сочетание {seq} уже назначено: «{label}». Сначала освободите или смените его."
                seen[key] = definition

            ok, message = self._register_global_hotkeys(new_profile)
            if not ok:
                return False, message
            self._hotkeys = new_profile
            self._emit_hotkeys_changed()
            return True, ""
        except Exception as exc:
            return False, f"Не удалось изменить горячие клавиши: {exc}"
