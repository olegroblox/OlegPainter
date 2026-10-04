"""Track native window recreation without forcing a hidden widget to appear."""
from PySide6.QtCore import QObject, QEvent
from PySide6.QtGui import QGuiApplication

from infrastructure.screen_capture import screen_capture


class CaptureRegistration(QObject):
    def __init__(self, window, registry):
        super().__init__(window)
        self.registry = registry
        self.key = object()
        window.installEventFilter(self)
        key = self.key
        window.destroyed.connect(lambda: registry.unregister(key))

    def eventFilter(self, window, event):
        if event.type() in (QEvent.Show, QEvent.WinIdChange):
            if window.isVisible():
                # effectiveWinId doesn't force creation (unlike winId).
                handle = window.effectiveWinId()
                if handle:
                    self.registry.register(self.key, int(handle))
                else:
                    self.registry.unregister(self.key)
            else:
                self.registry.unregister(self.key)
        elif event.type() == QEvent.Hide:
            self.registry.unregister(self.key)
        return False


def register_screen_tool(window, registry=screen_capture):
    if QGuiApplication.platformName() in ("offscreen", "minimal"):
        return  # no native desktop window exists on these test platforms
    registration = CaptureRegistration(window, registry)
    window._capture_registration = registration
    if window.isVisible() and window.effectiveWinId():
        registry.register(registration.key, int(window.effectiveWinId()))
