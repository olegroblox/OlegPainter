"""Mixin extracted from engine/olegpainter/core.py.

Low-level keyboard/quiet/abort helpers and main-window HWND tracking.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from infrastructure.input_ownership import automation_input

import math
import time
from collections import deque
from pathlib import Path  # noqa: F401

from .common import *  # noqa: F401,F403
from .translations import tr, LANGUAGES, current_lang  # noqa: F401
from .viewport_state import (  # noqa: F401
    VIEWPORT_UNSET,
    full_area_rect,
    normalize_area_rect,
    normalize_desktop_rect,
)


log = logging.getLogger("olegpainter.engine.olegpainter.input_emulation")


class InputEmulationMixin:
    """Low-level keyboard/quiet/abort helpers and main-window HWND tracking."""

    def _begin_capture_session(self, purpose):
        from infrastructure.capture_session import CaptureSession
        self._cancel_active_captures()
        self._capture_session = CaptureSession(purpose, on_error=self._capture_failed)

    def _capture_failed(self, error):
        try:
            self._cancel_active_captures()
        finally:
            message = "Ошибка захвата ввода: " + str(error)
            self._log(message, True)
            if self.status_callback:
                self.status_callback(message)

    def _end_capture_session(self, *, wait=False):
        session = getattr(self, "_capture_session", None)
        if session is not None:
            session.close()
            if wait:
                # Only stop/shutdown workers use this; never wait in a Qt slot
                # or inside a capture callback for that callback's own exit.
                session.finished.wait()

    def _capture_listener(self, factory, **callbacks):
        session = getattr(self, "_capture_session", None)
        if session is None:
            raise RuntimeError("Захват ввода не открыт.")
        return session.listener(factory, **callbacks)

    @contextmanager
    def _owned_automation_input(self, purpose, *, target_region=None):
        with automation_input.claim(self, purpose):
            try:
                self._input.begin(
                    self.draw_region if target_region is None else target_region,
                    focus=self.focus_target_window_enabled,
                )
                yield
                if self._input.failure is not None:
                    raise self._input.failure
            finally:
                try:
                    self._release_automation_input()
                finally:
                    self._input.end()

    def _release_automation_input(self):
        def release():
            failures = []
            for button in ("left", "right"):
                try:
                    if not self._mouse_up_with_settle(button):
                        failures.append(button)
                except Exception:
                    failures.append(button)
            if failures:
                raise RuntimeError("Не удалось отпустить кнопки мыши: " + ", ".join(failures))
        return automation_input.cleanup(self, release)

    def _keyboard_press(self, name: str, scan_code=None):
        self._input.key_down(scan_code if scan_code is not None else str(name))

    def _keyboard_release(self, name: str, scan_code=None):
        self._input.key_up(scan_code if scan_code is not None else str(name))

    def _quiet(self) -> bool:
        # Ничего не делать при выходе/тишине
        return bool(getattr(self, "is_shutting_down", False) or
                    getattr(self, "disable_stencil_autorestart", False))

    def _get_app_monitor_bbox(self):
        # Вернёт (left, top, right, bottom) монитора, где окно приложения.
        if os.name != 'nt':
            return None
        try:
            user32 = ctypes.windll.user32
            MONITOR_DEFAULTTONEAREST = 2

            # пробуем по окну приложения; если что — по курсору
            hwnd = getattr(self, 'main_hwnd', None)
            if hwnd and user32.IsWindow(hwnd):
                hmon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
            else:
                pt = wintypes.POINT()
                user32.GetCursorPos(ctypes.byref(pt))
                hmon = user32.MonitorFromPoint(pt, MONITOR_DEFAULTTONEAREST)

            class RECT(ctypes.Structure):
                _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                            ("right", ctypes.c_long), ("bottom", ctypes.c_long)]

            class MONITORINFO(ctypes.Structure):
                _fields_ = [("cbSize", ctypes.c_ulong),
                            ("rcMonitor", RECT),
                            ("rcWork", RECT),
                            ("dwFlags", ctypes.c_ulong)]

            mi = MONITORINFO()
            mi.cbSize = ctypes.sizeof(MONITORINFO)
            if not user32.GetMonitorInfoW(hmon, ctypes.byref(mi)):
                return None

            return (mi.rcMonitor.left, mi.rcMonitor.top, mi.rcMonitor.right, mi.rcMonitor.bottom)
        except Exception as e:
            self._log(f"Monitor detection error: {e}", True)
            return None

    def set_main_window_hwnd(self, hwnd) -> None:
        try:
            value = int(hwnd)
        except Exception:
            value = None
        self.main_hwnd = value if value and value > 0 else None

    def _sleep_with_abort(self, duration: float, step: float = 0.05, *, check_target=True) -> bool:
        """Sleep in slices so automation can be interrupted; returns False if cancelled."""
        remaining = max(0.0, float(duration))
        if remaining <= 0:
            return not self._automation_cancelled()
        try:
            deadline = time.perf_counter() + remaining
        except Exception:
            try:
                time.sleep(remaining)
            except Exception:
                log.debug('ignored exception in time.sleep(remaining)', exc_info=True)
            return not self._automation_cancelled()

        try:
            step = float(step)
        except Exception:
            step = 0.05
        if step <= 0:
            step = 0.05

        while True:
            if check_target:
                self._input.check_target()
            if self._automation_cancelled():
                return False
            now = time.perf_counter()
            remaining_time = deadline - now
            if remaining_time <= 0:
                return True
            sleep_slice = step if remaining_time > step else remaining_time
            if sleep_slice <= 0:
                sleep_slice = remaining_time
            if sleep_slice <= 0:
                return True
            try:
                time.sleep(sleep_slice)
            except Exception:
                # Even if sleep fails retry so cancellation can still be detected.
                log.debug('ignored exception in time.sleep(sleep_slice)', exc_info=True)
