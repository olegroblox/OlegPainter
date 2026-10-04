"""Responsive native shell, real QML interaction with no global input."""
import pytest
from PySide6.QtCore import QObject, QPointF, QSettings, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from tests.test_quick_presentation import quick, pump, visual_item  # noqa: F401
from ui.quick.application import QuickApplication
from ui.helpers.config_store import ConfigStore


@pytest.fixture
def shell(quick):
    quick.presenter.setSidebarCollapsed(False)
    quick.presenter.setAlwaysOnTop(True)
    yield quick
    quick.presenter.setSidebarCollapsed(False)
    quick.presenter.setAlwaysOnTop(True)


def click(window, item):
    QTest.qWait(35)
    position = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    assert 0 <= position.x() < window.width() and 0 <= position.y() < window.height()
    QTest.mouseClick(window, Qt.LeftButton, pos=position)
    pump()


def test_sidebar_collapse_keeps_navigation_and_expands_workspace(shell):
    window, presenter = shell.window, shell.presenter
    sidebar = window.findChild(QQuickItem, "sidebar")
    surface = window.findChild(QQuickItem, "previewSurface")
    old_width = surface.width()
    click(window, window.findChild(QQuickItem, "sidebarToggle"))
    pump(lambda: sidebar.width() == 64)
    assert surface.width() > old_width + 100
    assert presenter.sidebarCollapsed
    click(window, visual_item(sidebar, "navigation_4"))
    assert window.property("page") == 4
    click(window, window.findChild(QQuickItem, "sidebarToggle"))
    pump(lambda: sidebar.width() == 216)


def test_app_icon_is_the_menu_control_and_the_title_shows_the_version(shell):
    window, presenter = shell.window, shell.presenter
    version = presenter.property("appVersion")
    assert window.title() == f"OlegPainter v{version}"
    assert window.findChild(QQuickItem, "appVersion").property("text") == f"v{version}"
    toggle = window.findChild(QQuickItem, "sidebarToggle")
    assert toggle.findChild(QQuickItem, "appIcon") is not None  # no separate burger button any more
    chevron = window.findChild(QQuickItem, "sidebarChevron")
    pump(lambda: chevron.property("rotation") == 90)  # expanded: the arrow points to collapse
    click(window, toggle)
    pump(lambda: presenter.sidebarCollapsed and chevron.property("rotation") == -90)
    click(window, toggle)
    pump(lambda: not presenter.sidebarCollapsed)


def test_buttons_size_to_their_captions_and_center_icon_only_controls(shell):
    window = shell.window
    QTest.qWait(60)
    for name in ("openButton", "backgroundButton", "desktopToolsButton", "stopButton"):
        button = window.findChild(QQuickItem, name)
        caption = button.findChild(QQuickItem, "buttonCaption")
        assert caption.width() > 0 and not caption.property("truncated"), name
    pin = window.findChild(QQuickItem, "pinWindow")
    contents = pin.property("contentItem")
    row = contents.childItems()[0]
    glyph = row.childItems()[0]
    center = glyph.mapToItem(pin, QPointF(glyph.width() / 2, glyph.height() / 2))
    assert abs(center.x() - pin.width() / 2) <= 1


def test_fractional_text_metrics_do_not_elide_fitting_button_captions(shell):
    from PySide6.QtGui import QFont
    # Native Inter at 125% produced e.g. 102.39 px for a 102 px allocation.
    # Fractional spacing also reproduces the layout rounding with fallback fonts.
    for name in ("openButton", "drawingCalibration", "desktopToolsButton", "nextStepButton"):
        button = shell.window.findChild(QQuickItem, name)
        font = button.property("font")
        font.setLetterSpacing(QFont.AbsoluteSpacing, 0.37)
        button.setProperty("font", font)
        caption = button.findChild(QQuickItem, "buttonCaption")
        # Layouts settle over several frames (the next-step caption may also change).
        pump(lambda: caption.width() >= caption.implicitWidth())
        assert caption.width() >= caption.implicitWidth(), name
        assert not caption.property("truncated"), name


def test_styled_choice_keyboard_selection_and_dialog_close(shell):
    window = shell.window
    shell.presenter.setChoice("color_picking_method", "hex_field")
    popup = window.findChild(QObject, "calibrationDialog")
    popup.open()
    pump(lambda: popup.property("opened"))
    combo = visual_item(popup.property("contentItem"), "calibrationMethod")
    combo.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Space)
    choices = combo.findChild(QObject, "choicePopup")
    pump(lambda: choices.property("opened"))
    QTest.keyClick(window, Qt.Key_Down)
    QTest.keyClick(window, Qt.Key_Return)
    pump(lambda: not choices.property("opened"))
    assert shell.presenter.view["settings"]["color_picking_method"] == "hsv_palette"
    close_button = visual_item(popup.findChild(QQuickItem, "dialogFooter"), "dialogButton")
    assert close_button is not None
    click(window, close_button)
    pump(lambda: not popup.property("opened"))


