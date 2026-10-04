from __future__ import annotations

from dataclasses import dataclass
from typing import Final

DesktopRect = tuple[int, int, int, int]
AreaRect = tuple[float, float, float, float]
VIEWPORT_UNSET: Final = object()


@dataclass(frozen=True, slots=True)
class ViewportState:
    draw_region_desktop_px: DesktopRect | None
    stretch_to_area: bool
    manual_rect_area_px: AreaRect | None
    target_monitor_id: str | None = None
    target_monitor_rect_desktop_px: DesktopRect | None = None
    stale: bool = False


def normalize_desktop_rect(rect) -> DesktopRect | None:
    if not isinstance(rect, (tuple, list)) or len(rect) != 4:
        return None
    try:
        x, y, w, h = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
    except Exception:
        return None
    w_int = int(round(w))
    h_int = int(round(h))
    if w_int <= 0 or h_int <= 0:
        return None
    return (int(round(x)), int(round(y)), w_int, h_int)


def normalize_area_rect(rect) -> AreaRect | None:
    if rect is None:
        return None
    if not isinstance(rect, (tuple, list)) or len(rect) != 4:
        return None
    try:
        x, y, w, h = (float(rect[0]), float(rect[1]), float(rect[2]), float(rect[3]))
    except Exception:
        return None
    if w <= 0 or h <= 0:
        return None
    return (x, y, w, h)


def full_area_rect(draw_region) -> AreaRect | None:
    normalized = normalize_desktop_rect(draw_region)
    if normalized is None:
        return None
    _, _, w, h = normalized
    return (0.0, 0.0, float(w), float(h))
