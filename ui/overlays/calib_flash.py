"""Static calibration marks: repaint on exposure, no animation timer."""
from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QPainter, QPen
from PySide6.QtWidgets import QApplication, QWidget

from .theme import ACCENT, DIM, draw_banner, draw_label
from .coordinates import DesktopCoordinates


class CalibFlashOverlay(QWidget):
    closed = Signal()

    def __init__(self, items: list, hint: str, timeout_ms: int):
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        geo = None
        for s in QApplication.screens():
            geo = s.geometry() if geo is None else geo.united(s.geometry())
        if geo is not None:
            self.setGeometry(geo)
        self._items = DesktopCoordinates().logical_items(items)
        self._hint = hint
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(int(timeout_ms))
        self._timeout.timeout.connect(self.close)
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def showEvent(self, event):
        self._timeout.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._timeout.stop()
        super().hideEvent(event)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        self.close()

    def keyPressEvent(self, event) -> None:  # noqa: N802
        self.close()

    def closeEvent(self, event) -> None:  # noqa: N802
        self._timeout.stop()
        self.closed.emit()
        super().closeEvent(event)

    # ------------------------------------------------------------------ paint
    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.fillRect(self.rect(), DIM)
        off = self.geometry().topLeft()
        for it in self._items:
            kind = str(it.get("type") or "point")
            label = str(it.get("label") or "")
            p.setPen(QPen(ACCENT, 3))
            p.setBrush(Qt.BrushStyle.NoBrush)
            lx = ly = None
            if kind == "point":
                x = int(it["x"]) - off.x()
                y = int(it["y"]) - off.y()
                r = 15
                p.drawEllipse(QPoint(x, y), r, r)
                p.setPen(QPen(ACCENT, 2))
                p.drawLine(x - 26, y, x - 8, y)
                p.drawLine(x + 8, y, x + 26, y)
                p.drawLine(x, y - 26, x, y - 8)
                p.drawLine(x, y + 8, x, y + 26)
                p.setBrush(ACCENT)
                p.drawEllipse(QPoint(x, y), 3, 3)
                p.setBrush(Qt.BrushStyle.NoBrush)
                lx, ly = x + 18, y - 18
            elif kind == "rect":
                rect = QRect(
                    int(it["x"]) - off.x(), int(it["y"]) - off.y(),
                    int(it["w"]), int(it["h"]),
                )
                p.save()
                p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
                p.fillRect(rect, Qt.GlobalColor.transparent)
                p.restore()
                p.setPen(QPen(ACCENT, 3))
                p.drawRect(rect)
                lx, ly = rect.left(), rect.top() - 10
            elif kind == "circle":
                x = int(it["x"]) - off.x()
                y = int(it["y"]) - off.y()
                r = int(it.get("r") or 20)
                p.drawEllipse(QPoint(x, y), r, r)
                lx, ly = x - r, y - r - 12
            elif kind == "line":
                x1 = int(it["x1"]) - off.x(); y1 = int(it["y1"]) - off.y()
                x2 = int(it["x2"]) - off.x(); y2 = int(it["y2"]) - off.y()
                p.drawLine(x1, y1, x2, y2)
                p.setBrush(ACCENT)
                p.drawEllipse(QPoint(x1, y1), 5, 5)
                p.drawEllipse(QPoint(x2, y2), 5, 5)
                p.setBrush(Qt.BrushStyle.NoBrush)
                lx, ly = min(x1, x2), min(y1, y2) - 12
            if label and lx is not None:
                global_point = QPoint(lx, ly) + off
                screen = QApplication.screenAt(global_point) or QApplication.primaryScreen()
                bounds = screen.geometry().translated(-off) if screen else self.rect()
                draw_label(p, bounds, QPoint(lx + 12, ly + 12), label)

        if self._hint:
            screen = QApplication.screenAt(QCursor.pos()) or QApplication.primaryScreen()
            bounds = screen.geometry().translated(-off) if screen else self.rect()
            draw_banner(p, bounds, self._hint)
        p.end()
