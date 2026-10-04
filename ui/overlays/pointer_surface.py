"""Shared lifecycle: poll a passive pointer, repaint only when it changes."""
from PySide6.QtCore import QPoint, Qt, QTimer
from PySide6.QtGui import QCursor
from PySide6.QtWidgets import QApplication

from ui.widgets.overlay_base import ClickThroughOverlay
from .coordinates import DesktopCoordinates


class PointerSurface(ClickThroughOverlay):
    def __init__(self, *, window_kind=Qt.Tool):
        super().__init__(None, window_kind=window_kind, translucent=True, passthrough=True)
        self.setAttribute(Qt.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WA_QuitOnClose, False)
        self.coordinates = DesktopCoordinates()
        self._cursor = QCursor.pos()
        self._anim = QTimer(self)
        self._anim.setInterval(16)
        self._anim.timeout.connect(self._tick)
        self._refresh_geometry()

    def _refresh_geometry(self):
        screens = QApplication.screens()
        if screens:
            rect = screens[0].geometry()
            for screen in screens[1:]:
                rect = rect.united(screen.geometry())
            self.setGeometry(rect)
        self.coordinates = DesktopCoordinates()

    def _tick(self):
        if not self.isVisible():
            return
        cursor = QCursor.pos()
        if cursor != self._cursor:
            self._cursor = QPoint(cursor)
            self.update()

    def active_screen_rect(self):
        screen = QApplication.screenAt(self._cursor) or QApplication.primaryScreen()
        return screen.geometry().translated(-self.geometry().topLeft()) if screen else self.rect()

    def showEvent(self, event):
        self._refresh_geometry()
        self._cursor = QCursor.pos()
        self._anim.start()
        super().showEvent(event)

    def hideEvent(self, event):
        self._anim.stop()
        super().hideEvent(event)

    def closeEvent(self, event):
        self._anim.stop()
        super().closeEvent(event)
