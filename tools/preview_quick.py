"""Render the real Qt Quick pages with isolated data and no system input."""
import json
import os
from pathlib import Path
import sys
import time
from tempfile import TemporaryDirectory

os.environ["QT_QPA_PLATFORM"] = "offscreen"
os.environ["QT_QUICK_BACKEND"] = "software"
target = Path(sys.argv[1] if len(sys.argv) > 1 else "test-results/quick-preview").resolve()
target.mkdir(parents=True, exist_ok=True)
test_state = TemporaryDirectory(prefix="olegpainter-quick-preview-")
os.environ["OLEGPAINTER_CONFIG_DIR"] = str(Path(test_state.name) / "configs")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings, QUrl, QObject
from PySide6.QtGui import QImage, QPainter, QColor, QFontDatabase
from PySide6.QtWidgets import QApplication
from PySide6.QtQuickControls2 import QQuickStyle
from ui.quick.application import QuickApplication
from ui.helpers.app_setup import _apply_app_font

QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, test_state.name)
QQuickStyle.setStyle("Basic")
app = QApplication([])
# The Windows offscreen plugin does not enumerate installed system fonts.
for name in ("segoeui.ttf", "segoeuib.ttf"):
    font = Path(os.environ["WINDIR"]) / "Fonts" / name
    if QFontDatabase.addApplicationFont(str(font)) < 0:
        raise RuntimeError(f"Cannot load preview font: {font}")
# The caption buttons draw Windows icon glyphs (Theme.iconFont): without the icon
# font they render as empty boxes. Windows 10 has only Segoe MDL2 Assets.
for name in ("SegoeIcons.ttf", "segmdl2.ttf"):
    font = Path(os.environ["WINDIR"]) / "Fonts" / name
    if font.is_file() and QFontDatabase.addApplicationFont(str(font)) < 0:
        raise RuntimeError(f"Cannot load preview font: {font}")
# Match fonts installed on this machine: offscreen cannot enumerate the user's
# font registry, whereas the normal Windows window (and browser) can.
user_fonts = Path(os.environ["LOCALAPPDATA"]) / "Microsoft" / "Windows" / "Fonts"
for name in ("Inter-Regular.otf", "Inter-Medium.otf", "Inter-SemiBold.otf", "Inter-Bold.otf"):
    font = user_fonts / name
    if font.is_file() and QFontDatabase.addApplicationFont(str(font)) < 0:
        raise RuntimeError(f"Cannot load preview font: {font}")
_apply_app_font(app)
quick = QuickApplication(desktop=False)
warnings = quick.qml_warnings


def settle():
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.005)


