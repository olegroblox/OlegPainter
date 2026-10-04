# -*- coding: utf-8 -*-
"""Pure-geometry controller for interactively moving/resizing a rectangle.

This is the testable core behind the stencil's "edit draw area" mode. It knows
nothing about Qt or screens — it operates on plain float rectangles in a single
coordinate space (the caller decides which: the overlay feeds it widget/monitor
pixels). Keeping the math here means the fiddly bits — handle hit-testing, min-size
and bounds clamping, edge snapping, aspect-ratio lock and arrow-key nudging — are
unit-tested in isolation instead of living in untestable GUI event handlers.

Regions returned by :meth:`AreaInteractionController.hit_test`:
    "" (none), "move", and the eight resize handles
    "n", "s", "e", "w", "nw", "ne", "se", "sw".
"""
from __future__ import annotations

from dataclasses import dataclass

REGION_NONE = ""
REGION_MOVE = "move"
RESIZE_CORNERS = ("nw", "ne", "se", "sw")
RESIZE_EDGES = ("n", "s", "e", "w")
RESIZE_REGIONS = RESIZE_CORNERS + RESIZE_EDGES

# Cursor hints — string ids the widget layer maps to concrete Qt cursors.
CURSOR_ARROW = "arrow"
CURSOR_MOVE = "move"
CURSOR_SIZE_VER = "ver"      # n / s
CURSOR_SIZE_HOR = "hor"      # e / w
CURSOR_SIZE_FDIAG = "fdiag"  # nw / se  (↖↘)
CURSOR_SIZE_BDIAG = "bdiag"  # ne / sw  (↗↙)

_CURSOR_BY_REGION = {
    REGION_MOVE: CURSOR_MOVE,
    "n": CURSOR_SIZE_VER, "s": CURSOR_SIZE_VER,
    "e": CURSOR_SIZE_HOR, "w": CURSOR_SIZE_HOR,
    "nw": CURSOR_SIZE_FDIAG, "se": CURSOR_SIZE_FDIAG,
    "ne": CURSOR_SIZE_BDIAG, "sw": CURSOR_SIZE_BDIAG,
}


def cursor_for_region(region: str) -> str:
    """Map a hit-test region to a cursor id (see ``CURSOR_*``)."""
    return _CURSOR_BY_REGION.get(region or REGION_NONE, CURSOR_ARROW)


def _clamp(value: float, lo: float, hi: float) -> float:
    if hi < lo:  # degenerate range (rect larger than bounds) — pin to lo
        return lo
    if value < lo:
        return lo
    if value > hi:
        return hi
    return value


@dataclass(frozen=True)
class Rect:
    """Immutable float rectangle (x, y, w, h) with convenience edges."""

    x: float
    y: float
    w: float
    h: float

    @property
    def left(self) -> float:
        return self.x

    @property
    def top(self) -> float:
        return self.y

    @property
    def right(self) -> float:
        return self.x + self.w

    @property
    def bottom(self) -> float:
        return self.y + self.h

    @property
    def center_x(self) -> float:
        return self.x + self.w / 2.0

    @property
    def center_y(self) -> float:
        return self.y + self.h / 2.0

    def as_tuple(self) -> tuple[float, float, float, float]:
        return (self.x, self.y, self.w, self.h)

    def as_int_tuple(self) -> tuple[int, int, int, int]:
        return (round(self.x), round(self.y), round(self.w), round(self.h))


