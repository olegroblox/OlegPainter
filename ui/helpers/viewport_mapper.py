from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Iterable

from PySide6.QtCore import QRect
from PySide6.QtGui import QGuiApplication, QScreen

from engine.olegpainter.viewport_state import DesktopRect, normalize_desktop_rect
import logging

log = logging.getLogger("olegpainter.ui.helpers.viewport_mapper")

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _MONITOR_DEFAULTTONEAREST = 2
    _MONITORINFOF_PRIMARY = 1
    _MDT_EFFECTIVE_DPI = 0

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class _MONITORINFOEX(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", _RECT),
            ("rcWork", _RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    _MONITORENUMPROC = ctypes.WINFUNCTYPE(
        wintypes.BOOL,
        wintypes.HMONITOR,
        wintypes.HDC,
        ctypes.POINTER(_RECT),
        wintypes.LPARAM,
    )

    try:
        _USER32 = ctypes.windll.user32
    except Exception:
        _USER32 = None  # type: ignore[assignment]
    try:
        _SHCORE = ctypes.windll.shcore
    except Exception:
        _SHCORE = None  # type: ignore[assignment]
else:
    _USER32 = None
    _SHCORE = None
    _MONITOR_DEFAULTTONEAREST = 2
    _MONITORINFOF_PRIMARY = 1


@dataclass(frozen=True, slots=True)
class MonitorSnapshot:
    monitor_id: str
    device_name: str
    desktop_rect: DesktopRect
    logical_rect: DesktopRect
    scale_x: float
    scale_y: float
    is_primary: bool = False
    screen_name: str | None = None


def normalize_monitor_name(value: str | None) -> str:
    raw = str(value or "").strip().upper()
    if raw.startswith("\\\\.\\"):
        raw = raw[4:]
    return raw


def _qt_scale(screen: QScreen | None) -> tuple[float, float]:
    if screen is None:
        return (1.0, 1.0)
    try:
        ratio = float(screen.devicePixelRatio())
        if ratio > 0:
            return (ratio, ratio)
    except Exception:
        log.debug('ignored exception in ratio = float(screen.devicePixelRatio())', exc_info=True)
    try:
        dpi = float(screen.logicalDotsPerInch())
        if dpi > 0:
            scale = max(1.0, dpi / 96.0)
            return (scale, scale)
    except Exception:
        log.debug('ignored exception in dpi = float(screen.logicalDotsPerInch())', exc_info=True)
    return (1.0, 1.0)


def _fallback_snapshot_from_screen(screen: QScreen) -> MonitorSnapshot:
    geo = screen.geometry()
    scale_x, scale_y = _qt_scale(screen)
    desktop_rect = (
        int(round(geo.x() * scale_x)),
        int(round(geo.y() * scale_y)),
        max(1, int(round(geo.width() * scale_x))),
        max(1, int(round(geo.height() * scale_y))),
    )
    logical_rect = (geo.x(), geo.y(), max(1, geo.width()), max(1, geo.height()))
    monitor_id = normalize_monitor_name(screen.name()) or f"SCREEN_{id(screen)}"
    return MonitorSnapshot(
        monitor_id=monitor_id,
        device_name=monitor_id,
        desktop_rect=desktop_rect,
        logical_rect=logical_rect,
        scale_x=max(scale_x, 1e-6),
        scale_y=max(scale_y, 1e-6),
        is_primary=screen == QGuiApplication.primaryScreen(),
        screen_name=screen.name(),
    )


def collect_monitor_snapshots() -> list[MonitorSnapshot]:
    screens = list(QGuiApplication.screens())
    if not screens:
        return []

    qt_by_name = {normalize_monitor_name(screen.name()): screen for screen in screens}
    snapshots: list[MonitorSnapshot] = []

    if os.name == "nt" and _USER32 is not None:
        enum_monitors = []

        @_MONITORENUMPROC
        def _enum_proc(hmon, hdc, rect_ptr, lparam):
            enum_monitors.append(hmon)
            return True

        try:
            if _USER32.EnumDisplayMonitors(0, 0, _enum_proc, 0):
                for hmon in enum_monitors:
                    info = _MONITORINFOEX()
                    info.cbSize = ctypes.sizeof(info)
                    if not _USER32.GetMonitorInfoW(hmon, ctypes.byref(info)):
                        continue
                    left = int(info.rcMonitor.left)
                    top = int(info.rcMonitor.top)
                    right = int(info.rcMonitor.right)
                    bottom = int(info.rcMonitor.bottom)
                    width = max(1, right - left)
                    height = max(1, bottom - top)
                    device_name = info.szDevice.strip("\x00") if info.szDevice else ""
                    normalized_name = normalize_monitor_name(device_name)
                    screen = qt_by_name.get(normalized_name)
                    if screen is None and len(screens) == 1:
                        screen = screens[0]
                    dpi_x = dpi_y = 96.0
                    if _SHCORE is not None:
                        try:
                            dpi_x_raw = ctypes.c_uint()
                            dpi_y_raw = ctypes.c_uint()
                            if _SHCORE.GetDpiForMonitor(
                                hmon,
                                _MDT_EFFECTIVE_DPI,
                                ctypes.byref(dpi_x_raw),
                                ctypes.byref(dpi_y_raw),
                            ) == 0:
                                dpi_x = float(dpi_x_raw.value or 96.0)
                                dpi_y = float(dpi_y_raw.value or 96.0)
                        except Exception:
                            log.debug('ignored exception in dpi_x_raw = ctypes.c_uint()', exc_info=True)
                    if screen is not None:
                        geo = screen.geometry()
                        logical_rect = (geo.x(), geo.y(), max(1, geo.width()), max(1, geo.height()))
                        scale_x = width / max(geo.width(), 1)
                        scale_y = height / max(geo.height(), 1)
                    else:
                        scale_x = dpi_x / 96.0 if dpi_x > 0 else 1.0
                        scale_y = dpi_y / 96.0 if dpi_y > 0 else 1.0
                        logical_rect = (
                            int(round(left / max(scale_x, 1e-6))),
                            int(round(top / max(scale_y, 1e-6))),
                            max(1, int(round(width / max(scale_x, 1e-6)))),
                            max(1, int(round(height / max(scale_y, 1e-6)))),
                        )
                    snapshots.append(
                        MonitorSnapshot(
                            monitor_id=normalized_name or f"HMON_{int(hmon)}",
                            device_name=device_name or normalized_name,
                            desktop_rect=(left, top, width, height),
                            logical_rect=logical_rect,
                            scale_x=max(scale_x, 1e-6),
                            scale_y=max(scale_y, 1e-6),
                            is_primary=bool(info.dwFlags & _MONITORINFOF_PRIMARY),
                            screen_name=screen.name() if screen is not None else None,
                        )
                    )
        except Exception:
            snapshots = []

    if snapshots:
        return snapshots

    return [_fallback_snapshot_from_screen(screen) for screen in screens]


def _rect_center(rect: DesktopRect) -> tuple[float, float]:
    x, y, w, h = rect
    return (x + (w / 2.0), y + (h / 2.0))


def _overlap_area(rect_a: DesktopRect, rect_b: DesktopRect) -> int:
    ax, ay, aw, ah = rect_a
    bx, by, bw, bh = rect_b
    left = max(ax, bx)
    top = max(ay, by)
    right = min(ax + aw, bx + bw)
    bottom = min(ay + ah, by + bh)
    if right <= left or bottom <= top:
        return 0
    return int((right - left) * (bottom - top))


def select_monitor_snapshot(
    rect,
    monitors: Iterable[MonitorSnapshot] | None = None,
    *,
    allow_fallback: bool = True,
) -> MonitorSnapshot | None:
    normalized = normalize_desktop_rect(rect)
    if normalized is None:
        return None
    items = list(monitors) if monitors is not None else collect_monitor_snapshots()
    if not items:
        return None
    center_x, center_y = _rect_center(normalized)
    containing: list[MonitorSnapshot] = []
    for item in items:
        mx, my, mw, mh = item.desktop_rect
        if mx <= center_x < mx + mw and my <= center_y < my + mh:
            containing.append(item)
    if containing:
        return max(containing, key=lambda item: _overlap_area(normalized, item.desktop_rect))
    overlaps = [(item, _overlap_area(normalized, item.desktop_rect)) for item in items]
    overlaps.sort(key=lambda pair: (pair[1], pair[0].is_primary), reverse=True)
    if overlaps and overlaps[0][1] > 0:
        return overlaps[0][0]
    if not allow_fallback:
        return None
    primary = next((item for item in items if item.is_primary), None)
    return primary or items[0]


def desktop_rect_to_ui_rect(rect, monitor: MonitorSnapshot | None) -> DesktopRect | None:
    normalized = normalize_desktop_rect(rect)
    if normalized is None or monitor is None:
        return normalized
    dx, dy, dw, dh = normalized
    mx, my, _, _ = monitor.desktop_rect
    lx, ly, _, _ = monitor.logical_rect
    ui_x = lx + int(round((dx - mx) / max(monitor.scale_x, 1e-6)))
    ui_y = ly + int(round((dy - my) / max(monitor.scale_y, 1e-6)))
    ui_w = max(1, int(round(dw / max(monitor.scale_x, 1e-6))))
    ui_h = max(1, int(round(dh / max(monitor.scale_y, 1e-6))))
    return (ui_x, ui_y, ui_w, ui_h)


def ui_rect_to_desktop_rect(rect, monitor: MonitorSnapshot | None) -> DesktopRect | None:
    normalized = normalize_desktop_rect(rect)
    if normalized is None or monitor is None:
        return normalized
    ux, uy, uw, uh = normalized
    mx, my, _, _ = monitor.desktop_rect
    lx, ly, _, _ = monitor.logical_rect
    dx = mx + int(round((ux - lx) * monitor.scale_x))
    dy = my + int(round((uy - ly) * monitor.scale_y))
    dw = max(1, int(round(uw * monitor.scale_x)))
    dh = max(1, int(round(uh * monitor.scale_y)))
    return (dx, dy, dw, dh)


def monitor_binding_for_rect(rect, monitors: Iterable[MonitorSnapshot] | None = None) -> tuple[str | None, DesktopRect | None]:
    snapshot = select_monitor_snapshot(rect, monitors=monitors, allow_fallback=False)
    if snapshot is None:
        return (None, None)
    return (snapshot.monitor_id, snapshot.desktop_rect)


def qrect_from_tuple(rect: DesktopRect | None) -> QRect:
    if rect is None:
        return QRect()
    x, y, w, h = rect
    return QRect(int(x), int(y), int(w), int(h))