@pytest.mark.parametrize("size", [(360, 360), (480, 640), (640, 480)])
def test_small_window_drawer_and_fixed_stop_are_accessible(shell, size):
    window = shell.window
    window.resize(*size)
    QTest.qWait(60)
    assert (window.width(), window.height()) == size
    assert not window.findChild(QQuickItem, "sidebar").isVisible()
    for name in ("sidebarToggle", "pinWindow", "desktopToolsButton", "nextStepButton", "stopButton"):
        item = window.findChild(QQuickItem, name)
        start = item.mapToScene(QPointF())
        assert start.x() >= 0 and start.y() >= 0
        assert start.x() + item.width() <= size[0] + 1
        assert start.y() + item.height() <= size[1] + 1
    click(window, window.findChild(QQuickItem, "sidebarToggle"))
    drawer = window.findChild(QQuickItem, "navigationDrawer")
    # Popup is QObject rather than QQuickItem on some Qt builds.
    if drawer is None:
        drawer = window.findChild(QObject, "navigationDrawer")
    pump(lambda: drawer.property("opened"))
    click(window, window.findChild(QQuickItem, "sidebarToggle"))
    pump(lambda: not drawer.property("opened"))
    click(window, window.findChild(QQuickItem, "sidebarToggle"))
    pump(lambda: drawer.property("opened"))
    panel = drawer.property("contentItem")
    click(window, visual_item(panel, "navigation_4"))
    pump(lambda: window.property("page") == 4 and not drawer.property("opened"))


def test_pin_and_sidebar_preferences_survive_new_window_without_resurrecting_hidden_window(shell, tmp_path):
    window, presenter = shell.window, shell.presenter
    assert window.flags() & Qt.WindowStaysOnTopHint
    click(window, window.findChild(QQuickItem, "pinWindow"))
    assert not window.flags() & Qt.WindowStaysOnTopHint
    assert window.isVisible()
    presenter.setSidebarCollapsed(True)
    assert QSettings().value("quick/sidebarCollapsed", False, type=bool)
    other = QuickApplication(config_store=ConfigStore(tmp_path / "second"), desktop=False)
    try:
        assert other.presenter.sidebarCollapsed and not other.presenter.alwaysOnTop
        assert not other.window.flags() & Qt.WindowStaysOnTopHint
    finally:
        other.dispose(save=False)
    window.hide()
    presenter.setAlwaysOnTop(True)
    assert window.flags() & Qt.WindowStaysOnTopHint
    assert not window.isVisible()


def test_pin_keeps_standard_window_controls_and_geometry(shell):
    window = shell.window
    window.setPosition(140, 160)
    geometry = window.geometry()
    # The header is the title bar (custom caption, like Steam): frameless in Qt,
    # native frame behaviour restored by ui/quick/window_frame.py on Windows.
    decorations = Qt.WindowSystemMenuHint | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
    for name in ("minimizeWindow", "maximizeWindow", "closeWindow"):
        assert window.findChild(QQuickItem, name) is not None, name
    for enabled in (False, True, False, True):
        shell.presenter.setAlwaysOnTop(enabled)
        pump()
        assert window.flags() & decorations == decorations
        assert window.flags() & Qt.WindowType_Mask == Qt.Window
        assert window.flags() & Qt.FramelessWindowHint
        assert bool(window.flags() & Qt.WindowStaysOnTopHint) == enabled
        assert window.isVisible() and window.geometry() == geometry
    window.setPosition(180, 200)
    assert window.x() == 180 and window.y() == 200


def test_navigation_has_consistent_spacing_without_empty_group_gap(shell):
    sidebar = shell.window.findChild(QQuickItem, "sidebar")
    QTest.qWait(60)
    pages = (7, 0, 4, 5, 6, 8, 1, 2, 3)
    buttons = [visual_item(sidebar, "navigation_" + str(page)) for page in pages]
    for before, after in zip(buttons, buttons[1:]):
        assert abs(after.y() - before.y() - before.height() - 4) < 1


