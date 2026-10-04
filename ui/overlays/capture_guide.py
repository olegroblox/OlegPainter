"""Passive capture feedback shared by both application shells."""
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPainter, QPen

from .pointer_surface import PointerSurface
from .theme import ACCENT, draw_banner, draw_crosshair, draw_label


class CaptureGuideOverlay(PointerSurface):
    def __init__(self):
        super().__init__()
        self._text = ""
        self._count = 0
        self._points = []
        self._compact_marks = False

    def set_state(self, text, count, points=(), *, compact_marks=False):
        state = (str(text), int(count), list(points), bool(compact_marks))
        if state == (self._text, self._count, self._points, self._compact_marks):
            return
        self._text, self._count, self._points, self._compact_marks = state
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        bounds = self.active_screen_rect()
        pos = self.mapFromGlobal(self._cursor)
        draw_crosshair(p, bounds, pos)
        for pt in self.coordinates.logical_items(self._points[-80:]):
            point = self.mapFromGlobal(QPoint(pt["x"], pt["y"]))
            p.setPen(QPen(ACCENT, 2))
            p.setBrush(Qt.NoBrush)
            # Dense palettes need the swatch and its neighbours to remain visible.
            # No filled centre, numeric badges or cursor-coordinate tooltip here.
            p.drawEllipse(point, 6 if self._compact_marks else 9, 6 if self._compact_marks else 9)
            if not self._compact_marks:
                p.setBrush(ACCENT)
                p.drawEllipse(point, 4, 4)
            if pt.get("label") and not self._compact_marks:
                draw_label(p, self.rect(), point + QPoint(14, 14), pt["label"])
        x, y = self.coordinates.to_physical(self._cursor.x(), self._cursor.y())
        if not self._compact_marks:
            draw_label(p, bounds, pos + QPoint(18, 18), f"X {x}   Y {y} px")
        if self._text:
            draw_banner(p, bounds, self._text + (f"  ·  {self._count}" if self._count else ""))
        p.end()
