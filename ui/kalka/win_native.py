"""Платформенные функции для Windows через ctypes (без сторонних зависимостей).

Используются для трёх вещей, которые нельзя получить чистым Qt:
  * set_click_through  — пропускать клики мыши сквозь окно (режим трафарета);
  * set_tool_window    — убрать окно из панели задач;
  * register_hotkey    — глобальная горячая клавиша, работающая даже когда окно
                          в режиме сквозных кликов и не получает события мыши.

На не-Windows платформах все функции — безопасные заглушки.
"""

from __future__ import annotations

import sys

IS_WINDOWS = sys.platform == "win32"

# Константы WinAPI
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x00000020
WS_EX_LAYERED = 0x00080000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000

WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_NOREPEAT = 0x4000

if IS_WINDOWS:
    import ctypes
    from ctypes import wintypes

    _user32 = ctypes.windll.user32

    _user32.GetWindowLongW.restype = ctypes.c_long
    _user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
    _user32.SetWindowLongW.restype = ctypes.c_long
    _user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]
    _user32.RegisterHotKey.restype = wintypes.BOOL
    _user32.RegisterHotKey.argtypes = [
        wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT
    ]
    _user32.UnregisterHotKey.restype = wintypes.BOOL
    _user32.UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]


def _get_exstyle(hwnd: int) -> int:
    return _user32.GetWindowLongW(hwnd, GWL_EXSTYLE)


def _set_exstyle(hwnd: int, style: int) -> None:
    _user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)


def set_click_through(hwnd: int, enabled: bool) -> None:
    """Пассивный трафарет пропускает клики и не забирает фокус."""
    if not IS_WINDOWS or not hwnd:
        return
    style = _get_exstyle(hwnd)
    if enabled:
        style |= WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_NOACTIVATE
    else:
        style &= ~(WS_EX_TRANSPARENT | WS_EX_NOACTIVATE)  # сохраняем WS_EX_LAYERED
    _set_exstyle(hwnd, style)


def set_tool_window(hwnd: int, enabled: bool = True) -> None:
    """Убрать/вернуть окно в панель задач (tool window не показывается в taskbar)."""
    if not IS_WINDOWS or not hwnd:
        return
    style = _get_exstyle(hwnd)
    if enabled:
        style |= WS_EX_TOOLWINDOW
    else:
        style &= ~WS_EX_TOOLWINDOW
    _set_exstyle(hwnd, style)


def register_hotkey(hwnd: int, hotkey_id: int, mods: int, vk: int) -> bool:
    """Зарегистрировать глобальную горячую клавишу. Возвращает успех."""
    if not IS_WINDOWS or not hwnd:
        return False
    return bool(_user32.RegisterHotKey(hwnd, hotkey_id, mods | MOD_NOREPEAT, vk))


def unregister_hotkey(hwnd: int, hotkey_id: int) -> None:
    if not IS_WINDOWS or not hwnd:
        return
    _user32.UnregisterHotKey(hwnd, hotkey_id)


def parse_hotkey_message(message) -> int | None:
    """Из nativeEvent вернуть id сработавшей горячей клавиши или None."""
    if not IS_WINDOWS:
        return None
    try:
        import ctypes
        from ctypes import wintypes
        msg = wintypes.MSG.from_address(int(message))
        if msg.message == WM_HOTKEY:
            return int(msg.wParam)
    except (ValueError, OSError):
        return None
    return None
