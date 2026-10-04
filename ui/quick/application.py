"""Own the native Qt Quick presentation and its real application controller."""
from pathlib import Path
from PySide6.QtCore import QObject, QUrl, QCoreApplication, QEvent, Qt
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtGui import QGuiApplication

from application.controller import ApplicationController
from ui.services.painter_service import PainterService
from ui.helpers.app_setup import _apply_app_font
from .images import ImageProvider
from .icons import IconProvider
from .presenter import Presenter
from .graphics import configure_graphics


class QuickApplication(QObject):
    def __init__(self, *, config_store=None, initialize_runtime=False, desktop=True, parent=None):
        super().__init__(parent)
        configure_graphics()
        _apply_app_font(QGuiApplication.instance())
        self.service = PainterService(parent=self)
        self.controller = ApplicationController(self.service, config_store=config_store, parent=self)
        self.images = ImageProvider()
        self.icons = IconProvider()
        self.presenter = Presenter(self.controller, self.images, parent=self)
        self.engine = QQmlApplicationEngine(self)
        self._disposed = False
        self.qml_warnings = []
        self.engine.warnings.connect(
            lambda items: self.qml_warnings.extend(str(item) for item in items))
        self.engine.addImageProvider("painter", self.images)
        self.engine.addImageProvider("icons", self.icons)
        self.engine.setInitialProperties({"backend": self.presenter})
        try:
            if desktop:
                self.controller.enable_desktop()
                self.controller.desktop.brush_learning_handler = self.presenter.open_brush_setup
            self.controller.restore()
            self.presenter.refresh()
            self.engine.load(QUrl.fromLocalFile(str(Path(__file__).with_name("qml") / "Main.qml")))
            roots = self.engine.rootObjects()
            if not roots:
                raise RuntimeError("Не удалось загрузить интерфейс QML.\n" + "\n".join(self.qml_warnings))
            self.window = roots[0]
            self.presenter.attach_window(self.window)
            self.presenter.languageApplied.connect(self.engine.retranslate)
            self.controller.closed.connect(self.window.close, Qt.QueuedConnection)
            if initialize_runtime:
                self.service.initialize_runtime()
                self.service.register_global_hotkeys()
        except Exception:
            self.controller.close(save=False)
            raise

    def close(self, *, save=True):
        self.controller.close(save=save)
        self.presenter._refresh_timer.stop()

    def dispose(self, *, save=True):
        """Destroy the QML scene before its Python-backed properties disappear.

        Call on the GUI thread after the window/event loop has closed. Keeping
        teardown explicit avoids null-backend bindings during interpreter exit.
        """
        if self._disposed:
            return
        self.close(save=save)
        self.window.hide()
        self.engine.deleteLater()
        QCoreApplication.sendPostedEvents(self.engine, QEvent.DeferredDelete)
        self._disposed = True
