"""Shared floating settings cards for screen tools."""
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QPainter
from PySide6.QtWidgets import QWidget, QFrame, QVBoxLayout, QLayout, QGraphicsDropShadowEffect

from . import theme
from .theme import panel_styles
from .capture_registration import register_screen_tool


class GlassCard(QFrame):
    """A frame that paints the Card.qml surface itself (flat panel colour, hairline
    border, accent border while edited); stylesheets only style its children."""

    def __init__(self, parent=None, *, radius: float = 16):
        super().__init__(parent)
        self._radius = radius
        self._focused = False
        self._alpha = 246

    def set_focused(self, on: bool) -> None:
        if bool(on) != self._focused:
            self._focused = bool(on)
            self.update()

    def set_surface_alpha(self, alpha: int) -> None:
        alpha = max(0, min(255, int(alpha)))
        if alpha != self._alpha:
            self._alpha = alpha
            self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        theme.paint_card(painter, self.rect(), self._radius, alpha=self._alpha, focused=self._focused)
        painter.end()


class FloatingPopover(QWidget):
    def __init__(self, parent):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        register_screen_tool(self)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(14, 12, 14, 16)
        outer.setSizeConstraint(QLayout.SetFixedSize)
        self._card = GlassCard(self)
        self._card.setObjectName("ScreenPopover")
        self._shadow = QGraphicsDropShadowEffect(self._card)
        self._shadow.setBlurRadius(28)
        self._shadow.setOffset(0, 6)
        self._card.setGraphicsEffect(self._shadow)
        self._restyle()
        theme.watch(self._restyle)
        outer.addWidget(self._card)
        self.body = QVBoxLayout(self._card)
        self.body.setContentsMargins(18, 16, 18, 16)
        self.body.setSpacing(14)

    def _restyle(self, *_):
        self._card.setStyleSheet(panel_styles("#ScreenPopover", painted=True))
        self._shadow.setColor(theme.shadow_color())

    def open_below(self, anchor):
        self.adjustSize()
        point = anchor.mapToGlobal(QPoint(0, anchor.height() + 6))
        screen = anchor.screen()
        if screen is not None:
            bounds = screen.availableGeometry()
            if point.y() + self.height() > bounds.bottom():
                point.setY(anchor.mapToGlobal(QPoint()).y() - self.height() - 6)
            point.setX(max(bounds.left() + 8, min(point.x(), bounds.right() - self.width() - 8)))
            point.setY(max(bounds.top() + 8, min(point.y(), bounds.bottom() - self.height() - 8)))
        self.move(point)
        self.show()
        self.raise_()