try:
    source = QImage(700, 440, QImage.Format_RGB32)
    source.fill(QColor("#2f647b"))
    painter = QPainter(source)
    painter.setPen(QColor("#aec6b2"))
    painter.setBrush(QColor("#aec6b2"))
    painter.drawEllipse(85, 70, 300, 300)
    painter.fillRect(340, 220, 320, 165, QColor("#daab76"))
    painter.end()
    source_path = target / "source.png"
    source.save(str(source_path))
    quick.presenter.openSource(QUrl.fromLocalFile(str(source_path)))
    quick.window.setProperty("showOriginal", True)
    quick.presenter.preset("save", "Тестовый профиль")
    quick.service.engine.manual_palette_coords = [
        dict(x=1039, y=321, rgb=[0, 0, 0], hex="#000000"),
        dict(x=1039, y=351, rgb=[255, 255, 255], hex="#FFFFFF"),
        dict(x=-200, y=300, rgb=[50, 106, 178], hex="#326AB2"),
    ]
    quick.service.engine._invalidate_manual_palette_cache()
    quick.service._on_engine_manual_palette_changed()
    quick.service.engine.target_app_layer_coords = [(-120, 240), (-120, 280), (-120, 320)]
    quick.service.engine.extra_actions_state["pre"] = dict(enabled=True, events=[
        dict(device="mouse", action="click", button="left", x=1400, y=260, delay=.15)])
    quick.presenter.refresh()
    # Park the offscreen cursor over plain text once the window is shown: otherwise the
    # header menu button shows hover and a tooltip in every render.
    from PySide6.QtCore import QPoint
    from PySide6.QtTest import QTest
    settle()
    QTest.mouseMove(quick.window, QPoint(700, 90))
    settle()
    for page, name in enumerate(("drawing", "settings", "profiles", "hotkeys", "palette", "brush", "sequences")):
        quick.window.setProperty("page", page)
        quick.window.update()
        settle()
        frame = quick.window.grabWindow()
        assert not frame.isNull(), "QML rendering returned no image"
        assert frame.save(str(target / f"{name}.png"))
    quick.window.setProperty("page", 4)
    editor = quick.window.findChild(QObject, "paletteEditor")
    quick.presenter.setChoice("color_picking_method", "manual_palette")
    deadline = time.monotonic() + 10
    while quick.service._preview_thread is not None and time.monotonic() < deadline:
        settle()
    assert quick.presenter.setManualMix(dict(enabled=True, canvas_hex="#F5E9D5"))
    for width, height in ((960, 640), (1280, 860)):
        quick.window.resize(width, height)
        settle()
        assert quick.window.grabWindow().save(str(target / f"mixing-{width}x{height}.png"))
        flickable = quick.window.findChild(QObject, "pagesScroll").property("contentItem")
        flickable.setProperty("contentY", max(0, flickable.property("contentHeight") - flickable.height()))
        settle()
        assert quick.window.grabWindow().save(str(target / f"mixing-bottom-{width}x{height}.png"))
        flickable.setProperty("contentY", 0)
    quick.window.resize(1280, 860)
    editor.loadEntry(1)
    settle()
    assert quick.window.grabWindow().save(str(target / "palette-edit.png"))
    quick.window.setProperty("page", 1)
    selector = quick.window.findChild(QObject, "settingsGroup")
    for index, group in enumerate(quick.presenter.settingGroups):
        selector.setProperty("currentIndex", index)
        settle()
        assert quick.window.grabWindow().save(str(target / f"settings-{group['id']}.png"))
    quick.window.setProperty("page", 0)
    for width, height in ((960, 640), (1280, 860)):
        quick.window.resize(width, height)
        settle()
        assert quick.window.grabWindow().save(str(target / f"drawing-{width}x{height}.png"))
    quick.presenter.setChoice("color_picking_method", "hex_field")
    quick.presenter.setNumber("k_clusters", 3)
    quick.service.apply_viewport_state(draw_region_desktop_px=(0, 0, 96, 64))
    quick.presenter.setBackground(dict(enabled=True, mode="corner", color_tolerance=25))
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not quick.presenter.view["preview_ready"]:
        settle()
    assert quick.presenter.view["preview_ready"], "Background result not prepared"
    editor = quick.window.findChild(QObject, "backgroundEditor")
    editor.open()
    for width, height in ((960, 640), (1280, 860)):
        quick.window.resize(width, height)
        settle()
        assert quick.window.grabWindow().save(str(target / f"background-{width}x{height}.png"))
    editor.close()
    imported = [name for name in sys.modules if name == "ui.main_window" or name.startswith("ui.web.")]
    quick.window.setProperty("page", 5)
    for width, height in ((960, 640), (1280, 860)):
        quick.window.resize(width, height)
        settle()
        assert quick.window.grabWindow().save(str(target / f"brush-{width}x{height}.png"))
    quick.window.setProperty("page", 6)
    editor = quick.window.findChild(QObject, "sequencesEditor")
    for selected in range(3):
        editor.setProperty("selected", selected)
        for width, height in ((960, 640), (1280, 860)):
            quick.window.resize(width, height)
            settle()
            assert quick.window.grabWindow().save(str(target / f"sequences-{selected}-{width}x{height}.png"))
    quick.window.setProperty("page", 7)
    for target_id in ("speed_draw", "other"):
        assert quick.presenter.chooseTarget(target_id)
        for width, height in ((960, 640), (1280, 860)):
            quick.window.resize(width, height)
            settle()
            assert quick.window.grabWindow().save(str(target / f"quickstart-{target_id}-{width}x{height}.png"))
    quick.window.setProperty("page", 8)
    quick.window.resize(1280, 860)
    settle()
    assert quick.window.grabWindow().save(str(target / "ai-off.png"))
    assert quick.presenter.aiSet("enabled", True)
    for width, height in ((960, 640), (1280, 860)):
        quick.window.resize(width, height)
        settle()
        assert quick.window.grabWindow().save(str(target / f"ai-{width}x{height}.png"))
    scroll = quick.window.findChild(QObject, "pagesScroll")
    scroll.property("contentItem").setProperty("contentY", 700)
    settle()
    assert quick.window.grabWindow().save(str(target / "ai-models.png"))
    quick.presenter.setDarkTheme(False)
    for page, name in ((7, "quickstart"), (0, "drawing"), (1, "settings"), (5, "brush"), (8, "ai"), (9, "help")):
        quick.window.setProperty("page", page)
        quick.window.resize(1280, 860)
        settle()
        assert quick.window.grabWindow().save(str(target / f"light-{name}.png"))
    quick.presenter.setDarkTheme(True)
    assert quick.presenter.setLanguage("en")
    for page, name in ((7, "quickstart"), (0, "drawing"), (1, "settings"), (5, "brush"), (8, "ai"), (9, "help")):
        quick.window.setProperty("page", page)
        settle()
        assert quick.window.grabWindow().save(str(target / f"en-{name}.png"))
    assert quick.presenter.setLanguage("ru")
    quick.presenter.setSidebarCollapsed(True)
    quick.window.setProperty("page", 0)
    quick.window.resize(960, 640)
    settle()
    assert quick.window.grabWindow().save(str(target / "drawing-collapsed.png"))
    quick.presenter.setSidebarCollapsed(False)
    for width, height in ((360, 360), (480, 640), (640, 480)):
        quick.window.resize(width, height)
        for page in range(10):
            quick.window.setProperty("page", page)
            settle()
            assert quick.window.grabWindow().save(str(target / f"compact-{page}-{width}x{height}.png"))
        quick.window.setProperty("page", 0)
        drawer = quick.window.findChild(QObject, "navigationDrawer")
        drawer.open()
        settle()
        assert quick.window.grabWindow().save(str(target / f"menu-{width}x{height}.png"))
        drawer.close()
    report = {"qml_warnings": warnings, "legacy_imports": imported, "pages": 10,
              "settings_groups": len(quick.presenter.settingGroups), "font": app.font().family(),
              "device_pixel_ratio": quick.window.devicePixelRatio()}
    (target / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    assert not warnings and not imported, report
    print(json.dumps(report))
finally:
    quick.dispose(save=False)
    test_state.cleanup()
