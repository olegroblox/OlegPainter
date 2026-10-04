"""Inspect the real Windows frame without showing a window or sending input."""
import json
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "windows"
os.environ["QT_QUICK_BACKEND"] = "software"
state = TemporaryDirectory(prefix="olegpainter-frame-")
os.environ["OLEGPAINTER_CONFIG_DIR"] = str(Path(state.name) / "configs")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import win32con
import win32gui
from PySide6.QtCore import QSettings
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtWidgets import QApplication
from PySide6.QtQuickControls2 import QQuickStyle
from ui.quick import application
from ui.helpers.app_setup import _configure_high_dpi
from PySide6.QtGui import QFontInfo


class HiddenEngine(QQmlApplicationEngine):
    def setInitialProperties(self, properties):
        # Initial values override QML's visible:true before creation; no flash.
        super().setInitialProperties({**properties, "visible": False})


QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, state.name)
_configure_high_dpi()
app = QApplication([])
QQuickStyle.setStyle("Basic")
with patch.object(application, "QQmlApplicationEngine", HiddenEngine):
    quick = application.QuickApplication(desktop=False, initialize_runtime=False)
report = []
try:
    assert quick.presenter.alwaysOnTop, "New sessions must default to pinned"
    window = quick.window
    hwnd = int(window.winId())
    expected = (win32con.WS_CAPTION | win32con.WS_SYSMENU | win32con.WS_MINIMIZEBOX
                | win32con.WS_MAXIMIZEBOX | win32con.WS_THICKFRAME)
    for pinned in (True, False, True):
        quick.presenter.setAlwaysOnTop(pinned)
        app.processEvents()
        hwnd = int(window.winId())
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        extended = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        assert style & expected == expected, hex(style)
        assert not win32gui.IsWindowVisible(hwnd)
        assert win32gui.GetForegroundWindow() != hwnd
        assert bool(extended & win32con.WS_EX_TOPMOST) == pinned, hex(extended)
        window.setPosition(140, 160)
        app.processEvents()
        left, top, right, bottom = win32gui.GetWindowRect(hwnd)
        assert right > left and bottom > top
        menu = win32gui.GetSystemMenu(hwnd, False)
        assert win32gui.GetMenuState(menu, win32con.SC_CLOSE, win32con.MF_BYCOMMAND) != -1
        report.append(dict(pinned=pinned, style=hex(style), extended_style=hex(extended),
                           visible=False, frame_rect=[left, top, right, bottom]))
    assert not quick.qml_warnings, quick.qml_warnings[:3]
    typography = dict(family=app.font().family(), resolved_family=QFontInfo(app.font()).family(),
                      logical_pixels=app.font().pixelSize(), device_pixel_ratio=window.devicePixelRatio())
finally:
    quick.dispose(save=False)
output = Path(sys.argv[1] if len(sys.argv) > 1 else "test-results/quick-frame.json")
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(dict(checks=report, typography=typography, qml_warnings=quick.qml_warnings), indent=2), encoding="utf-8")
print(f"Windows frame checks passed: {output}")
