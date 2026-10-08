"""Native Qt Quick entry point during the verified presentation migration."""
import logging
import os
import sys
from pathlib import Path
from infrastructure.window_sampling import dispatch_worker
if __name__ == "__main__":
    dispatch_worker()

from PySide6.QtGui import QWindow
from PySide6.QtWidgets import QApplication, QMessageBox
from PySide6.QtQuickControls2 import QQuickStyle

from ui.helpers.app_setup import (
    configure_application_logging, _configure_high_dpi, _apply_app_font, _apply_app_icon,
)
from ui.helpers.app_logging import session_log_path

log = logging.getLogger("olegpainter.quick_main")


def _show_window(window):
    if window is None:
        return
    if window.visibility() == QWindow.Minimized or not window.isVisible():
        window.showNormal()
    window.raise_()
    window.requestActivate()


def _isolate_trial_settings():
    """OLEGPAINTER_CONFIG_DIR (dev.ps1 fresh) runs the program as on its first start:
    the window's own settings — quick start seen, chosen program, updates — go to an
    INI file in that folder instead of the user's registry, so nothing real changes."""
    folder = os.environ.get("OLEGPAINTER_CONFIG_DIR", "").strip()
    if not folder:
        return None
    from PySide6.QtCore import QSettings
    path = str(Path(folder) / "settings")
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, path)
    return path


def _startup_failed(error):
    """STARTUP-001: the windowed EXE has no console, so a failed start must be visible."""
    log.exception("Startup failed")
    path = session_log_path()
    details = f"\n\nПодробности в журнале:\n{path}" if path else ""
    QMessageBox.critical(None, "OlegPainter",
                         f"Программа не смогла запуститься.\n\n{error}{details}")


def main():
    configure_application_logging()
    if _isolate_trial_settings():
        log.info("Trial run: settings in %s", os.environ["OLEGPAINTER_CONFIG_DIR"])
    _configure_high_dpi()
    QQuickStyle.setStyle("Basic")
    # UPDATE-001: started by the installer of a new version — note it, clean up the download.
    from application import updates
    from app_paths import get_app_paths
    argv = updates.finish_on_start(sys.argv, get_app_paths().app_root)
    app = QApplication(argv)
    app.setOrganizationName("OlegPainter")
    app.setApplicationName("OlegPainter")
    _apply_app_font(app)
    _apply_app_icon(app)
    # A second copy would register a second set of global hotkeys and move the
    # mouse together with the first one: show the running window instead.
    from infrastructure.single_instance import SingleInstance
    instance = SingleInstance()
    if not instance.acquire():
        log.info("OlegPainter is already running: its window was asked to show itself")
        return 0
    from ui.quick.application import QuickApplication
    try:
        backend = QuickApplication(initialize_runtime=True)
    except Exception as error:
        _startup_failed(error)
        return 1
    instance.activationRequested.connect(lambda: _show_window(backend.window))
    app.aboutToQuit.connect(lambda: backend.close(save=False))
    try:
        return app.exec()
    finally:
        instance.release()
        backend.dispose(save=False)


if __name__ == "__main__":
    sys.exit(main())
