"""Custom title bar on Windows without losing native window behaviour.

The QML window is frameless (Qt.FramelessWindowHint): the app header carries the
title, the caption buttons and the drag area, like Steam. A plain frameless window
loses Aero Snap, edge resizing, the DWM shadow, Windows 11 rounded corners and the
minimize animation, so the native frame styles are put back and the frame itself is
hidden by answering WM_NCCALCSIZE; WM_NCHITTEST gives the resize edges back.
Dragging (with snap) and double-click maximize come from QML: startSystemMove().
"""
from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication

log = logging.getLogger(__name__)

WM_NCCALCSIZE = 0x0083
WM_NCHITTEST = 0x0084
HTCLIENT, HTLEFT, HTRIGHT, HTTOP, HTTOPLEFT, HTTOPRIGHT = 1, 10, 11, 12, 13, 14
HTBOTTOM, HTBOTTOMLEFT, HTBOTTOMRIGHT = 15, 16, 17
GWL_STYLE = -16
WS_CAPTION, WS_THICKFRAME = 0x00C00000, 0x00040000
WS_MINIMIZEBOX, WS_MAXIMIZEBOX, WS_SYSMENU = 0x00020000, 0x00010000, 0x00080000
SWP_FLAGS = 0x0001 | 0x0002 | 0x0004 | 0x0010 | 0x0020  # NOSIZE NOMOVE NOZORDER NOACTIVATE FRAMECHANGED


class _Margins(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int), ("right", ctypes.c_int), ("top", ctypes.c_int), ("bottom", ctypes.c_int)]


class _NcCalcSizeParams(ctypes.Structure):
    _fields_ = [("rgrc", wintypes.RECT * 3), ("lppos", ctypes.c_void_p)]


def _frame_thickness(hwnd) -> tuple[int, int]:
    user32 = ctypes.windll.user32
    try:
        dpi = user32.GetDpiForWindow(hwnd) or 96
        padded = user32.GetSystemMetricsForDpi(92, dpi)  # SM_CXPADDEDBORDER
        return (user32.GetSystemMetricsForDpi(32, dpi) + padded,   # SM_CXSIZEFRAME
                user32.GetSystemMetricsForDpi(33, dpi) + padded)   # SM_CYSIZEFRAME
    except Exception:
        padded = user32.GetSystemMetrics(92)
        return user32.GetSystemMetrics(32) + padded, user32.GetSystemMetrics(33) + padded


class _FrameFilter(QAbstractNativeEventFilter):
    def __init__(self):
        super().__init__()
        self.handles: set[int] = set()

    def nativeEventFilter(self, event_type, message):
        if event_type != b"windows_generic_MSG" or not self.handles:
            return False, 0
        msg = wintypes.MSG.from_address(int(message))
        hwnd = int(msg.hWnd or 0)
        if hwnd not in self.handles:
            return False, 0
        user32 = ctypes.windll.user32
        if msg.message == WM_NCCALCSIZE and msg.wParam:
            if user32.IsZoomed(hwnd):
                # a maximized window hangs its frame off-screen: keep the client inside
                params = _NcCalcSizeParams.from_address(msg.lParam)
                fx, fy = _frame_thickness(hwnd)
                rect = params.rgrc[0]
                rect.left += fx; rect.right -= fx; rect.top += fy; rect.bottom -= fy
            return True, 0
        if msg.message == WM_NCHITTEST:
            if user32.IsZoomed(hwnd):
                return False, 0
            x = ctypes.c_short(msg.lParam & 0xFFFF).value
            y = ctypes.c_short((msg.lParam >> 16) & 0xFFFF).value
            rect = wintypes.RECT()
            user32.GetWindowRect(hwnd, ctypes.byref(rect))
            fx, fy = _frame_thickness(hwnd)
            left, right = x < rect.left + fx, x >= rect.right - fx
            top, bottom = y < rect.top + fy, y >= rect.bottom - fy
            code = (HTTOPLEFT if top and left else HTTOPRIGHT if top and right else
                    HTBOTTOMLEFT if bottom and left else HTBOTTOMRIGHT if bottom and right else
                    HTLEFT if left else HTRIGHT if right else HTTOP if top else HTBOTTOM if bottom else 0)
            if code:
                return True, code
        return False, 0


_filter: _FrameFilter | None = None


def apply(window) -> None:
    """Give a frameless QWindow its native frame behaviour back (Windows only).
    Idempotent: call again after flags change (pinning resets the styles)."""
    global _filter
    if sys.platform != "win32" or window is None:
        return
    try:
        hwnd = int(window.winId())
        user32 = ctypes.windll.user32
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        wanted = style | WS_CAPTION | WS_THICKFRAME | WS_MINIMIZEBOX | WS_MAXIMIZEBOX | WS_SYSMENU
        if _filter is None:
            _filter = _FrameFilter()
            QCoreApplication.instance().installNativeEventFilter(_filter)
        _filter.handles.add(hwnd)
        if wanted != style:
            user32.SetWindowLongW(hwnd, GWL_STYLE, wanted)
            user32.SetWindowPos(hwnd, 0, 0, 0, 0, 0, SWP_FLAGS)
        dwm = ctypes.windll.dwmapi
        # a 1 px extended frame keeps the DWM shadow; content covers it
        dwm.DwmExtendFrameIntoClientArea(hwnd, ctypes.byref(_Margins(0, 0, 1, 0)))
        corner = ctypes.c_int(2)  # DWMWCP_ROUND, Windows 11
        dwm.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(corner), ctypes.sizeof(corner))
    except Exception:
        log.debug("custom frame not applied", exc_info=True)
