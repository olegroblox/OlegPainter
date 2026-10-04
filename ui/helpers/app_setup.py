"""Qt process setup for the QML window (main.py / quick_main.py).

Importing this module installs no hooks.
"""
import logging
import os
import sys
from pathlib import Path
from PySide6 import QtCore
from PySide6.QtCore import Qt, qInstallMessageHandler
from PySide6.QtGui import QGuiApplication, QIcon, QFont
from PySide6.QtWidgets import QApplication, QWidget
from ui.helpers.app_logging import configure_logging, get_logger
from ui.helpers.icon_tinter import resolve_icon_path

log = get_logger("olegpainter.app_setup")


def configure_application_logging():
    configure_logging()
    _ensure_utf8_console()
    qInstallMessageHandler(_qt_msg_filter)
    _log_uncaught_exceptions()


def _log_uncaught_exceptions() -> None:
    """The windowed EXE has no console: an error in a slot must reach the session
    log that users send to support (STARTUP-001)."""
    previous = sys.excepthook

    def hook(kind, value, traceback):
        if not issubclass(kind, KeyboardInterrupt):
            log.error("Uncaught exception", exc_info=(kind, value, traceback))
        if previous is not None:
            previous(kind, value, traceback)

    sys.excepthook = hook


def _ensure_utf8_console() -> None:
    """Force UTF-8 IO on Windows so Russian logs render correctly."""
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")

    if os.name == "nt":
        try:
            import ctypes

            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            kernel32.SetConsoleOutputCP(65001)
            kernel32.SetConsoleCP(65001)
        except Exception:
            log.debug("Failed to switch Windows console to UTF-8", exc_info=True)

    for handle in ("stdout", "stderr"):
        stream = getattr(sys, handle, None)
        if not stream or not hasattr(stream, "encoding"):
            continue
        try:
            encoding = stream.encoding or ""
            if encoding.lower() != "utf-8" and hasattr(stream, "reconfigure"):
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            log.debug("Failed to reconfigure %s to UTF-8", handle, exc_info=True)


def _configure_high_dpi() -> None:
    """Ensure the app stays per-monitor DPI aware with crisp scaling."""
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_PER_MONITOR_DPI_AWARE = 2
            DPI_AWARENESS_CONTEXT_PER_MONITOR_V2 = ctypes.c_void_p(-4)  # type: ignore[assignment]

            try:
                ctypes.windll.shcore.SetProcessDpiAwareness(PROCESS_PER_MONITOR_DPI_AWARE)
            except Exception:
                try:
                    ctypes.windll.user32.SetProcessDpiAwarenessContext(DPI_AWARENESS_CONTEXT_PER_MONITOR_V2)
                except Exception:
                    log.debug("Could not set per-monitor DPI awareness", exc_info=True)
    except Exception:
        log.warning("DPI configuration failed", exc_info=True)

    try:
        qt_major = int(str(QtCore.qVersion()).split(".", 1)[0])
    except Exception:
        qt_major = 6

    if qt_major < 6:
        try:
            QApplication.setAttribute(Qt.AA_EnableHighDpiScaling, True)
        except Exception:
            log.debug("Failed to enable AA_EnableHighDpiScaling", exc_info=True)
        try:
            QApplication.setAttribute(Qt.AA_UseHighDpiPixmaps, True)
        except Exception:
            log.debug("Failed to enable AA_UseHighDpiPixmaps", exc_info=True)

    policy = getattr(Qt, "HighDpiScaleFactorRoundingPolicy", None)
    if policy is not None:
        try:
            QGuiApplication.setHighDpiScaleFactorRoundingPolicy(policy.PassThrough)
        except Exception:
            try:
                QGuiApplication.setHighDpiScaleFactorRoundingPolicy(policy.RoundPreferFloor)
            except Exception:
                log.debug("Failed to set HighDpiScaleFactorRoundingPolicy", exc_info=True)


def _qt_msg_filter(mode, context, message):
    # Silence noisy warnings that are harmless
    try:
        serious = mode in (QtCore.QtMsgType.QtCriticalMsg, QtCore.QtMsgType.QtFatalMsg)
        if isinstance(message, str) and not serious:
            if message.startswith("QPainter::"):
                return
            if "SetProcessDpiAwarenessContext() failed" in message:
                return
            if "Qt's default DPI awareness context" in message:
                return
        level = {
            QtCore.QtMsgType.QtDebugMsg: logging.DEBUG,
            QtCore.QtMsgType.QtInfoMsg: logging.INFO,
            QtCore.QtMsgType.QtWarningMsg: logging.WARNING,
            QtCore.QtMsgType.QtCriticalMsg: logging.ERROR,
            QtCore.QtMsgType.QtFatalMsg: logging.CRITICAL,
        }.get(mode, logging.WARNING)
        # FileHandler flushes every record, including the last message before
        # Qt aborts. Printing alone lost that message from the session log.
        logging.getLogger("olegpainter.qt").log(level, "%s", message)
    except Exception:
        log.debug("Qt message filter failed for message=%r", message, exc_info=True)


def _apply_app_font(app: QApplication) -> None:
    """Force a consistent UI font across the whole Qt application.

    Prefer the Windows system face (Segoe UI Variable Text) so text matches other apps.
    Pixel sizes are logical Qt pixels and scale with the screen's DPI. Native
    Quick text rendering supplies hinting; no claim that one font is universally
    sharper than another.
    """
    try:
        from PySide6.QtGui import QFontDatabase

        families = set(QFontDatabase.families())
        # The Windows system face first: at 125% scaling Inter showed uneven letter
        # spacing next to every other app on the owner's PC (2026-09-29).
        candidates = (
            "Segoe UI Variable Text",
            "Segoe UI",
            "Inter",
            "Arial",
        )
        picked = next((c for c in candidates if c in families), "Segoe UI")

        font = QFont(picked)
        font.setPixelSize(14)
        font.setStyleStrategy(
            QFont.PreferAntialias | QFont.PreferQuality
        )
        # Respect the platform's text metrics instead of forcing strong grid
        # snapping, which can make small Inter text look heavier than the web UI.
        font.setHintingPreference(QFont.PreferDefaultHinting)
        app.setFont(font)
    except Exception:
        log.warning("Failed to apply app font", exc_info=True)


def _apply_app_icon(app: QApplication, window: "QWidget | None" = None) -> None:
    """Set the bundled icon for title bars and taskbar."""
    try:
        icon_path = resolve_icon_path("ui/OlegPainter Pro v1.3.ico")
    except Exception:
        log.warning("Failed to resolve app icon path", exc_info=True)
        icon_path = None
    if not icon_path:
        return

    icon = QIcon(str(icon_path))
    if icon.isNull():
        log.warning("App icon resolved but QIcon is null: %s", icon_path)
        return

    try:
        app.setWindowIcon(icon)
    except Exception:
        log.warning("Failed to set application icon", exc_info=True)
    if window is not None:
        try:
            window.setWindowIcon(icon)
        except Exception:
            log.warning("Failed to set window icon", exc_info=True)