class AreaInteractionController:
    """Stateful drag controller for a single rectangle.

    Typical lifecycle::

        region = ctrl.hit_test(px, py, rect)        # on hover / press
        if region:
            ctrl.begin(px, py, region, rect)        # on press
            rect = ctrl.update(px, py, bounds, ...)  # on move (repeat)
            ctrl.end()                               # on release

    All coordinates are in one space chosen by the caller; ``bounds`` is the
    allowed rectangle (e.g. the monitor) the area may not leave.
    """

    def __init__(self, *, handle_size: float = 14.0, min_size: float = 8.0, snap: float = 8.0) -> None:
        self.handle_size = float(handle_size)
        self.min_size = float(min_size)
        self.snap = float(snap)
        self._region = REGION_NONE
        self._start_x = 0.0
        self._start_y = 0.0
        self._start_rect = Rect(0.0, 0.0, 0.0, 0.0)

    # ----- state ---------------------------------------------------------- #
    @property
    def active(self) -> bool:
        return self._region != REGION_NONE

    @property
    def region(self) -> str:
        return self._region

    # ----- hit testing ---------------------------------------------------- #
    def hit_test(self, px: float, py: float, rect: Rect) -> str:
        """Return the region under (px, py): a resize handle, "move", or "" (none)."""
        if rect.w <= 0 or rect.h <= 0:
            return REGION_NONE
        half = self.handle_size / 2.0
        points = {
            "nw": (rect.left, rect.top),
            "ne": (rect.right, rect.top),
            "se": (rect.right, rect.bottom),
            "sw": (rect.left, rect.bottom),
            "n": (rect.center_x, rect.top),
            "s": (rect.center_x, rect.bottom),
            "w": (rect.left, rect.center_y),
            "e": (rect.right, rect.center_y),
        }
        # Corners take priority over edges; edges over the body.
        for code in RESIZE_CORNERS + RESIZE_EDGES:
            hx, hy = points[code]
            if abs(px - hx) <= half and abs(py - hy) <= half:
                return code
        if rect.left <= px <= rect.right and rect.top <= py <= rect.bottom:
            return REGION_MOVE
        return REGION_NONE

    # ----- drag ----------------------------------------------------------- #
    def begin(self, px: float, py: float, region: str, rect: Rect) -> None:
        self._region = region or REGION_NONE
        self._start_x = float(px)
        self._start_y = float(py)
        self._start_rect = rect

    def end(self) -> None:
        self._region = REGION_NONE

    def update(
        self,
        px: float,
        py: float,
        bounds: Rect,
        *,
        keep_aspect: bool = False,
        snap: bool = True,
    ) -> Rect:
        """Compute the new rectangle for the current drag at pointer (px, py)."""
        if self._region == REGION_NONE:
            return self._start_rect
        dx = float(px) - self._start_x
        dy = float(py) - self._start_y
        if self._region == REGION_MOVE:
            return self._move(dx, dy, bounds, snap)
        return self._resize(dx, dy, bounds, keep_aspect, snap)

    def _move(self, dx: float, dy: float, bounds: Rect, snap: bool) -> Rect:
        r = self._start_rect
        x = _clamp(r.x + dx, bounds.left, bounds.right - r.w)
        y = _clamp(r.y + dy, bounds.top, bounds.bottom - r.h)
        if snap and self.snap > 0:
            if abs(x - bounds.left) <= self.snap:
                x = bounds.left
            elif abs((x + r.w) - bounds.right) <= self.snap:
                x = bounds.right - r.w
            if abs(y - bounds.top) <= self.snap:
                y = bounds.top
            elif abs((y + r.h) - bounds.bottom) <= self.snap:
                y = bounds.bottom - r.h
        return Rect(x, y, r.w, r.h)

    def _resize(self, dx: float, dy: float, bounds: Rect, keep_aspect: bool, snap: bool) -> Rect:
        r = self._start_rect
        m = self.min_size
        region = self._region
        left, top, right, bottom = r.left, r.top, r.right, r.bottom
        if "w" in region:
            left = _clamp(r.left + dx, bounds.left, r.right - m)
            if snap and self.snap > 0 and abs(left - bounds.left) <= self.snap:
                left = bounds.left
        if "e" in region:
            right = _clamp(r.right + dx, r.left + m, bounds.right)
            if snap and self.snap > 0 and abs(right - bounds.right) <= self.snap:
                right = bounds.right
        if "n" in region:
            top = _clamp(r.top + dy, bounds.top, r.bottom - m)
            if snap and self.snap > 0 and abs(top - bounds.top) <= self.snap:
                top = bounds.top
        if "s" in region:
            bottom = _clamp(r.bottom + dy, r.top + m, bounds.bottom)
            if snap and self.snap > 0 and abs(bottom - bounds.bottom) <= self.snap:
                bottom = bounds.bottom
        new = Rect(left, top, right - left, bottom - top)
        if keep_aspect and region in RESIZE_CORNERS:
            new = self._apply_aspect(new, region)
        return new

    def _apply_aspect(self, rect: Rect, region: str) -> Rect:
        r0 = self._start_rect
        if r0.w <= 0 or r0.h <= 0:
            return rect
        aspect = r0.w / r0.h
        w, h = rect.w, rect.h
        # Drive the dimension that changed more; derive the other from the ratio.
        if abs(w - r0.w) >= abs(h - r0.h):
            h = w / aspect
        else:
            w = h * aspect
        # Anchor the corner opposite to the one being dragged.
        if region == "se":
            x, y = r0.left, r0.top
        elif region == "nw":
            x, y = r0.right - w, r0.bottom - h
        elif region == "ne":
            x, y = r0.left, r0.bottom - h
        else:  # sw
            x, y = r0.right - w, r0.top
        return Rect(x, y, w, h)

    # ----- keyboard nudge ------------------------------------------------- #
    def nudge(self, rect: Rect, dx: float, dy: float, bounds: Rect, *, resize: bool = False) -> Rect:
        """Move (or, with ``resize=True``, grow/shrink) the rect by (dx, dy)."""
        if resize:
            w = max(self.min_size, min(rect.w + dx, bounds.right - rect.x))
            h = max(self.min_size, min(rect.h + dy, bounds.bottom - rect.y))
            return Rect(rect.x, rect.y, w, h)
        x = _clamp(rect.x + dx, bounds.left, bounds.right - rect.w)
        y = _clamp(rect.y + dy, bounds.top, bounds.bottom - rect.h)
        return Rect(x, y, rect.w, rect.h)