def test_shared_icon_assets_render_at_small_size_and_high_dpi():
    import re
    from pathlib import Path
    from PySide6.QtCore import QSize
    from ui.quick.icons import IconProvider

    icons = IconProvider()
    for path in icons.root.glob("*.svg"):
        for pixels in (20, 40):
            image = icons.requestImage(path.stem + "/ffd21e", None, QSize(pixels, pixels))
            assert image.size() == QSize(pixels, pixels), path.name
            visible = [image.pixelColor(x, y) for x in range(pixels) for y in range(pixels)
                       if image.pixelColor(x, y).alpha() > 128]
            assert visible, path.name
            assert all(abs(color.red() - 255) <= 1 and abs(color.green() - 210) <= 1
                       and abs(color.blue() - 30) <= 1 for color in visible), path.name
    qml = Path(__file__).resolve().parents[1] / "ui/quick/qml"
    names = set()
    for path in qml.glob("*.qml"):
        names.update(re.findall(r'(?:iconName|icon|name):\s*"([a-z-]+)"', path.read_text(encoding="utf-8")))
    for name in names:
        assert not icons.requestImage(name + "/f0f2f6", None, QSize(20, 20)).isNull(), name


def test_qml_uses_application_font_and_native_text_rasterization(shell):
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtQuick import QQuickWindow
    application_font = QGuiApplication.font()
    assert application_font.pixelSize() == 14
    assert shell.window.property("font").family() == application_font.family()
    caption = shell.window.findChild(QQuickItem, "openButton").findChild(QQuickItem, "buttonCaption")
    assert caption.property("font").family() == application_font.family()
    assert caption.property("font").pixelSize() == 13
    assert QQuickWindow.textRenderType() == QQuickWindow.NativeTextRendering


def test_widget_icons_have_native_high_dpi_variants(shell):
    from PySide6.QtCore import QSize
    from ui.helpers.icon_tinter import load_svg_icon
    from app_paths import get_app_paths
    icon = load_svg_icon(get_app_paths().assets_root / "icons/brush.svg", color="#ffffff", size=QSize(20, 20))
    for ratio in (1.0, 2.0, 3.0):
        pixmap = icon.pixmap(QSize(20, 20), ratio)
        assert not pixmap.isNull()
        assert pixmap.width() == int(20 * ratio)
        assert pixmap.devicePixelRatio() == ratio


@pytest.mark.parametrize("page", range(7))
def test_compact_pages_do_not_require_horizontal_scrolling(shell, page):
    window = shell.window
    window.resize(360, 360)
    window.setProperty("page", page)
    QTest.qWait(70)
    scroll = window.findChild(QQuickItem, "pagesScroll")
    flickable = scroll.property("contentItem")
    assert flickable.property("contentWidth") <= scroll.width() + 1
    # Named edit controls are never laid out beyond the right edge, even below the fold.
    names = {0: ["drawingPlace", "drawingColors", "drawingCalibration"],
             1: ["placeSelector", "qualityPresets", "advancedSettings"], 2: ["presetName"], 3: [],
             4: ["appendPaletteButton"], 5: ["brushCaptureControl", "brushApplyRange", "brushCaptureScratch"],
             6: ["sequenceRecord"]}[page]
    for name in names:
        item = window.findChild(QQuickItem, name)
        point = item.mapToScene(QPointF())
        assert point.x() >= 0 and point.x() + item.width() <= window.width() + 1, name


@pytest.mark.parametrize("popup_name,names", [
    ("calibrationDialog", ["calibrationMethod", "openManualPalette"]),
    ("backgroundEditor", ["closeBackground", "backgroundSource", "backgroundMode", "backgroundHex", "applyBackgroundHex"]),
    ("imageSearchDialog", ["imageSearchQuery", "imageSearchRun", "imageService_yandex", "imageLinkOpen"]),
])
def test_compact_editors_keep_controls_within_window(shell, popup_name, names):
    window = shell.window
    window.resize(360, 360)
    shell.presenter.setChoice("color_picking_method", "manual_palette")
    popup = window.findChild(QObject, popup_name)
    popup.open()
    if popup_name == "backgroundEditor":
        popup.setProperty("picking", True)
    QTest.qWait(90)
    assert popup.property("height") <= window.height()
    for name in names:
        item = visual_item(popup.property("contentItem"), name)
        position = item.mapToScene(QPointF())
        assert position.x() >= 0 and position.x() + item.width() <= window.width() + 1, name
    if popup_name == "backgroundEditor":
        click(window, visual_item(popup.property("contentItem"), "closeBackground"))
    else:
        QTest.keyClick(window, Qt.Key_Escape)
    pump(lambda: not popup.property("opened"))


def test_desktop_tools_menu_fits_its_items_and_renders(shell, tmp_path):
    window = shell.window
    window.resize(1280, 860)
    click(window, window.findChild(QQuickItem, "desktopToolsButton"))
    QTest.qWait(250)
    rows = [item for item in window.findChildren(QQuickItem) if item.objectName() == "appMenuItem"]
    assert len(rows) == 4
    for row in rows:
        assert row.width() >= row.implicitWidth() - 1, row.property("text")
    import os
    if os.environ.get("OLEGPAINTER_MENU_SHOT"):
        window.grabWindow().save(os.environ["OLEGPAINTER_MENU_SHOT"])
