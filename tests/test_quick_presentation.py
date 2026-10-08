"""Real QML engine and application commands, isolated from system input/data."""
import re
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QMetaObject, QUrl, Qt, QPointF, QObject
from PySide6.QtGui import QImage, QColor
from PySide6.QtWidgets import QApplication
from PySide6.QtQuick import QQuickItem, QQuickWindow
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtTest import QTest

from ui.helpers.config_store import ConfigStore
from ui.quick.application import QuickApplication
from ui.quick.images import ImageProvider
from ui.helpers.hotkey_definitions import HOTKEY_DEFINITIONS
from application.settings import SETTINGS, GROUPS


def pump(predicate=lambda: True):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(0.005)
    assert predicate()


def visual_item(item, name):
    if item.objectName() == name:
        return item
    for child in item.childItems():
        found = visual_item(child, name)
        if found is not None:
            return found
    return None


def _open_quick(tmp_path, *, first_run=False):
    from PySide6.QtCore import QSettings
    QSettings().setValue("quick/quickStartSeen", not first_run)
    quick = QuickApplication(config_store=ConfigStore(tmp_path / "profiles"), desktop=False)
    # Most suites exercise the drawing page; only the first-run test keeps the quick start.
    quick.presenter.setQuickStartSeen(not first_run)
    if not first_run:
        quick.window.setProperty("page", 0)
    return quick


@pytest.fixture
def quick(tmp_path, request):
    app = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Basic")
    quick = _open_quick(tmp_path, first_run=getattr(request, "param", False))
    pump()
    yield quick
    from PySide6.QtCore import QSettings
    QSettings().setValue("quick/quickStartSeen", True)  # other suites expect the drawing page
    quick.dispose(save=False)
    assert not quick.qml_warnings
    quick.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def test_qml_loads_real_source_and_keeps_image_revision(quick, tmp_path):
    source = tmp_path / "source.png"
    image = QImage(48, 32, QImage.Format_RGB32)
    image.fill(QColor("#326ab2"))
    assert image.save(str(source))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(source)))
    pump(lambda: quick.presenter.view["image_loaded"])
    url = quick.presenter.sourceUrl
    assert url.startswith("image://painter/source/")
    quick.presenter.refresh()
    assert quick.presenter.sourceUrl == url
    quick.window.setProperty("showOriginal", True)
    pump()
    item = quick.window.findChild(QQuickItem, "previewImage")
    assert item is not None
    assert item.property("source").toString() == url


def test_qml_button_calls_presenter_and_respects_state(quick):
    received = []
    quick.presenter.openRequested.connect(lambda: received.append("open"))
    button = quick.window.findChild(QQuickItem, "openButton")
    assert button is not None and button.isEnabled()
    point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=point)
    pump(lambda: received)
    assert received == ["open"]


@pytest.mark.parametrize("slot", ["pre", "post"])
def test_presenter_can_finish_action_capture_and_exposes_draft_count(quick, monkeypatch, slot):
    from tests.test_capture_transactions import Listener
    from engine.olegpainter import core
    monkeypatch.setattr(core.mouse, "Listener", Listener)
    # Debouncing remains tested separately; this test exercises both commands.
    monkeypatch.setattr(quick.service, "_should_bounce_toggle", lambda *_: False)
    code = f"record_{slot}_color_actions"
    assert quick.presenter.action(code)
    engine = quick.service.engine
    engine._capture_ignore_until = 0
    engine.mouse_listener_extra_actions.native.callbacks["on_click"](1, 2, core.mouse.Button.left, False)
    pump(lambda: quick.controller.state.capture.count == 1)
    pump(lambda: quick.window.findChild(QQuickItem, "sequencesEditor").property("selected") == (1 if slot == "pre" else 2))
    assert engine._serialize_actions(slot) == []
    assert quick.presenter.action(code)
    pump(lambda: not quick.controller.state.capture.active)
    assert engine._serialize_actions(slot)[0]["x"] == 1


@pytest.mark.parametrize("key,selected", [("layers", 0), ("pre", 1), ("post", 2)])
def test_qml_sequences_record_finish_cancel_and_restore_saved_data(quick, monkeypatch, key, selected):
    from tests.test_capture_transactions import Listener
    from engine.olegpainter import core
    monkeypatch.setattr(core.mouse, "Listener", Listener)
    engine, presenter = quick.service.engine, quick.presenter
    monkeypatch.setattr(engine, "_capture_point", lambda fallback: fallback)
    quick.window.setProperty("page", 6)
    editor = quick.window.findChild(QQuickItem, "sequencesEditor")
    editor.setProperty("selected", selected)
    pump()
    def group():
        return presenter.view["input_sequences"][selected]
    def click_record():
        button = visual_item(quick.window.contentItem(), "sequenceRecord")
        assert button.isEnabled() and button.isVisible()
        # Text wrapping changes the card geometry; await Qt Quick's layout pass.
        QTest.qWait(50)
        QTest.mouseClick(quick.window, Qt.LeftButton,
                        pos=button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint())
    def record_point(x, y):
        engine.ignore_clicks_until = engine._capture_ignore_until = 0
        listener = engine.mouse_listener_app_layer if key == "layers" else engine.mouse_listener_extra_actions
        listener.native.callbacks["on_click"](x, y, core.mouse.Button.left, key == "layers")
    click_record()
    pump(lambda: group()["capturing"])
    record_point(-50, 120)
    pump(lambda: len(group()["entries"]) == 1)
    assert group()["saved_count"] == 0
    click_record()
    pump(lambda: not group()["capturing"] and group()["saved_count"] == 1)
    assert presenter.setSequenceEnabled(key, True, group()["revision"])
    config = quick.service.snapshot_painter_config()
    saved_revision = group()["revision"]
    assert presenter.sequenceCommand(key, "start", saved_revision)
    record_point(700, 800)
    pump(lambda: group()["entries"][0]["x"] == "700")
    assert group()["saved_count"] == 1
    assert not presenter.sequenceCommand(key, "clear", saved_revision)
    assert presenter.sequenceCommand(key, "cancel", saved_revision)
    pump(lambda: not quick.service._stop_pending and not group()["capturing"])
    assert group()["entries"][0]["x"] == "-50"
    assert group()["revision"] == saved_revision
    assert presenter.sequenceCommand(key, "clear", saved_revision)
    assert not group()["entries"] and not group()["enabled"]
    engine.load_config(config)
    presenter.refresh()
    assert group()["entries"][0]["x"] == "-50" and group()["enabled"]
    assert group()["revision"] == saved_revision


def test_sequences_reject_stale_clear_invalid_kind_and_empty_enable(quick):
    presenter, engine = quick.presenter, quick.service.engine
    revision = presenter.view["input_sequences"][0]["revision"]
    assert not presenter.setSequenceEnabled("layers", True, revision)
    engine.target_app_layer_coords = [(1, 2)]
    assert not presenter.sequenceCommand("layers", "clear", revision)
    assert engine.target_app_layer_coords == [(1, 2)]
    assert not presenter.sequenceCommand("unknown", "start", revision)
    assert not presenter.sequenceCommand("layers", "finish", revision)


def test_guided_preparation_opens_the_missing_step_and_keeps_stop_visible(quick, tmp_path, monkeypatch):
    from tests.test_capture_transactions import Listener
    from engine.olegpainter import core
    monkeypatch.setattr(core.mouse, "Listener", Listener)
    presenter = quick.presenter
    assert presenter.selectProfile("universal", "dfs_4dir")
    assert presenter.view["next_step"]["action"] == "open_file"
    image = QImage(48, 32, QImage.Format_RGB32)
    image.fill(Qt.white)
    path = tmp_path / "guided.png"
    assert image.save(str(path))
    assert presenter.openSource(QUrl.fromLocalFile(str(path)))
    pump(lambda: presenter.view["next_step"]["action"] == "select_area")
    quick.service.apply_viewport_state(draw_region_desktop_px=(100, 100, 48, 32))
    pump(lambda: presenter.view["next_step"]["action"] == "open_palette")
    assert presenter.nextStep()
    pump(lambda: quick.window.property("page") == 4)
    assert presenter.action("define_app_layers")
    pump(lambda: presenter.view["capture"]["active"])
    quick.window.setProperty("page", 3)
    pump()
    controls = quick.window.findChild(QQuickItem, "drawingControls")
    assert controls.isVisible()
    assert presenter.view["next_step"]["action"] == "finish_capture"
    assert presenter.nextStep()
    pump(lambda: not presenter.view["capture"]["active"])


def test_screen_palette_readiness_uses_its_own_calibration_not_hex(quick):
    import numpy as np
    engine, service = quick.service.engine, quick.service
    engine.color_picking_method = "screen_palette"
    engine.hex_input_coord = (1, 2)
    engine.screen_palette_calib = None
    assert not service._color_tools_ready()[0]
    assert "calibrate_screen_palette" in service.get_preparation_snapshot()["missing_action_codes"]
    engine.screen_palette_calib = dict(lab=np.array([[50., 0., 0.]]), coords=np.array([[1, 2]]), slider=None)
    engine.hex_input_coord = None
    assert service._color_tools_ready()[0]
    assert "calibrate_hex_field" not in service.get_preparation_snapshot()["missing_action_codes"]


def test_main_screen_calibration_opens_palette_and_blocks_changes_during_recording(quick):
    presenter = quick.presenter
    assert presenter.selectProfile("universal", "dfs_4dir")
    button = quick.window.findChild(QQuickItem, "drawingCalibration")
    QTest.qWait(50)
    QTest.mouseClick(quick.window, Qt.LeftButton,
                    pos=button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint())
    dialog = quick.window.findChild(QObject, "calibrationDialog")
    pump(lambda: dialog.property("opened"))
    palette = visual_item(quick.window.contentItem(), "openManualPalette")
    assert palette.isVisible() and palette.isEnabled()
    QTest.mouseClick(quick.window, Qt.LeftButton,
                    pos=palette.mapToScene(QPointF(palette.width() / 2, palette.height() / 2)).toPoint())
    pump(lambda: quick.window.property("page") == 4 and not dialog.property("opened"))
    assert presenter.beginRecording("select_area")
    pump(lambda: not presenter.view["can_edit"])
    assert presenter.view["next_step"]["action"] == ""
    assert not presenter.nextStep()
    presenter.cancelRecording()


def test_sequences_keep_saved_layers_when_native_capture_cannot_start(quick, monkeypatch):
    from engine.olegpainter import core
    def fail(**_):
        raise OSError("hook unavailable")
    monkeypatch.setattr(core.mouse, "Listener", fail)
    quick.service.engine.target_app_layer_coords = [(10, 20)]
    quick.presenter.refresh()
    revision = quick.presenter.view["input_sequences"][0]["revision"]
    assert not quick.presenter.sequenceCommand("layers", "start", revision)
    assert "hook unavailable" in quick.presenter.message
    assert quick.service.engine.target_app_layer_coords == [(10, 20)]
    assert quick.service.engine._capture_session.finished.is_set()


def test_brush_editor_explains_why_old_profile_needs_learning(quick):
    from test_brush_profile_compatibility import learned_config
    config = learned_config()
    config["dynamic_brush_profile"]["cached_calibration"].pop("measurement_revision")
    quick.service.engine.load_config(config)
    quick.service._emit_dynamic_brush_settings_changed()
    quick.presenter.open_brush_setup()
    pump(lambda: quick.window.property("page") == 5)
    label = quick.window.findChild(QQuickItem, "brushLearningStatus")
    message = quick.service.get_dynamic_brush_settings()["profile_message"]
    pump(lambda: label.property("text") == message)
    assert message and quick.service._dynamic_brush_should_prompt_learning()
    assert quick.service.engine.dynamic_brush_scratch_zone == (400, 100, 500, 500)


def test_brush_editor_applies_atomic_range_and_reports_errors(quick):
    quick.presenter.open_brush_setup()
    pump(lambda: quick.window.property("page") == 5)
    # the size range is found automatically unless the user asks for manual input
    assert quick.presenter.setBrush({"text_auto": False}), quick.presenter.message
    quick.window.findChild(QQuickItem, "brushAdvanced").setProperty("checked", True)
    pump(lambda: quick.window.findChild(QQuickItem, "brushApplyRange").isVisible())
    for name, text in (("brushMinimum", "1"), ("brushMaximum", "15"), ("brushDefault", "2"), ("brushStep", "1")):
        item = quick.window.findChild(QQuickItem, name)
        assert item is not None
        item.setProperty("text", text)
    button = quick.window.findChild(QQuickItem, "brushApplyRange")
    QTest.qWait(80)  # let the expanded layout acquire its final content height
    scroll = quick.window.findChild(QQuickItem, "pagesScroll").property("contentItem")
    scroll.setProperty("contentY", max(0, scroll.property("contentHeight") - scroll.height()))
    QTest.qWait(80)
    point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
    assert 0 <= point.y() < quick.window.height()
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=point)
    pump(lambda: quick.service.engine.dynamic_brush_max_value == 15)
    assert quick.service.engine.dynamic_brush_default_value == 2
    assert not quick.presenter.setBrush({"min_value": 20})
    assert quick.presenter.messageError
    assert quick.service.engine.dynamic_brush_min_value == 1
    assert not quick.presenter.captureBrush("text", 0)
    assert "не подключены" in quick.presenter.message


def test_brush_editor_running_cancel_and_actual_worker_exit(quick, monkeypatch):
    from threading import Event
    from engine.olegpainter.core import OlegPainter
    entered, release = Event(), Event()

    def learn(engine, **kwargs):
        entered.set()
        assert release.wait(3)
        return None

    monkeypatch.setattr(OlegPainter, "_run_dynamic_brush_calibration", learn)
    monkeypatch.setattr(OlegPainter, "_mouse_up_with_settle", lambda *a, **k: True)
    engine = quick.service.engine
    engine.draw_region = (0, 0, 200, 200)
    engine.dynamic_brush_coord = (20, 20)
    engine.dynamic_brush_scratch_zone = (250, 0, 200, 200)
    quick.service._emit_dynamic_brush_settings_changed()
    quick.window.resize(1280, 1200)
    quick.presenter.open_brush_setup()
    quick.presenter.refresh()
    pump()
    try:
        button = quick.window.findChild(QQuickItem, "brushLearn")
        assert button.isEnabled()
        point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
        assert 0 <= point.y() < quick.window.height()
        QTest.mouseClick(quick.window, Qt.LeftButton, pos=point)
        # Learning clicks in the user's program: it says so and waits for a yes (owner, 2026-10-05).
        confirm = quick.window.findChild(QObject, "learnConfirm")
        pump(lambda: confirm.property("visible"))
        assert confirm.property("title") == "Обучить кисть?" and not confirm.property("speed")
        assert not quick.service.brush_learning_active and not entered.is_set()
        assert quick.presenter.view["brush"]["probe_colour_auto"] is False  # no colour method: «выберите тёмный цвет»
        QMetaObject.invokeMethod(confirm, "accept")
        pump(lambda: entered.is_set() and quick.presenter.view["brush_learning"]["active"])
        pump(lambda: not confirm.property("visible"))  # the closing dialog still catches clicks
        assert quick.window.visibility() == QQuickWindow.Minimized
        assert not button.isEnabled()
        assert not quick.presenter.view["can_edit"]
        assert not quick.presenter.action("capture_hex_palette")
        stop = quick.window.findChild(QQuickItem, "brushCancel")
        assert stop.isEnabled() and stop.isVisible()
        # The taskbar can reopen the window; F4 also remains globally available.
        quick.window.showNormal()
        point = stop.mapToScene(QPointF(stop.width() / 2, stop.height() / 2)).toPoint()
        QTest.mouseClick(quick.window, Qt.LeftButton, pos=point)
        pump(lambda: quick.presenter.view["brush_learning"]["cancelling"])
        assert quick.service.brush_learning_active
        release.set()
        pump(lambda: not quick.presenter.view["brush_learning"]["active"])
        assert engine.dynamic_brush_profile is None
        assert quick.presenter.view["can_edit"]
        assert "отменено" in quick.presenter.view["brush_learning"]["message"]
        assert quick.window.visibility() != QQuickWindow.Minimized
    finally:
        release.set()


def test_brush_learning_start_error_restores_window(quick, monkeypatch):
    def reject():
        assert quick.window.visibility() == QQuickWindow.Minimized
        raise ValueError("Нет свободной тестовой зоны")
    monkeypatch.setattr(quick.service, "brush_learning_blocker", lambda mode: "")
    monkeypatch.setattr(quick.service, "start_brush_learning", reject)
    assert not quick.presenter.brushCommand("learn")
    assert quick.window.isVisible()
    assert quick.window.visibility() != QQuickWindow.Minimized
    assert "тестовой зоны" in quick.presenter.message


def test_learning_without_its_setup_explains_and_keeps_the_window(quick, monkeypatch):
    """Minimised first, the window hid «…4 × 4 пикселей» about a canvas never
    selected (2026-10-05): now nothing moves and the reason is plain."""
    monkeypatch.setattr(quick.service, "start_brush_learning", lambda *a, **kw: pytest.fail("must not start"))
    for command in ("learn", "learn_speed"):
        assert not quick.presenter.brushCommand(command)
        assert quick.window.visibility() != QQuickWindow.Minimized
        assert "обведите на экране холст" in quick.presenter.message
    quick.service.apply_viewport_state(draw_region_desktop_px=(0, 0, 120, 80))
    pump(lambda: not quick.service.brush_learning_blocker("speed").startswith("Подождите"))
    assert "место на холсте для пробных мазков" in quick.service.brush_learning_blocker("speed")


def test_brush_capture_uses_shared_picker_and_restores_qml_window(quick, monkeypatch):
    from ui.overlays.region_pick import _BasePickOverlay
    monkeypatch.setattr(_BasePickOverlay, "_start_listeners", lambda self: None)
    desktop = quick.controller.enable_desktop()
    assert quick.presenter.captureBrush("text", 0)
    picker = desktop.desktop_overlays.picker
    assert not quick.window.isVisible() and quick.service.desktop_interaction == "pick"
    picker._handle_event("down", (-400, 80))
    assert quick.service.engine.dynamic_brush_coord is None
    picker._handle_event("up", (-400, 80))
    # The pick is first shown on screen as understood; the window waits for that
    # check, or it would cover the control the check points at (owner, 2026-10-05).
    pump(lambda: desktop.desktop_overlays.flash is not None)
    assert quick.service.engine.dynamic_brush_coord == (-400, 80)
    assert not quick.window.isVisible()
    desktop.desktop_overlays.flash.close()
    pump(lambda: quick.window.isVisible())
    assert quick.service.desktop_interaction == ""
    assert quick.presenter.captureBrush("scratch", 0)
    picker = desktop.desktop_overlays.picker
    picker._handle_event("down", (200, 300))
    picker.cancel()
    pump(lambda: quick.window.isVisible())
    assert quick.service.engine.dynamic_brush_scratch_zone is None
    assert quick.service.desktop_interaction == ""


def _past_edge(item, edge, found):
    """Visible items whose right side is past ``edge`` (scene x), with the overflow."""
    for child in item.childItems():
        if child.isVisible() and child.width() > 0 and child.height() > 0:
            right = child.mapToScene(QPointF(child.width(), 0)).x()
            if right > edge + 1:
                found.append((child.metaObject().className(), child.property("text"), round(right - edge)))
            _past_edge(child, edge, found)
    return found


def test_every_page_fits_the_narrowest_window(quick, monkeypatch):
    """Nothing reaches past a 360 px window. A layout row keeps the natural width of
    every item that does not fill: the AI model rows pushed the page 264 px past the
    edge, cutting off text and buttons (owner, 2026-10-05)."""
    real = quick.service.ai.snapshot

    def not_installed(language):  # the widest state: «Установить рекомендуемые» and «Установить» shown
        view = real(language)
        return dict(view, enabled=True, models=[dict(m, installed=False, installing=False) for m in view["models"]])

    monkeypatch.setattr(quick.service.ai, "snapshot", not_installed)
    assert quick.presenter.preset("save", "Профиль с длинным названием для проверки ширины окна")
    # the widest states of «Палитра» and «Слои и действия»: mixing on, a recorded click
    assert quick.presenter.setChoice("color_picking_method", "manual_palette")
    assert quick.presenter.setManualMix(dict(enabled=True, canvas_hex="#F5E9D5"))
    quick.service.engine.extra_actions_state["pre"] = dict(enabled=True, events=[
        dict(device="mouse", action="click", button="left", x=1400, y=260, delay=.15)])
    quick.presenter.refresh()
    quick.window.resize(360, 900)
    scroll = quick.window.findChild(QQuickItem, "pagesScroll")
    for page in range(10):
        quick.window.setProperty("page", page)
        quick.presenter.refresh()
        wide = []
        for _attempt in range(5):  # a page that just became visible lays out on the next polish
            pump()
            QTest.qWait(100)
            edge = scroll.mapToScene(QPointF(scroll.width(), 0)).x()
            wide = _past_edge(scroll.property("contentItem"), edge, [])
            if not wide:
                break
        assert not wide, (page, wide[:5])


def _squeezed_buttons(item, found):
    """Visible ActionButtons in wrapping rows narrower than their caption needs, and
    button rows beside text that still wrap (a normal window has room for one line)."""
    name = item.metaObject().className()
    shown = [child for child in item.childItems() if child.isVisible() and child.width() > 0]
    if name.startswith("ButtonRow") and len({round(child.y()) for child in shown}) > 1:
        found.append(("wrapped row", [child.property("text") for child in shown]))
    for child in item.childItems():
        if name.startswith(("QQuickFlow", "ButtonRow")) and child.isVisible() and child.metaObject().className().startswith("ActionButton"):
            if child.width() < child.implicitWidth() - 1:
                found.append((child.property("text"), child.width(), child.implicitWidth()))
        _squeezed_buttons(child, found)
    return found


def test_buttons_stay_whole_on_a_normal_window_after_a_narrow_one(quick, tmp_path, monkeypatch):
    """Buttons beside text keep their full width on a normal window, also after the
    window was narrow: the saved-profile buttons shrank to nothing (2026-10-05)."""
    real = quick.service.ai.snapshot
    monkeypatch.setattr(quick.service.ai, "snapshot", lambda language: dict(real(language), enabled=True))
    image = QImage(200, 140, QImage.Format_RGB32)
    image.fill(Qt.darkCyan)
    path = tmp_path / "picture.png"
    assert image.save(str(path))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(path)))
    assert quick.presenter.preset("save", "Тестовый профиль")
    scroll = quick.window.findChild(QQuickItem, "pagesScroll")
    viewer = quick.window.findChild(QObject, "imageViewer")
    for width, height in ((1280, 860), (360, 900), (1280, 860)):
        quick.window.resize(width, height)
        for page in range(10):
            quick.window.setProperty("page", page)
            quick.presenter.refresh()
            squeezed = []
            for _attempt in range(5):
                pump()
                QTest.qWait(60)
                squeezed = _squeezed_buttons(scroll.property("contentItem"), [])
                if not squeezed or width < 760:
                    break
            assert width < 760 or not squeezed, (page, squeezed[:5])
        QMetaObject.invokeMethod(viewer, "open")
        pump()
        QTest.qWait(150)
        squeezed = _squeezed_buttons(viewer.property("contentItem"), [])
        QMetaObject.invokeMethod(viewer, "close")
        pump()
        assert width < 760 or not squeezed, ("imageViewer", squeezed[:5])


def _cut_captions(item, found):
    """Button captions elided although the window is wide enough for them."""
    for child in item.childItems():
        if child.isVisible() and child.objectName() == "buttonCaption" and child.property("truncated"):
            found.append(child.property("text"))
        _cut_captions(child, found)
    return found


@pytest.mark.parametrize("size", [(960, 640), (1200, 800), (1280, 860)])
def test_no_button_caption_is_cut_on_a_normal_window(quick, tmp_path, monkeypatch, size):
    """With the driver banner shown, a label sharing a row by natural widths squeezed
    «Установить драйвер» / «Проверить снова» on a ~1200 px window (audit 2026-10-05)."""
    from infrastructure.input_driver import DriverStatus
    # The fullest banner: a damaged driver offers repair, removal and a new check.
    monkeypatch.setattr(quick.presenter._drivers, "status", DriverStatus("broken", parts=_driver_parts()))
    quick.presenter.driverChanged.emit()
    real = quick.service.ai.snapshot
    monkeypatch.setattr(quick.service.ai, "snapshot", lambda language: dict(real(language), enabled=True))
    image = QImage(200, 140, QImage.Format_RGB32)
    image.fill(Qt.darkCyan)
    path = tmp_path / "picture.png"
    assert image.save(str(path))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(path)))
    assert quick.presenter.preset("save", "Тестовый профиль")
    quick.window.resize(*size)
    for page in range(10):
        quick.window.setProperty("page", page)
        quick.presenter.refresh()
        cut = []
        for _attempt in range(5):
            pump()
            QTest.qWait(60)
            cut = _cut_captions(quick.window.contentItem(), [])
            if not cut:
                break
        assert not cut, (page, cut[:5])


def test_setting_hint_appears_under_what_is_hovered(quick):
    """A 420 px tip centred over a wide label and over its small ⓘ landed at each
    other's places: hovering ⓘ showed the text by the label and back (2026-10-05)."""
    window = quick.window
    window.resize(1280, 860)
    window.setProperty("page", 1)
    pump()
    for name in ("explain_k_clusters", "explain_brush_size"):
        button = visual_item(window.contentItem(), name)
        assert button is not None and button.isVisible(), name
        scroll = window.findChild(QQuickItem, "pagesScroll").property("contentItem")
        scroll.setProperty("contentY", max(0, button.mapToItem(scroll.property("contentItem"), 0, 0).y() - 200))
        pump()
        label = button.parentItem().childItems()[0]
        for owner, icon in ((button, True), (label, False)):
            tip = owner.findChildren(QObject, "settingTooltip")[0]
            tip.setProperty("visible", True)
            pump(lambda: tip.property("opened"))
            item = tip.property("contentItem")
            left = item.mapToScene(QPointF(0, 0))
            right = item.mapToScene(QPointF(item.width(), 0)).x()
            edge = button.mapToScene(QPointF(button.width(), button.height()))
            row_left = button.parentItem().mapToScene(QPointF(0, 0)).x()
            assert left.y() >= edge.y() - 2, (name, left, edge)
            if icon:                   # ⓘ: the tip ends at the icon, within its padding
                assert abs(right - edge.x()) < 30, (name, right, edge)
            else:                      # the label: the tip starts where the label does
                assert abs(left.x() - row_left) < 30, (name, left, row_left)
            tip.setProperty("visible", False)
            pump(lambda: not tip.property("visible"))


def test_every_dialog_fits_the_narrowest_window(quick, tmp_path):
    """Dialogs keep their text and buttons inside a 360 px window: the viewer toolbar
    needed ~700 px and confirmation texts ran past the edge (2026-10-05)."""
    image = QImage(200, 140, QImage.Format_RGB32)
    image.fill(Qt.darkCyan)
    path = tmp_path / "picture.png"
    assert image.save(str(path))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(path)))
    assert quick.presenter.preset("save", "Профиль с длинным названием для проверки ширины окна")
    quick.window.resize(360, 640)
    pump()
    names = ["backgroundEditor", "calibrationDialog", "imageSearchDialog", "imageViewer", "whatsNewDialog",
             "learnConfirm", "driverConsent", "driverRestart", "sequenceRemoveDialog", "resetHotkeysDialog", "overwritePresetDialog",
             "deletePresetDialog", "applyPresetDialog", "brushResetDialog", "clearPaletteDialog", "removeModelDialog",
             "shutdownPanel"]
    for name in names:
        dialog = quick.window.findChild(QObject, name)
        assert dialog is not None, name
        if name == "overwritePresetDialog":
            dialog.setProperty("name", "Профиль с длинным названием для проверки ширины окна")
        QMetaObject.invokeMethod(dialog, "open")
        wide = []
        for _attempt in range(5):
            pump()
            QTest.qWait(100)
            wide = _past_edge(dialog.property("contentItem"), 360, [])
            if not wide:
                break
        QMetaObject.invokeMethod(dialog, "close")
        pump()
        assert not wide, (name, wide[:5])


@pytest.mark.parametrize("size", [(960, 640), (1100, 750), (1280, 860)])
def test_drawing_preview_and_controls_fit_window(quick, tmp_path, size):
    image = QImage(80, 120, QImage.Format_RGB32)
    image.fill(Qt.red)
    path = tmp_path / "portrait.png"
    assert image.save(str(path))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(path)))
    quick.window.resize(*size)
    quick.window.setProperty("showOriginal", True)
    quick.presenter._set_message("Проверка длинного сообщения о подготовке изображения. " * 8)
    pump()
    QTest.qWait(100)
    surface = quick.window.findChild(QQuickItem, "previewSurface")
    preview = quick.window.findChild(QQuickItem, "previewImage")
    controls = quick.window.findChild(QQuickItem, "drawingControls")
    for item in (surface, controls):
        point = item.mapToScene(QPointF(0, 0))
        assert point.x() >= 0 and point.y() >= 0
        assert point.x() + item.width() <= quick.window.width()
        assert point.y() + item.height() <= quick.window.height()
    assert surface.mapToScene(QPointF(0, surface.height())).y() <= controls.mapToScene(QPointF()).y()
    assert preview.height() >= 40
    assert preview.property("paintedHeight") <= preview.height() + 0.1
    assert preview.property("paintedWidth") / preview.property("paintedHeight") == pytest.approx(80 / 120)


def test_background_dialog_pick_uses_source_and_preserves_letterbox(quick, tmp_path):
    source = QImage(80, 120, QImage.Format_RGBA8888)
    source.fill(QColor("#12AB34"))
    path = tmp_path / "portrait.png"
    assert source.save(str(path))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(path)))
    quick.window.resize(960, 640)
    button = quick.window.findChild(QQuickItem, "backgroundButton")
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=button.mapToScene(QPointF(button.width()/2, button.height()/2)).toPoint())
    popup = quick.window.findChild(QObject, "backgroundEditor")
    pump(lambda: popup.property("opened"))
    by_colour = visual_item(quick.window.contentItem(), "backgroundMethod_color")
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=by_colour.mapToScene(QPointF(by_colour.width()/2, by_colour.height()/2)).toPoint())
    pump(lambda: quick.presenter.view["background"]["method"] == "color")
    pick = visual_item(quick.window.contentItem(), "pickBackgroundButton")
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=pick.mapToScene(QPointF(pick.width()/2, pick.height()/2)).toPoint())
    pump(lambda: popup.property("picking"))
    surface = visual_item(quick.window.contentItem(), "backgroundSource")
    # This wide surface letterboxes a portrait. Clicking the margin is not a pick.
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=surface.mapToScene(QPointF(10, surface.height()/2)).toPoint())
    assert quick.service.engine.background_reference_rgb is None
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=surface.mapToScene(QPointF(surface.width()/2, surface.height()/2)).toPoint())
    pump(lambda: quick.service.engine.background_reference_rgb == (18, 171, 52))
    assert not popup.property("picking")
    assert quick.service.engine.background_removal_mode == "picked"
    assert quick.presenter.sourceUrl and quick.service.engine.source_pil_image.getpixel((0, 0)) == (18, 171, 52, 255)
    assert visual_item(quick.window.contentItem(), "backgroundHex").property("text") == "#12AB34"
    for name in ("backgroundSource", "backgroundResult", "applyBackgroundHex", "closeBackground"):
        item = visual_item(quick.window.contentItem(), name)
        origin = item.mapToScene(QPointF())
        assert origin.x() >= 0 and origin.y() >= 0
        assert origin.x() + item.width() <= quick.window.width()
        assert origin.y() + item.height() <= quick.window.height()


def test_background_commands_reject_invalid_patch_stale_source_and_capture(quick, tmp_path):
    import copy
    presenter, service = quick.presenter, quick.service
    before = copy.deepcopy(presenter.view["background"])
    for patch in ({"enabled": True, "mode": "wrong"}, {"color_tolerance": float("nan")},
                  {"enabled": "yes"}, {"reference_rgb": [1, 2]}, {"reference_rgb": [1, True, 3]},
                  {"alpha_threshold": -1}, {"oops": 4}, {"enabled": True, "mode": "picked"}):
        assert not presenter.setBackground(patch)
        presenter.refresh()
        assert presenter.view["background"] == before
    assert not presenter.setBackgroundColor("#12GG34")
    assert presenter.setBackgroundColor("12ab34")
    assert not presenter.messageError
    assert service.engine.background_reference_rgb == (18, 171, 52)
    assert not presenter.pickBackground(0.5, 0.5, "image://painter/source/0")
    service.engine.is_capturing_manual_palette = True
    service._emit_capture_state()
    assert not presenter.setBackground({"enabled": True})
    assert not service.engine.background_removal_enabled
    service.engine.is_capturing_manual_palette = False
    service._emit_capture_state()
    # The engine can replace a source before Presenter's coalesced refresh.
    # The old URL must not sample those new pixels even during that short gap.
    from PIL import Image
    service._engine_set_image(Image.new("RGBA", (4, 4), "red"), origin="test")
    presenter.refresh()
    old_url = presenter.sourceUrl
    service._engine_set_image(Image.new("RGBA", (4, 4), "blue"), origin="test")
    assert not presenter.pickBackground(0.5, 0.5, old_url)
    assert service.engine.background_reference_rgb == (18, 171, 52)


def test_background_changes_rebuild_pixels_and_persist(quick, tmp_path):
    import numpy as np
    service, engine = quick.service, quick.service.engine
    source = QImage(16, 16, QImage.Format_RGBA8888)
    source.fill(Qt.white)
    for y in range(4, 12):
        for x in range(4, 12):
            source.setPixelColor(x, y, QColor("#E03020"))
    path = tmp_path / "object.png"
    assert source.save(str(path))
    assert quick.presenter.setChoice("color_picking_method", "hex_field")
    assert quick.presenter.setNumber("k_clusters", 2)
    assert quick.presenter.setNumber("brush_size", 1)
    service.apply_viewport_state(draw_region_desktop_px=(0, 0, 16, 16))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(path)))
    pump(lambda: service._preview_thread is None and engine.cluster_map is not None)

    def change(patch):
        url = quick.presenter.previewUrl
        assert quick.presenter.setBackground(patch)
        pump(lambda: quick.presenter.previewUrl != url and service._preview_thread is None)

    change(dict(enabled=True, mode="corner", color_tolerance=20))
    assert np.count_nonzero(engine.cluster_map == -1) == 192
    assert engine.cluster_map[6, 6] != -1
    preview = quick.images.requestImage(quick.presenter.previewUrl.split("painter/")[1], None, None)
    assert preview.pixelColor(0, 0).alpha() == 0
    assert preview.pixelColor(6, 6).alpha() == 255
    change(dict(enabled=False))
    assert not np.any(engine.cluster_map == -1)
    change(dict(enabled=True, mode="picked", reference_rgb=[224, 48, 32]))
    assert np.count_nonzero(engine.cluster_map == -1) == 64
    change(dict(mode="alpha", alpha_threshold=15))
    assert not np.any(engine.cluster_map == -1)
    assert quick.presenter.preset("save", "Фон")
    slug = quick.presenter.view["presets"][0]["slug"]
    change(dict(enabled=False, color_tolerance=99))
    assert quick.presenter.preset("apply", slug)
    pump(lambda: service._preview_thread is None and engine.background_removal_enabled)
    assert engine.background_removal_mode == "alpha"
    assert engine.background_color_tolerance == 20
    assert engine.background_reference_rgb == (224, 48, 32)
    assert engine.source_pil_image.getpixel((0, 0)) == (255, 255, 255, 255)


def test_background_recalculation_blocks_start_until_latest_result(quick, tmp_path, monkeypatch):
    from threading import Event
    service, engine = quick.service, quick.service.engine
    source = QImage(16, 16, QImage.Format_RGBA8888)
    source.fill(Qt.white)
    source.setPixelColor(8, 8, QColor(Qt.black))
    path = tmp_path / "source.png"
    assert source.save(str(path))
    assert quick.presenter.setChoice("color_picking_method", "hex_field")
    engine.hex_input_coord = (100, 100)
    service.apply_viewport_state(draw_region_desktop_px=(0, 0, 16, 16))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(path)))
    pump(lambda: quick.presenter.view["can_start"] and service._preview_thread is None)
    entered, release = Event(), Event()
    prepare = engine.prepare_image_and_palette
    starts = []

    def held_prepare():
        entered.set()
        assert release.wait(3), "Test did not release preparation"
        return prepare()

    monkeypatch.setattr(engine, "prepare_image_and_palette", held_prepare)
    monkeypatch.setattr(engine, "draw_image", lambda: starts.append(True))
    try:
        assert quick.presenter.setBackground(dict(enabled=True, mode="corner"))
        assert quick.presenter.view["preview_busy"] and not quick.presenter.view["can_start"]
        service.start_pause()  # The actual global hotkey target, including debounce time.
        assert not starts and service._worker_thread is None
        pump(entered.is_set)
        assert quick.presenter.setBackground(dict(enabled=False))
        service.start_pause()
        assert not starts and service._worker_thread is None
    finally:
        release.set()
    pump(lambda: not quick.presenter.view["preview_busy"] and quick.presenter.view["can_start"])
    assert quick.presenter.view["preview_ready"]
    assert engine.background_removal_enabled is False
    assert not (engine.cluster_map == -1).any()


def test_bw_settings_and_alpha_mask_reach_real_preview(quick, tmp_path):
    service, engine = quick.service, quick.service.engine
    source = QImage(16, 16, QImage.Format_RGBA8888)
    source.fill(Qt.white)
    for y in range(16):
        for x in range(8):
            source.setPixelColor(x, y, QColor(0, 0, 0, 128 if x < 4 else 255))
    path = tmp_path / "alpha.png"
    assert source.save(str(path))
    assert quick.presenter.setChoice("color_picking_method", "hex_field")
    assert quick.presenter.setNumber("brush_size", 1)
    service.apply_viewport_state(draw_region_desktop_px=(0, 0, 16, 16))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(path)))
    assert quick.presenter.setBackground(dict(enabled=True, mode="alpha", alpha_threshold=150))
    assert quick.presenter.setChoice("mode", "bw")
    pump(lambda: quick.presenter.view["preview_ready"] and service._preview_thread is None)
    assert (engine.cluster_map == -1).sum() == 64
    assert {tuple(color[3]) for color in engine.color_palette} == {(0, 0, 0), (255, 255, 255)}
    for mode, expected in (("black_only", {(0, 0, 0)}), ("white_only", {(255, 255, 255)})):
        assert quick.presenter.setChoice("bw_draw_mode", mode)
        pump(lambda: quick.presenter.view["preview_ready"] and service._preview_thread is None)
        assert {tuple(color[3]) for color in engine.color_palette} == expected
    assert not quick.presenter.setChoice("mode", "invalid")
    assert engine.mode == "bw"


def test_native_settings_profiles_and_error_are_real(quick):
    presenter = quick.presenter
    assert presenter.setNumber("k_clusters", 18)
    assert quick.service.engine.k_clusters == 18
    assert presenter.preset("save", "Native profile")
    saved = presenter.view["presets"][0]
    assert presenter.setNumber("k_clusters", 32)
    assert presenter.preset("apply", saved["slug"])
    assert quick.service.engine.k_clusters == 18
    assert not presenter.setNumber("k_clusters", -1)
    assert presenter.messageError
    assert quick.service.engine.k_clusters == 18


def test_qml_palette_editor_saves_exact_color_and_keeps_draft_on_revision_change(quick):
    engine = quick.service.engine
    engine.manual_palette_coords = [dict(x=-90, y=80, rgb=[238, 245, 252], hex="#EEF5FC")]
    engine._invalidate_manual_palette_cache()
    quick.service._on_engine_manual_palette_changed()
    quick.window.resize(1200, 1050)
    quick.window.setProperty("page", 4)
    pump(lambda: len(quick.presenter.view["manual_palette"]["entries"]) == 1)
    assert quick.presenter.view["manual_palette"]["entries"][0]["hex"] == "#EEF5FC"
    pump()
    sample = visual_item(quick.window.contentItem(), "paletteSample_0")
    assert sample and sample.isVisible()
    point = sample.mapToScene(QPointF(sample.width() / 2, sample.height() / 2)).toPoint()
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=point)
    color = visual_item(quick.window.contentItem(), "paletteColor")
    pump(lambda: color.isVisible())
    color.forceActiveFocus()
    QTest.keyClick(quick.window, Qt.Key_A, Qt.ControlModifier)
    QTest.keyClick(quick.window, Qt.Key_NumberSign)
    for _ in range(6):
        QTest.keyClick(quick.window, Qt.Key_F, Qt.ShiftModifier)
    save = visual_item(quick.window.contentItem(), "savePaletteButton")
    point = save.mapToScene(QPointF(save.width() / 2, save.height() / 2)).toPoint()
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=point)
    pump(lambda: engine.manual_palette_coords[0]["hex"] == "#FFFFFF")
    assert engine._get_manual_palette_cache()["entries"][0]["rgb"] == (255, 255, 255)
    color.setProperty("text", "#112233")
    quick.service._on_engine_manual_palette_changed()
    pump(lambda: not save.isEnabled())
    assert color.property("text") == "#112233"
    assert not quick.qml_warnings


def test_qml_palette_commands_reject_capture_and_stale_deletion(quick):
    engine = quick.service.engine
    engine.manual_palette_coords = [dict(x=10, y=20, rgb=[0, 0, 0], hex="#000000")]
    engine._invalidate_manual_palette_cache()
    quick.service._on_engine_manual_palette_changed()
    quick.presenter.refresh()
    revision = quick.presenter.view["manual_palette"]["revision"]
    engine.is_capturing_manual_palette = True
    quick.service._emit_capture_state()
    assert not quick.presenter.editPalette("clear", -1, revision, "", "", "")
    assert engine.manual_palette_coords
    engine.is_capturing_manual_palette = False
    quick.service._emit_capture_state()
    assert quick.presenter.editPalette("update", 0, revision, "10", "20", "FFFFFF")
    assert not quick.presenter.editPalette("remove", 0, revision, "", "", "")
    assert len(engine.manual_palette_coords) == 1


def test_image_provider_uses_qimages_and_rejects_old_revision():
    images = ImageProvider()
    first = QImage(4, 4, QImage.Format_RGB32)
    first.fill(Qt.red)
    images.publish("source", first)
    second = QImage(4, 4, QImage.Format_RGB32)
    second.fill(Qt.blue)
    images.publish("source", second)
    assert images.requestImage("source/1", None, None).isNull()
    assert images.requestImage("source/2", None, None).pixelColor(0, 0) == QColor(Qt.blue)
    assert images.publish("source", None) == ""


def test_palette_edit_rebuilds_real_preparation_with_corrected_color(quick, tmp_path):
    service, engine = quick.service, quick.service.engine
    assert quick.presenter.selectProfile("universal", "dfs_4dir")
    engine.manual_palette_coords = [dict(x=10, y=20, rgb=[238, 245, 252], hex="#EEF5FC")]
    engine._invalidate_manual_palette_cache()
    service._on_engine_manual_palette_changed()
    source = QImage(16, 16, QImage.Format_RGB32)
    source.fill(Qt.white)
    path = tmp_path / "white.png"
    assert source.save(str(path))
    service.apply_viewport_state(draw_region_desktop_px=(0, 0, 16, 16))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(path)))
    pump(lambda: bool(engine.color_palette) and service._preview_thread is None)
    assert {tuple(item[3]) for item in engine.color_palette} == {(238, 245, 252)}
    url = quick.presenter.previewUrl
    revision = service.manual_palette_snapshot()["revision"]
    assert quick.presenter.editPalette("update", 0, revision, "10", "20", "FFFFFF")
    pump(lambda: service._preview_thread is None and bool(engine.color_palette)
         and {tuple(item[3]) for item in engine.color_palette} == {(255, 255, 255)})
    pump(lambda: quick.presenter.previewUrl != url)


def test_native_recording_commits_key_and_cancels_on_deactivate(quick):
    presenter = quick.presenter
    assert presenter.beginRecording("select_area")
    QTest.keyClick(quick.window, Qt.Key_F11)
    pump(lambda: not presenter.recordingCode)
    assert quick.service.get_hotkeys()["global"]["select_area"] == "F11"
    assert presenter.beginRecording("select_area")
    QCoreApplication.sendEvent(quick.window, QEvent(QEvent.WindowDeactivate))
    assert not presenter.recordingCode
    assert not quick.service._hotkey_capture_active


def test_failed_save_keeps_native_application_usable(quick, monkeypatch):
    write = quick.controller.session.store.write_autosave
    def fail(*_):
        raise OSError("disk unavailable")
    monkeypatch.setattr(quick.controller.session.store, "write_autosave", fail)
    assert not quick.presenter.closeApplication()
    pump(lambda: bool(quick.controller._close_error))
    assert not quick.controller._closed
    assert not quick.service._is_shutting_down
    assert "disk unavailable" in quick.presenter.message
    assert not quick.controller._closing and not quick.service._close_pending
    assert quick.presenter.setSetting("brush_size", 6)
    monkeypatch.setattr(quick.controller.session.store, "write_autosave", write)
    assert not quick.window.close()
    pump(lambda: quick.controller._closed and not quick.window.isVisible())


def test_qml_displays_controller_models_and_missing_requirements(quick):
    selector = quick.window.findChild(QObject, "placeSelector")
    rows = quick.window.findChild(QObject, "hotkeyRows")
    assert selector.property("count") == len(quick.presenter.view["places"])
    assert selector.property("currentText") == quick.controller.state.preparation.current_place_label
    assert rows.property("count") == len(HOTKEY_DEFINITIONS)
    assert isinstance(quick.presenter.view["missing_requirements"], list)
    assert isinstance(quick.presenter.view["required_calibrations"], list)


def test_text_editing_owns_paste_and_restores_window_shortcuts(quick, monkeypatch):
    pasted_images = []
    monkeypatch.setattr(quick.service, "paste_from_clipboard", lambda: pasted_images.append(True))
    quick.window.setProperty("page", 2)
    field = quick.window.findChild(QQuickItem, "presetName")
    field.forceActiveFocus()
    pump()
    QApplication.clipboard().setText("My preset")
    QTest.keyClick(quick.window, Qt.Key_V, Qt.ControlModifier)
    pump()
    assert field.property("text") == "My preset"
    assert pasted_images == []
    assert not any(shortcut.isEnabled() for shortcut in quick.presenter._shortcuts)
    quick.window.setProperty("page", 0)
    button = quick.window.findChild(QQuickItem, "openButton")
    button.forceActiveFocus()
    pump()
    assert all(shortcut.isEnabled() for shortcut in quick.presenter._shortcuts)
    QTest.keyClick(quick.window, Qt.Key_V, Qt.ControlModifier)
    pump(lambda: pasted_images)
    assert pasted_images == [True]


def test_keyboard_slider_updates_real_settings(quick):
    quick.window.setProperty("page", 1)
    slider = visual_item(quick.window.contentItem(), "setting_k_clusters")
    assert quick.presenter.setNumber("k_clusters", 18)
    slider.forceActiveFocus()
    pump()
    QTest.keyClick(quick.window, Qt.Key_Right)
    pump(lambda: quick.service.engine.k_clusters == 19)
    assert slider.property("value") == 19


def test_qml_preview_and_start_execute_real_pipeline_with_simulated_pen(quick, tmp_path, monkeypatch):
    from helpers.sim_pen import SimPen
    from engine.olegpainter.drawing_events import DrawingPhase

    service, engine = quick.service, quick.service.engine
    source = QImage(24, 24, QImage.Format_RGB32)
    source.fill(QColor("#326ab2"))
    path = tmp_path / "drawing.png"
    assert source.save(str(path))
    assert quick.presenter.setChoice("color_picking_method", "hex_field")
    assert quick.presenter.setNumber("k_clusters", 1)
    assert quick.presenter.setNumber("brush_size", 1)
    engine.hex_input_coord = (100, 100)
    service.apply_viewport_state(draw_region_desktop_px=(0, 0, 24, 24))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(path)))
    pump(lambda: bool(quick.presenter.previewUrl) and service._preview_thread is None)
    quick.controller.refresh_preparation()
    pump(lambda: quick.presenter.view["can_start"])
    preview = quick.window.findChild(QQuickItem, "previewImage")
    assert preview.property("source").toString() == quick.presenter.previewUrl
    assert engine.cluster_map is not None and engine.color_palette

    # Replace device operations only; preparation, command worker, routing,
    # drawing lifecycle and QML progress remain real.
    callback = engine.pixel_update_callback
    cancellation = engine._automation_cancelled
    pen = SimPen(engine, 24, 24)
    engine.pixel_update_callback = callback
    engine._automation_cancelled = cancellation
    engine.drawing_enabled = False
    engine.focus_target_window_enabled = False
    engine.post_draw_repair_enabled = False
    engine.dynamic_brush_enabled = False
    monkeypatch.setattr(engine, "pick_color", lambda *args: None)
    start = quick.window.findChild(QQuickItem, "startButton")
    assert start.isEnabled()
    point = start.mapToScene(QPointF(start.width() / 2, start.height() / 2)).toPoint()
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=point)
    pump(lambda: service.drawing_phase == DrawingPhase.COMPLETED)
    pump(lambda: quick.presenter.view["phase"] == "completed")
    assert pen.canvas.any()
    assert engine.drawn_mask[engine.cluster_map != -1].all()
    assert quick.presenter.stats["percent"] == 100


def test_setting_catalog_updates_real_engine_and_persists(quick):
    expected = {}
    for key, setting in SETTINGS.items():
        values = ([True, False] if setting.kind == "bool" else
                  [option[0] for option in setting.options] if setting.kind == "choice" else
                  [setting.minimum + setting.step])
        for value in values:
            assert quick.presenter.setSetting(key, value), (key, quick.presenter.message)
            assert quick.presenter.view["settings"][key] == value, key
        expected[key] = values[-1]
    assert quick.presenter.preset("save", "All settings")
    slug = quick.presenter.view["presets"][0]["slug"]
    assert quick.presenter.setSetting("brush_size", 7)
    assert quick.presenter.preset("apply", slug)
    assert quick.presenter.view["settings"] == expected


@pytest.mark.parametrize("key,value", [
    ("k_clusters", 2.5), ("k_clusters", True), ("brush_size", "12"),
    ("draw_delay", float("inf")), ("draw_delay", float("nan")),
    ("draw_delay", -1), ("hex_add_hash", "false"),
    ("prep_quantization_mode", "unknown"), ("__dict__", 1),
])
def test_invalid_setting_values_do_not_reach_service(quick, key, value):
    before = quick.service.snapshot_painter_config()
    assert not quick.presenter.setSetting(key, value)
    assert quick.presenter.messageError
    assert quick.service.snapshot_painter_config() == before


def test_settings_are_blocked_during_capture(quick):
    assert quick.presenter.beginRecording("select_area")
    assert not quick.presenter.setSetting("brush_size", 7)
    quick.presenter.cancelRecording()
    assert quick.presenter.setSetting("brush_size", 7)


def test_all_qml_setting_groups_load_and_edit_with_stable_controls(quick):
    quick.window.setProperty("page", 1)
    quick.presenter.setAdvancedSettings(True)
    groups = quick.window.findChild(QQuickItem, "settingsGroup")
    assert groups.property("count") == len(GROUPS)
    for index, (_, _, settings) in enumerate(GROUPS):
        groups.setProperty("currentIndex", index)
        pump()
        for setting in settings:
            assert visual_item(quick.window.contentItem(), "setting_" + setting.key) is not None
    groups.setProperty("currentIndex", 0)  # «Основное»
    pump()
    switch = visual_item(quick.window.contentItem(), "setting_post_draw_repair_enabled")
    before = quick.service.engine.post_draw_repair_enabled
    switch.forceActiveFocus()
    QTest.keyClick(quick.window, Qt.Key_Space)
    pump()
    assert quick.service.engine.post_draw_repair_enabled != before
    assert switch == visual_item(quick.window.contentItem(), "setting_post_draw_repair_enabled")


def test_refresh_does_not_read_preset_files(quick, monkeypatch):
    def unexpected_read():
        pytest.fail("State refresh must not enumerate files")
    monkeypatch.setattr(quick.controller.config_store, "list", unexpected_read)
    quick.presenter.refresh()
    quick.presenter.setSetting("brush_size", 4)


def test_profile_export_import_uses_existing_format_and_preserves_source(quick, tmp_path):
    import json

    source = QImage(12, 12, QImage.Format_RGB32)
    source.fill(Qt.red)
    source_path = tmp_path / "source.png"
    source.save(str(source_path))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(source_path)))
    assert quick.presenter.setSetting("k_clusters", 19)
    assert quick.presenter.changeHotkey("select_area", "F11")
    assert quick.presenter.preset("save", "Portable")
    slug = quick.presenter.view["presets"][0]["slug"]
    path = tmp_path / "export.json"
    assert quick.presenter.exportPreset(slug, QUrl.fromLocalFile(str(path)))
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["payload"]["painter"]["k_clusters"] == 19
    assert "IMAGE_PATH" not in document["payload"]["painter"]
    assert document["payload"]["hotkeys"]["global"]["select_area"] == "F11"
    assert quick.presenter.preset("delete", slug)
    assert quick.presenter.setSetting("k_clusters", 32)
    assert quick.presenter.changeHotkey("select_area", "F1")
    assert quick.presenter.importPreset(QUrl.fromLocalFile(str(path)))
    slug = quick.presenter.view["presets"][0]["slug"]
    assert quick.presenter.preset("apply", slug)
    assert quick.service.engine.k_clusters == 19
    assert quick.service.get_hotkeys()["global"]["select_area"] == "F11"
    assert quick.service._last_source_qimg.pixelColor(0, 0) == QColor(Qt.red)


def test_qml_numeric_entry_accepts_comma_and_reports_invalid_value(quick):
    quick.window.setProperty("page", 1)
    field = visual_item(quick.window.contentItem(), "value_draw_delay")
    field.forceActiveFocus()
    field.setProperty("text", "0,0125")
    QTest.keyClick(quick.window, Qt.Key_Return)
    pump()
    assert quick.service.engine.draw_delay == 0.0125
    assert field.property("text") == "0.0125"
    field.setProperty("text", "-2")
    QTest.keyClick(quick.window, Qt.Key_Return)
    pump()
    assert quick.service.engine.draw_delay == 0.0125
    assert quick.presenter.messageError
    assert field.property("text") == "0.0125"


def test_flat_qml_and_existing_structured_profiles_remain_usable(quick):
    store = quick.controller.config_store
    flat = store.save_new("Early QML", ["painter"], {"k_clusters": 11})
    structured = store.save_new("Classic", ["drawing"], {"painter": {"k_clusters": 23}})
    assert quick.presenter.preset("apply", flat.slug)
    assert quick.service.engine.k_clusters == 11
    assert quick.presenter.preset("apply", structured.slug)
    assert quick.service.engine.k_clusters == 23


def test_close_waits_for_preparation_without_freezing_or_accepting_commands(quick, monkeypatch):
    from threading import Event
    from PySide6.QtCore import QTimer
    entered, release = Event(), Event()
    def preparation():
        entered.set()
        assert release.wait(3)
    monkeypatch.setattr(quick.service, "_invoke_engine_preprocess", preparation)
    monkeypatch.setattr(quick.service.engine, "prepare_image_and_palette", lambda: None)
    ticks, closed = [], []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(1))
    quick.controller.closed.connect(lambda: closed.append(True))
    try:
        quick.service._start_preview_worker(strict=True)
        pump(entered.is_set)
        started = time.monotonic()
        assert not quick.window.close()
        assert time.monotonic() - started < 0.2
        timer.start()
        pump(lambda: len(ticks) >= 4 and quick.presenter.view["closing"])
        assert quick.window.isVisible()
        assert not quick.controller._closed
        assert not quick.presenter.view["can_edit"]
        assert not quick.presenter.action("toggle_stencil")
        assert not quick.presenter.setSetting("k_clusters", 17)
        assert not quick.window.close()  # repeated request keeps the same shutdown
        assert not closed
        release.set()
        pump(lambda: quick.controller._closed and not quick.window.isVisible())
        assert closed == [True]
        assert quick.service._shutdown_completed
    finally:
        release.set()
        timer.stop()


def test_close_reports_device_failure_and_retries_without_losing_window(quick, monkeypatch):
    from threading import get_ident
    calls = []
    def release_input():
        calls.append(get_ident())
        if len(calls) == 1:
            raise RuntimeError("device unavailable")
    monkeypatch.setattr(quick.service, "_runtime_initialized", True)
    monkeypatch.setattr(quick.service.engine, "stop_script", release_input)
    assert not quick.window.close()
    pump(lambda: bool(quick.presenter.view.get("close_error")))
    assert "device unavailable" in quick.presenter.view["close_error"]
    assert quick.window.isVisible()
    assert not quick.controller._closed
    assert calls and all(thread != get_ident() for thread in calls)
    assert not quick.presenter.closeApplication()
    pump(lambda: quick.controller._closed and not quick.window.isVisible())
    assert len(calls) == 2


def test_failed_save_on_exit_can_close_without_saving(quick, monkeypatch):
    """Only «retry» was offered: with a disk that refuses the session there was no
    way out but the task manager (audit 2026-10-05)."""
    from concurrent.futures import Future

    def refuse(force=False):
        future = Future()
        future.set_exception(OSError("диск недоступен"))
        return future

    monkeypatch.setattr(quick.controller.session, "request_save", refuse)
    assert not quick.window.close()
    pump(lambda: quick.presenter.view.get("close_save_failed"))
    assert quick.window.isVisible() and "диск недоступен" in quick.presenter.message
    button = visual_item(quick.window.contentItem(), "closeWithoutSaving")
    assert button is not None and button.isVisible()
    quick.presenter.closeWithoutSaving()
    pump(lambda: quick.controller._closed and not quick.window.isVisible())


def test_status_line_hides_engine_journal_and_strips_error_prefix(quick):
    presenter, service = quick.presenter, quick.service
    service.statusChanged.emit("Палитра обновлена.")
    assert presenter.message == "Палитра обновлена." and not presenter.messageError
    for technical in ("[INFO] Extra actions (pre) playback disabled.", "info: Stop signal received."):
        service.statusChanged.emit(technical)
        assert presenter.message == "Палитра обновлена."
    service.statusChanged.emit("error: Точка вне окна рисования.")
    assert presenter.message == "Точка вне окна рисования." and presenter.messageError
    service.statusChanged.emit("[ERROR] Сбой записи.")
    assert presenter.message == "Сбой записи." and presenter.messageError
    # Refusals the user must act on: no technical prefix, shown as a problem.
    service.statusChanged.emit("warn: Сначала завершите назначение горячей клавиши.")
    assert presenter.message == "Сначала завершите назначение горячей клавиши." and presenter.messageError
    service.statusChanged.emit("[ОШИБКА] Палитра пуста для рисования по слоям.")
    assert presenter.message == "Палитра пуста для рисования по слоям." and presenter.messageError


def test_clipboard_without_picture_explains_and_copied_file_opens(quick, tmp_path, monkeypatch):
    from types import SimpleNamespace
    from PySide6.QtCore import QMimeData
    from ui.services import painter_service as module

    # A fake clipboard: the real one takes ownership of a Python QMimeData and the
    # process then crashed at exit (0xC0000005, double delete).
    class Clipboard:
        def __init__(self, mime):
            self.mime = mime

        def image(self):
            return QImage()

        def mimeData(self):
            return self.mime

    text = QMimeData()
    text.setText("просто текст")
    clipboard = Clipboard(text)
    monkeypatch.setattr(module, "QClipboard", Clipboard)
    monkeypatch.setattr(module, "QGuiApplication", SimpleNamespace(clipboard=lambda: clipboard, instance=lambda: None))
    assert not quick.service.paste_from_clipboard()
    assert quick.presenter.message.startswith("В буфере обмена нет картинки") and quick.presenter.messageError
    # Ctrl+C on a picture file in Explorer puts a file URL, not pixels, on the clipboard.
    source = tmp_path / "copied.png"
    image = QImage(24, 16, QImage.Format_RGB32)
    image.fill(QColor("#a03020"))
    assert image.save(str(source))
    files = QMimeData()
    files.setUrls([QUrl.fromLocalFile(str(source))])
    clipboard.mime = files
    assert quick.service.paste_from_clipboard()
    pump(lambda: quick.presenter.view["image_loaded"])
    assert quick.service.engine.IMAGE_PATH.replace("\\", "/").endswith("copied.png")


def test_error_is_visible_until_the_next_command_or_dismiss(quick):
    presenter = quick.presenter
    assert not presenter.setBackgroundColor("red")
    assert presenter.messageError
    pump()
    assert visual_item(quick.window.contentItem(), "dismissMessage").isVisible()
    assert presenter.refreshPresets()  # any next command: the old problem is gone
    assert presenter.message == "" and not presenter.messageError
    assert not presenter.setBackgroundColor("red")
    presenter.dismissMessage()
    assert presenter.message == ""


def test_help_page_opens_support_links_and_the_bot_waits(quick, monkeypatch):
    # HELP-001: the Help button leads to a page with the owner's links.
    opened = []
    monkeypatch.setattr("ui.quick.presenter.open_url", opened.append)
    quick.window.resize(1280, 1200)
    sidebar = quick.window.findChild(QQuickItem, "sidebar")
    button = visual_item(sidebar, "helpButton")
    point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=point)
    pump(lambda: quick.window.property("page") == 9)
    links = {item["id"]: item for item in quick.presenter.supportLinks}
    assert set(links) == {"personal", "chat", "channel", "youtube", "boosty"}
    assert all(link["enabled"] for link in links.values())
    tile = visual_item(quick.window.contentItem(), "supportLink_youtube")
    pump(lambda: tile.isVisible())
    point = tile.mapToScene(QPointF(tile.width() / 2, tile.height() / 2)).toPoint()
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=point)
    pump(lambda: opened)
    assert opened == ["https://www.youtube.com/@olegroblox1"]
    assert not quick.presenter.openLink("unknown")  # an unknown link opens nothing
    assert len(opened) == 1
    assert quick.presenter.openLogsFolder() and len(opened) == 2


def test_one_vocabulary_for_colour_methods_everywhere(quick):
    # VOCABULARY-001: the settings, the calibration dialog and the quick start use the same words.
    presenter = quick.presenter
    methods = [(m["id"], m["label"]) for m in presenter.colorMethods]
    assert methods == [("hex_field", "Поле HEX"), ("hsv_palette", "Круг и яркость"),
                       ("manual_palette", "Готовые цвета по одному"), ("screen_palette", "Палитра рамкой"),
                       ("wheel_square", "Кольцо и квадрат")]
    quick_methods = [(m["id"], m["title"]) for m in presenter.view["quick_start"]["methods"]]
    assert quick_methods == methods
    assert [label for _id, label in SETTINGS["color_picking_method"].options] == [label for _id, label in methods]
    assert presenter.setChoice("color_picking_method", "manual_palette")
    pump(lambda: presenter.view["quick_start"]["method_choice"] == "manual_palette")  # its own tile now


def test_calibration_tools_follow_the_chosen_method_and_route(quick, monkeypatch):
    presenter, service = quick.presenter, quick.service
    quick.window.setProperty("page", 1)
    buttons = lambda: visual_item(quick.window.contentItem(), "calibrationButtons")
    assert presenter.setChoice("color_picking_method", "hsv_palette")
    pump(lambda: [c.property("text") for c in buttons().childItems() if c.isVisible()][:2] == ["Указать круг", "Указать яркость"])
    assert presenter.setHsvDirection("cw")
    assert service.engine.palette_rotation_direction_calib == "cw" and presenter.view["hsv_direction"] == "cw"
    assert not presenter.setHsvDirection("sideways")
    # «Контур + заливка»: the pen/bucket switching appears with the route and reaches the engine.
    tools = lambda: visual_item(quick.window.contentItem(), "outlineFillTools")
    assert presenter.selectProfile("universal", "dfs_4dir")
    pump(lambda: not tools().isVisible())
    assert presenter.selectProfile("universal", "outline_and_fill")
    pump(lambda: tools().isVisible())
    assert presenter.setOutlineFill({"brush_key": "p", "fill_key": "k"})
    assert (service.engine.outline_fill_brush_key, service.engine.outline_fill_fill_key) == ("p", "k")
    assert presenter.setOutlineFill({"mode": "coords"})
    assert presenter.view["outline_fill"]["mode"] == "coords"
    assert not presenter.setOutlineFill({"brush_key": " "})
    assert not presenter.setOutlineFill({"volume": 3})


def test_show_calibration_draws_every_point_the_program_will_click(quick, monkeypatch):
    presenter, service = quick.presenter, quick.service
    assert not presenter.showCalibration()  # no screen tools in this window
    shown = []
    overlays = SimpleNamespace(show_calibration=lambda items, hint="": shown.append((items, hint)), flash=None)
    quick.controller.desktop = SimpleNamespace(desktop_overlays=overlays)
    try:
        assert not presenter.showCalibration()  # nothing calibrated yet
        assert "Пока нечего показывать" in presenter.message
        service.apply_viewport_state(draw_region_desktop_px=(100, 50, 480, 300))
        service.engine.color_picking_method = "hex_field"
        service.engine.hex_input_coord = (900, 40)
        assert presenter.showCalibration()
        items, hint = shown[-1]
        kinds = {(item["type"], item["label"]) for item in items}
        assert ("rect", "Холст") in kinds and ("point", "HEX") in kinds and "Esc" in hint
        pump(lambda: presenter.view["area_rect"] == [100, 50, 480, 300])
    finally:
        quick.controller.desktop = None


def test_all_hotkeys_can_be_reset_at_once(quick):
    from ui.helpers.hotkey_definitions import default_hotkey_profile
    assert quick.presenter.changeHotkey("select_area", "F11")
    assert quick.presenter.resetAllHotkeys()
    bindings = {code: seq for group in quick.service.get_hotkeys().values() for code, seq in group.items()}
    assert bindings == default_hotkey_profile()


def _driver_parts():
    from infrastructure.input_driver import DriverPart
    folder = r"C:\Windows\System32\drivers"
    return (DriverPart("keyboard", "keyboard", True, True, folder + r"\keyboard.sys", True, True, "1.0.0.0", 5_000.0),
            DriverPart("mouse", "mouse", True, True, folder + r"\mouse.sys", True, True, "1.0.0.0", 5_000.0))


def _set_driver(quick, monkeypatch, status):
    """What Windows says about Interception from now on, read by «Проверить снова»."""
    monkeypatch.setattr(quick.presenter._drivers, "_check", lambda: status)
    assert quick.presenter.driverAction("recheck")
    pump()


def _click(quick, item):
    # Let a banner that just changed its text settle: a click at the old place misses.
    QTest.qWait(60)
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint())


def test_missing_driver_is_explained_before_any_setup(quick, monkeypatch):
    from infrastructure.input_driver import DriverStatus
    root = quick.window.contentItem()
    banner = lambda: visual_item(root, "driverBanner")
    text = lambda: visual_item(root, "driverBannerText").property("text")
    _set_driver(quick, monkeypatch, DriverStatus("missing"))
    pump(lambda: banner().isVisible() and visual_item(root, "installDriver").isVisible())
    assert "не установлен" in text().lower() and "перезагрузите" in quick.presenter.message
    _set_driver(quick, monkeypatch, DriverStatus("reboot", parts=_driver_parts()))
    pump(lambda: visual_item(root, "restartForDriver").isVisible() and not visual_item(root, "installDriver").isVisible())
    assert quick.presenter.inputDriver["state"] == "reboot" and "после перезагрузки" in text()
    # A filter without its driver: the next boot could leave the computer without keyboard and mouse.
    _set_driver(quick, monkeypatch, DriverStatus("broken", parts=_driver_parts()))
    pump(lambda: visual_item(root, "repairDriver").isVisible() and visual_item(root, "removeDriverBanner").isVisible())
    assert "Не перезагружайте" in text() and quick.presenter.messageError
    # Memory Integrity on: nothing to install, the reason one click away.
    _set_driver(quick, monkeypatch, DriverStatus("missing", hvci=True))
    pump(lambda: visual_item(root, "driverDetails").isVisible())
    assert not visual_item(root, "installDriver").isVisible() and "Целостность памяти" in text()
    _set_driver(quick, monkeypatch, DriverStatus("ready", parts=_driver_parts(), keyboards=1, mice=1))
    pump(lambda: not banner().isVisible())
    assert quick.presenter.message == "Драйвер управления мышью работает."


def test_driver_changes_only_after_every_warning_is_ticked(quick, monkeypatch):
    from infrastructure.input_driver import DriverStatus
    root = quick.window.contentItem()
    consent = quick.window.findChild(QObject, "driverConsent")
    confirm = lambda: visual_item(root, "driverConsentConfirm")
    changes = []
    monkeypatch.setattr(quick.presenter._drivers, "change", lambda action: changes.append(action) or True)
    _set_driver(quick, monkeypatch, DriverStatus("missing", anticheats=("FACEIT",)))
    install = visual_item(root, "installDriver")
    pump(lambda: install.isVisible())
    _click(quick, install)
    pump(lambda: consent.property("visible") and consent.property("opened"))
    assert consent.property("command") == "install" and not confirm().isEnabled()
    games = quick.window.findChild(QObject, "driverConsentGames").property("text")
    assert "FACEIT" in games and "без проблем" in games and "гарантий нет" in games
    boxes = ["driverRiskThirdParty", "driverRiskGames", "driverRiskRecovery", "driverRiskRestart"]
    for name in boxes:
        assert not confirm().isEnabled() and visual_item(root, "driverConsentHint").isVisible()
        quick.window.findChild(QObject, name).setProperty("checked", True)
        pump()
    assert confirm().isEnabled() and not visual_item(root, "driverConsentHint").isVisible()
    assert changes == []                              # nothing runs before the explicit click
    _click(quick, confirm())
    pump(lambda: changes == ["install"] and not consent.property("visible"))
    # Reopened: every box starts unticked again.
    QMetaObject.invokeMethod(consent, "open")
    pump(lambda: consent.property("opened"))
    assert not any(quick.window.findChild(QObject, name).property("checked") for name in boxes)
    QMetaObject.invokeMethod(consent, "close")
    pump(lambda: not consent.property("visible"))
    # Removal from the Help page: its own three boxes, the other programs that need it named.
    _set_driver(quick, monkeypatch, DriverStatus("ready", parts=_driver_parts(), users=("Veyon",), keyboards=2, mice=1))
    quick.window.resize(1280, 1200)
    quick.window.setProperty("page", 9)
    status = visual_item(root, "driverStatus")
    remove = visual_item(root, "removeDriver")
    pump(lambda: remove.isVisible() and status.property("text") == "работает")
    assert "C:\\Windows\\System32\\drivers\\mouse.sys" in visual_item(root, "driverLocation").property("text")
    assert "Veyon" in visual_item(root, "driverOrigin").property("text")
    scroll = quick.window.findChild(QQuickItem, "pagesScroll").property("contentItem")
    card = visual_item(root, "helpDriver")
    scroll.setProperty("contentY", max(0, card.mapToItem(scroll.property("contentItem"), 0, 0).y() - 10))
    pump()
    _click(quick, remove)
    pump(lambda: consent.property("opened") and consent.property("command") == "uninstall")
    assert "Veyon" in quick.window.findChild(QObject, "driverConsentUsers").property("text")
    for name in ["driverRemoveDrawing", "driverRemoveOthers", "driverRemoveRestart"]:
        assert not confirm().isEnabled()
        quick.window.findChild(QObject, name).setProperty("checked", True)
        pump()
    _click(quick, confirm())
    pump(lambda: changes == ["install", "uninstall"])
    # Memory Integrity: even with every box ticked the install stays off.
    _set_driver(quick, monkeypatch, DriverStatus("missing", hvci=True))
    consent.setProperty("command", "install")
    QMetaObject.invokeMethod(consent, "open")
    pump(lambda: consent.property("opened"))
    for name in boxes:
        quick.window.findChild(QObject, name).setProperty("checked", True)
    pump(lambda: visual_item(root, "driverConsentHvci").isVisible())
    assert not confirm().isEnabled() and changes == ["install", "uninstall"]
    QMetaObject.invokeMethod(consent, "close")
    pump(lambda: not consent.property("visible"))


def test_driver_job_result_reaches_the_window_and_the_restart_saves_first(quick, monkeypatch):
    from infrastructure.input_driver import DriverStatus
    drivers = quick.presenter._drivers
    root = quick.window.contentItem()
    statuses = [DriverStatus("missing", r"C:\x\install-interception.exe"), DriverStatus("reboot", parts=_driver_parts())]
    monkeypatch.setattr(drivers, "_check", lambda: statuses.pop(0) if len(statuses) > 1 else statuses[0])
    monkeypatch.setattr(drivers, "_run", lambda path, action: 0)
    assert quick.presenter.driverAction("recheck")
    assert quick.presenter.driverAction("install")
    pump(lambda: quick.presenter.inputDriver["state"] == "reboot" and not quick.presenter.inputDriver["busy"])
    pump(lambda: "Перезагрузите компьютер" in quick.presenter.message)
    restart = visual_item(root, "restartForDriver")
    pump(lambda: restart.isVisible())
    saved, commands = [], []
    monkeypatch.setattr(quick.controller, "save", lambda: saved.append(True))
    monkeypatch.setattr(drivers, "_command", lambda args: commands.append(args) or SimpleNamespace(returncode=0))
    _click(quick, restart)
    dialog = quick.window.findChild(QObject, "driverRestart")
    pump(lambda: dialog.property("opened"))
    assert commands == []                             # asked first
    QMetaObject.invokeMethod(dialog, "accept")
    pump(lambda: commands and saved)
    assert commands[0][:4] == ["shutdown", "/r", "/t", "60"]
    cancel = visual_item(root, "cancelRestartBanner")
    # The closing dialog still catches clicks.
    pump(lambda: cancel.isVisible() and not restart.isVisible() and not dialog.property("visible"))
    _click(quick, cancel)
    pump(lambda: commands[-1] == ["shutdown", "/a"] and not cancel.isVisible())
    assert quick.presenter.message == "Перезагрузка отменена."


def test_start_hotkey_steps_the_pinned_window_aside(quick, monkeypatch):
    # F3 is pressed in the game while the pinned window may cover the canvas:
    # it must step aside like the start button, and only when drawing can start.
    started = []
    quick.controller.desktop = object()  # restored before the fixture closes the app
    quick.window.show()
    try:
        monkeypatch.setattr(type(quick.controller.state), "can_start", property(lambda self: False))
        quick.presenter._launch_hotkey_action("start_pause", lambda: started.append("refused"))
        assert quick.window.visibility() != QQuickWindow.Minimized
        monkeypatch.setattr(type(quick.controller.state), "can_start", property(lambda self: True))
        quick.presenter._launch_hotkey_action("start_pause", lambda: started.append("started"))
        assert started == ["refused", "started"]
        assert quick.window.visibility() == QQuickWindow.Minimized
        # resuming a paused drawing also clears the canvas (live Draw & Donate)
        quick.window.showNormal()
        from dataclasses import replace
        from engine.olegpainter.drawing_events import DrawingPhase
        monkeypatch.setattr(quick.controller, "state", replace(quick.controller.state, drawing=DrawingPhase.PAUSED))
        quick.presenter._step_aside_for_drawing()
        assert quick.window.visibility() == QQuickWindow.Minimized
    finally:
        quick.presenter._drawing_return = False
        quick.controller.desktop = None
        quick.window.showNormal()


@pytest.mark.parametrize("quick", [True], indirect=True)
def test_quick_start_targets_apply_colour_method_and_steps_follow_state(quick, tmp_path, monkeypatch):
    presenter = quick.presenter
    assert quick.window.property("page") == 7  # first launch opens the quick start
    steps = lambda: {step["id"]: step for step in presenter.view["quick_start"]["steps"]}
    assert steps()["image"]["current"] and not steps()["image"]["done"]
    # Nothing is preselected for a newcomer: the steps wait for the choice (ONBOARD-003).
    assert presenter.view["quick_start"]["target"] == "" and not presenter.view["quick_start"]["target_chosen"]
    assert presenter.view["current_place_id"] == "universal"
    assert visual_item(quick.window.contentItem(), "quickStartChooseHint").isVisible()

    assert presenter.chooseTarget("speed_draw")
    assert presenter.view["quick_start"]["target"] == "speed_draw" and presenter.view["quick_start"]["target_chosen"]
    pump(lambda: presenter.view["quick_start"]["method"] == "hsv_palette")
    actions = [a["action"] for a in steps()["colour"]["actions"]]
    assert actions == ["calibrate_color_circle", "calibrate_brightness_slider"]

    assert presenter.chooseTarget("other")
    assert presenter.setChoice("color_picking_method", "screen_palette")
    pump(lambda: presenter.view["quick_start"]["method_choice"] == "screen_palette")
    assert presenter.chooseTarget("other")  # a second click keeps the user's choice
    pump()
    assert presenter.view["quick_start"]["method"] == "screen_palette"
    assert not presenter.chooseTarget("nowhere")

    source = tmp_path / "source.png"
    image = QImage(24, 16, QImage.Format_RGB32)
    image.fill(QColor("#326ab2"))
    assert image.save(str(source))
    assert presenter.openSource(QUrl.fromLocalFile(str(source)))
    pump(lambda: steps()["image"]["done"] and steps()["area"]["current"])
    assert not steps()["start"]["actions"]  # start stays unavailable until required steps are done

    pages = []
    presenter.pageRequested.connect(pages.append)
    assert presenter.quickStartAction("open_brush") and pages == [5]
    quick.window.setProperty("page", 0)
    assert presenter.quickStartSeen


def test_quick_start_marks_optional_brush_and_ready_start():
    from types import SimpleNamespace
    from application import onboarding
    prep = SimpleNamespace(current_method_id="manual_palette", current_place_id="universal", image_loaded=True,
                           area_selected=True, area_stale=False, required_calibrations=(), can_start=True)
    view = onboarding.build(SimpleNamespace(preparation=prep), SimpleNamespace(), brush_ready=False)
    steps = {step["id"]: step for step in view["steps"]}
    assert view["target"] == "other" and view["current"] == "start" and view["ready"]
    assert steps["brush"]["optional"] and not steps["brush"]["current"]
    assert steps["start"]["actions"][0]["action"] == "start_pause"


def test_quick_start_spray_paint_brush_is_optional_and_explains_hex_row():
    # Owner 2026-10-07: the automatic brush is optional in every place; Spray Paint!
    # draws well with its measured defaults, the speed step is offered as an extra.
    from types import SimpleNamespace
    from application import onboarding
    prep = SimpleNamespace(current_method_id="hex_field", current_place_id="spray_paint", image_loaded=True,
                           area_selected=True, area_stale=False, required_calibrations=(), can_start=True)
    view = onboarding.build(SimpleNamespace(preparation=prep), SimpleNamespace(), brush_ready=False)
    steps = {step["id"]: step for step in view["steps"]}
    assert view["target"] == "spray_paint" and view["current"] == "start"
    assert steps["brush"]["optional"] and "0,1" in steps["brush"]["detail"]
    assert steps["speed"]["optional"]
    assert "стрелку у «Цвет»" in steps["colour"]["detail"]
    assert all(set(target) == {"id", "place", "group", "title", "detail"} for target in view["targets"])


def test_quick_start_explains_optional_steps_and_asks_about_extra_clicks():
    from types import SimpleNamespace
    from application import onboarding
    from ui.quick import translation
    keys = {"global": {"record_pre_color_actions": "F6", "record_post_color_actions": "F7"}}
    played = {"pre": True, "post": False}
    engine = SimpleNamespace(_should_play_actions=lambda slot: played[slot], hex_input_coord=None,
                             pen_stroke_gap=0.0, dynamic_brush_scratch_zone=None)
    service = SimpleNamespace(get_hotkeys=lambda: keys, engine=engine)
    prep = SimpleNamespace(current_method_id="hex_field", current_place_id="universal", image_loaded=True,
                           area_selected=True, area_stale=False, required_calibrations=(), can_start=True)
    build = lambda **kw: {s["id"]: s for s in onboarding.build(SimpleNamespace(preparation=prep), service,
                                                                brush_ready=False, **kw)["steps"]}
    steps = build()
    # «Другая программа»: the colour step itself asks about clicks around a colour
    # change; a newcomer never found them on «Слои и действия» (owner, 2026-10-05).
    extra = steps["colour"]["extra"]
    assert [a["action"] for a in extra["actions"]] == ["record_pre_color_actions", "record_post_color_actions",
                                                       "open_sequences"]
    assert extra["actions"][0]["label"].endswith("✓") and not extra["actions"][1]["label"].endswith("✓")
    assert "нажмите клавишу F6, чтобы закончить запись щелчков до цвета, или F7 — после" in extra["detail"]
    # The optional steps say what they are: an automatic brush and a speed probe.
    assert steps["brush"]["title"] == "Автоматическая кисть" and "регулятор размера" in steps["brush"]["detail"]
    assert steps["speed"]["title"] == "Подбор скорости" and "не теряет штрихи" in steps["speed"]["detail"]
    try:
        translation.activate("en")
        english = translation.view(build(language="en"))
    finally:
        translation.activate("ru")
    assert english["colour"]["extra"]["question"] == "Actions before and after picking a colour"
    assert english["colour"]["extra"]["detail"].startswith("The same as on the “Layers and actions” page.")
    assert "press F6 to finish the clicks before the colour, or F7 for those after" in english["colour"]["extra"]["detail"]
    assert english["brush"]["title"] == "Automatic brush" and english["speed"]["title"] == "Speed tuning"
    # A tuned place keeps its own words but asks about the clicks too (owner, 2026-10-06);
    # Gartic Phone records its clicks in the main row, not twice.
    prep.current_place_id = "speed_draw"
    assert build()["brush"]["title"] == "Размер кисти" and "extra" in build()["colour"]
    prep.current_place_id = "gartic_phone"
    assert "extra" not in build()["colour"]
    assert any(a["action"].startswith("record_") for a in build()["colour"]["actions"])


def test_brush_check_on_screen_shows_the_slider_buttons_and_test_spot():
    from types import SimpleNamespace
    from application import calibration_view
    settings = dict(control_mode="slider", slider_params=("vertical", 40, 300, 100), points=[], coord=None,
                    scratch_zone=(500, 100, 60, 40), enabled=False)
    engine = SimpleNamespace(get_dynamic_brush_settings=lambda: dict(settings))
    items = calibration_view.brush_items(engine)
    assert items[0] == {"type": "line", "x1": 40, "y1": 300, "x2": 40, "y2": 100, "label": "Ползунок кисти"}
    assert items[1] == {"type": "rect", "x": 500, "y": 100, "w": 60, "h": 40, "label": "Пробы"}
    settings.update(control_mode="points", points=[dict(x=10, y=20, value=4.0), dict(x=30, y=20, value=0.5)])
    assert [item["label"] for item in calibration_view.brush_items(engine)] == ["Размер 4", "Размер 0.5", "Пробы"]
    settings.update(control_mode="text", coord=(7, 8), scratch_zone=None)
    assert calibration_view.brush_items(engine) == [{"type": "point", "x": 7, "y": 8, "label": "Размер"}]


def test_quick_start_names_the_assigned_keys_not_the_defaults():
    from types import SimpleNamespace
    from application import onboarding
    from ui.quick import translation
    keys = {"global": {"start_pause": "F6", "stop": "F7", "record_pre_color_actions": "["},
            "app": {"paste_clipboard": "Ctrl+Shift+V"}}
    service = SimpleNamespace(get_hotkeys=lambda: keys,
                              engine=SimpleNamespace(_should_play_actions=lambda slot: False, hex_input_coord=None))
    prep = SimpleNamespace(current_method_id="hex_field", current_place_id="gartic_phone", image_loaded=True,
                           area_selected=True, area_stale=False, required_calibrations=(), can_start=True)
    steps = {s["id"]: s for s in onboarding.build(SimpleNamespace(preparation=prep), service, brush_ready=False)["steps"]}
    assert steps["start"]["detail"] == ("Нажмите «Начать рисование»: окно свернётся на время рисунка. "
                                        "Или нажмите F6 прямо в программе рисования. F7 — остановить.")
    assert "(Ctrl+Shift+V)" in steps["image"]["detail"]
    assert "нажмите клавишу [ (русская Х)." in steps["colour"]["detail"]
    english = onboarding.build(SimpleNamespace(preparation=prep), service, brush_ready=False, language="en")
    try:
        translation.activate("en")
        english = translation.view(english)
    finally:
        translation.activate("ru")
    steps = {s["id"]: s for s in english["steps"]}
    assert steps["start"]["detail"] == ("Press “Start drawing”: the window minimizes while drawing. "
                                        "Or press F6 right in the drawing program. F7 stops.")
    assert "and press [." in steps["colour"]["detail"].splitlines()[0]
    assert steps["colour"]["detail"].splitlines()[2].startswith("3) “Record closing”")
    keys["global"]["record_pre_color_actions"] = ""
    steps = {s["id"]: s for s in onboarding.build(SimpleNamespace(preparation=prep), service, brush_ready=False)["steps"]}
    assert "«Сохранить запись»" in steps["colour"]["detail"] and "{record_pre}" not in steps["colour"]["detail"]


def test_language_and_theme_switch_the_whole_shell(quick):
    from ui.helpers.theme_manager import theme_manager
    presenter = quick.presenter
    sidebar = quick.window.findChild(QQuickItem, "sidebar")
    nav = lambda: visual_item(sidebar, "navigation_0")  # the model rebuilds its rows on retranslation
    try:
        assert presenter.setLanguage("en")
        pump(lambda: presenter.language == "en")
        assert presenter.view["quick_start"]["steps"][0]["title"] == "Picture"
        assert any(group["label"] == "Essentials" for group in presenter.settingGroups)
        pump(lambda: nav().property("title") == "Drawing")
        presenter.setDarkTheme(False)
        assert theme_manager.theme() == "light" and not presenter.darkTheme
    finally:
        presenter.setLanguage("ru")
        presenter.setDarkTheme(True)
    pump(lambda: nav().property("title") == "Рисование")
    assert presenter.view["quick_start"]["steps"][0]["title"] == "Картинка"


def test_translation_covers_composite_and_engine_messages():
    from ui.quick import translation
    try:
        translation.activate("en")
        assert translation.text("Изображение обработано.") == "Image processed."
        assert translation.text("Неизвестная команда: zz") == "Unknown command: zz"
        assert "turn pause 0.1 ms" in translation.text("Скорость подобрана: пауза на повороте 0.1 мс")
        assert translation.text("не из словаря") == "не из словаря"
    finally:
        translation.activate("ru")
    assert translation.text("Рисование") == "Рисование"


def test_screen_tool_hides_window_until_the_tool_really_closes(quick, monkeypatch, tmp_path):
    # Area placement passes through "" (stencil -> "" -> stencil): the window
    # came back over the game 20 ms after hiding (live Spray Paint 2026-09-29).
    service, window = quick.service, quick.window
    image = QImage(40, 30, QImage.Format_RGB32)
    image.fill(Qt.darkCyan)
    assert image.save(str(tmp_path / "picture.png"))
    # the area is placed with the picture: without one the window does not move at all
    assert not quick.presenter.action("select_area") and "откройте картинку" in quick.presenter.message
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(tmp_path / "picture.png")))
    quick.controller.desktop = object()  # restored before the fixture closes the app

    def place_area():
        for mode in ("stencil", "", "stencil"):
            service.set_desktop_interaction(mode)

    monkeypatch.setattr(service, "_global_action_map", lambda: {"select_area": place_area})
    window.show()
    try:
        assert quick.presenter.action("select_area")
        QTest.qWait(600)
        assert not window.isVisible()
        service.set_desktop_interaction("")
        pump(lambda: window.isVisible())
        assert window.isVisible()
    finally:
        quick.controller.desktop = None
        service.set_desktop_interaction("")
        window.show()


@pytest.mark.parametrize("quick", [True], indirect=True)
def test_my_places_keep_a_setup_as_a_quick_start_tile(quick):
    # PLACES-003: a user's own game or program becomes a tile in «Мои места».
    presenter = quick.presenter
    assert presenter.chooseTarget("draw_me")
    assert presenter.setNumber("draw_delay", 0.02)
    assert presenter.saveQuickPlace("Моя игра")
    pump()
    custom = [t for t in presenter.view["quick_start"]["targets"] if t["id"].startswith("custom:")]
    assert [(t["title"], t["group"]) for t in custom] == [("Моя игра", "Мои места")]
    assert presenter.view["quick_start"]["target"] == custom[0]["id"]
    assert presenter.chooseTarget("other")
    assert presenter.setNumber("draw_delay", 0.001)
    assert presenter.chooseTarget(custom[0]["id"])
    pump()
    assert quick.service.engine.draw_delay == pytest.approx(0.02)
    assert presenter.view["quick_start"]["target"] == custom[0]["id"]
    assert not presenter.saveQuickPlace("   ")


def test_viewer_and_settings_preview_show_the_picture(quick, tmp_path):
    # VIEWER-001 / SETTINGS-PREVIEW-001: a closer look and the result beside the settings.
    source = QImage(80, 60, QImage.Format_RGBA8888)
    source.fill(QColor("#326ab2"))
    path = tmp_path / "picture.png"
    assert source.save(str(path))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(path)))
    pump(lambda: bool(quick.presenter.sourceUrl))
    viewer = quick.window.findChild(QObject, "imageViewer")
    viewer.openWith("original")
    pump(lambda: viewer.property("opened"))
    assert viewer.property("mode") == "original"
    viewer.setProperty("zoom", 3)
    assert viewer.property("zoom") == 3
    viewer.setProperty("mode", "compare")
    click = visual_item(viewer.property("contentItem"), "closeViewer")
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=click.mapToScene(QPointF(click.width()/2, click.height()/2)).toPoint())
    pump(lambda: not viewer.property("opened"))
    quick.window.setProperty("page", 1)
    quick.window.resize(1280, 860)
    pump()
    assert visual_item(quick.window.contentItem(), "settingsPreview").isVisible()
    quick.window.resize(960, 700)
    pump()
    assert visual_item(quick.window.contentItem(), "settingsPreviewInline").isVisible()


def test_speed_step_is_done_after_any_speed_probe():
    """Outside straight strokes the probe sets turn and lift pauses, not the stroke
    gap: «Подбор скорости» never became done for Paint (audit 2026-10-05)."""
    from application import onboarding
    engine = SimpleNamespace(_should_play_actions=lambda slot: False, hex_input_coord=None,
                             pen_stroke_gap=0.0, input_timing_measured=False, dynamic_brush_scratch_zone=None)
    service = SimpleNamespace(get_hotkeys=lambda: {"global": {}, "app": {}}, engine=engine,
                              brush_learning_blocker=lambda mode: "Сначала выделите свободное место на холсте для пробных мазков.")
    prep = SimpleNamespace(current_method_id="hex_field", current_place_id="universal", image_loaded=True,
                           area_selected=True, area_stale=False, required_calibrations=(), can_start=True)
    steps = lambda: {s["id"]: s for s in onboarding.build(SimpleNamespace(preparation=prep), service,
                                                           brush_ready=False)["steps"]}
    speed = steps()["speed"]
    assert not speed["done"] and "Пробные линии останутся" in speed["detail"]
    learn = next(a for a in speed["actions"] if a["action"] == "learn_speed")
    assert learn["blocked"].startswith("Сначала выделите")
    engine.input_timing_measured = True
    assert steps()["speed"]["done"]


def test_glued_messages_are_translated_whole():
    """«Готово: Сепия. Отменить — Ctrl+Z…» stayed half Russian in English (audit 2026-10-05)."""
    from ui.quick import translation
    try:
        translation.activate("en")
        assert translation.text("Готово: Сепия. Отменить — Ctrl+Z в «Обработке».") == "Done: Sepia. Undo with Ctrl+Z in “Processing”."
        assert translation.text("Отменено: Фон убран автоматически.") == "Undone: Background removed automatically."
        assert translation.text("Получено: 3. Отмена сохраняет прежние настройки.") == "Received: 3. Cancelling keeps the previous settings."
        assert translation.text("Сепия: не получилось.") == "Sepia: failed."
        assert not re.search("[А-Яа-яЁё]", translation.text("Сначала откройте картинку: место рисования выбирается вместе с ней."))
    finally:
        translation.activate("ru")


def test_processing_done_keeps_the_colour_sliders(quick, tmp_path):
    """«Готово» with moved sliders dropped them silently (audit 2026-10-05)."""
    image = QImage(60, 40, QImage.Format_RGB32)
    image.fill(QColor("#326ab2"))
    assert image.save(str(tmp_path / "picture.png"))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(tmp_path / "picture.png")))
    pump(lambda: not quick.service.edit_busy)
    editor = quick.window.findChild(QObject, "backgroundEditor")
    QMetaObject.invokeMethod(editor, "open")
    pump(lambda: editor.property("opened"))
    editor.setProperty("adjust", {"brightness": 40, "contrast": 0, "saturation": 0, "sharpness": 0})
    done = visual_item(editor.property("contentItem"), "closeBackground")
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=done.mapToScene(QPointF(done.width() / 2, done.height() / 2)).toPoint())
    pump(lambda: not editor.property("opened"))
    pump(lambda: not quick.service.edit_busy and quick.service.has_picture_edits)
    assert quick.service.edit_history()["undo_label"] == "Цвет и свет"


def test_quick_start_reads_english_for_every_place():
    """Steps are built in Python, out of reach of the qsTr check: a changed Russian text
    silently stayed Russian in English (audit 2026-10-05). Labels of a Russian game in
    quotes stay as the game shows them."""
    from application import onboarding
    from ui.quick import translation
    keys = {"global": {"start_pause": "F3", "stop": "F4", "record_pre_color_actions": "F6",
                       "record_post_color_actions": "F7"}, "app": {"paste_clipboard": "Ctrl+V"}}
    engine = SimpleNamespace(_should_play_actions=lambda slot: False, hex_input_coord=None, pen_stroke_gap=0.0,
                             input_timing_measured=False, dynamic_brush_scratch_zone=None, screen_palette_calib=None,
                             wheel_square_calib=None, circle_params_calib=None, slider_params_calib=None)
    service = SimpleNamespace(get_hotkeys=lambda: keys, engine=engine, auto_background="off",
                              brush_learning_blocker=lambda mode: "Сначала выделите свободное место на холсте для пробных мазков.",
                              _is_valid_hsv_circle_calibration=lambda value: False,
                              _is_valid_hsv_slider_calibration=lambda value: False)
    russian = []

    def walk(value):
        if isinstance(value, dict):
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and re.search("[А-Яа-яЁё]", re.sub("“[^”]*”", "", value)):
            russian.append(value)

    try:
        translation.activate("en")
        for target in onboarding.TARGETS:
            for method in ("hex_field", "hsv_palette", "manual_palette", "screen_palette", "wheel_square"):
                for can_start in (True, False):
                    prep = SimpleNamespace(current_method_id=method, current_place_id=target["place"], image_loaded=False,
                                           area_selected=False, area_stale=False, required_calibrations=("x",),
                                           can_start=can_start)
                    walk(translation.view(onboarding.build(SimpleNamespace(preparation=prep), service,
                                                           brush_ready=False, language="en")))
    finally:
        translation.activate("ru")
    assert not russian, sorted(set(russian))[:5]


def test_update_banner_and_card_lead_to_the_new_version(quick, tmp_path, monkeypatch):
    """UPDATE-001: a check finds a new version, the banner leads to «Помощь», «Обновить»
    downloads it and «Перезапустить и обновить» hands over and closes the window."""
    from PySide6.QtCore import QSettings, QTimer
    from application import updates
    release = updates.parse_release(dict(tag_name="v9.0", body="- новое", assets=[dict(
        name="OlegPainter-9.0-win64.zip", size=2 * 2**20, browser_download_url="https://example.invalid/a.zip")]))
    launched, closed = [], []
    presenter = quick.presenter
    center = updates.UpdateCenter(
        "1.4", tmp_path / "OlegPainter", frozen=True, work_dir=tmp_path / "updates",
        notify=lambda: QTimer.singleShot(0, presenter, presenter._update_changed),
        fetch=lambda: release, fetch_archive=lambda r, progress, cancelled: tmp_path / "a.zip",
        unpack=lambda archive, progress, cancelled: tmp_path / "new", launch=launched.append, writable=lambda f: True)
    monkeypatch.setattr(presenter, "_updates", center)
    monkeypatch.setattr(presenter, "closeApplication", lambda: closed.append(True) or True)
    try:
        assert presenter.checkUpdates()
        pump(lambda: presenter.updateInfo["state"] == "available")
        banner = visual_item(quick.window.contentItem(), "updateBanner")
        pump(lambda: banner.isVisible())
        QTest.mouseClick(quick.window, Qt.LeftButton, pos=visual_item(banner, "updateBannerOpen").mapToScene(QPointF(10, 10)).toPoint())
        pump(lambda: quick.window.property("page") == 9)
        download = visual_item(quick.window.contentItem(), "downloadUpdate")
        pump(lambda: download.isVisible())
        assert "9.0" in download.property("text") and "2 МБ" in download.property("text")
        assert presenter.downloadUpdate()
        pump(lambda: presenter.updateInfo["state"] == "ready")
        install = visual_item(quick.window.contentItem(), "installUpdate")
        pump(lambda: install.isVisible())
        assert presenter.installUpdate() and closed == [True]
        assert launched and launched[0][1] == updates.INSTALL_FLAG
        # «Не сейчас» puts this version aside: the banner stays away until a newer one
        quick.window.setProperty("page", 0)
        monkeypatch.setattr(center, "state", "available")
        presenter.dismissUpdate()
        pump(lambda: not banner.isVisible())
        assert QSettings().value("updates/dismissed") == "9.0"
    finally:
        QSettings().remove("updates/dismissed")


def test_quick_start_steps_carry_the_places_guide_clip():
    from application import onboarding
    prep = SimpleNamespace(current_method_id="hex_field", current_place_id="rust_sign", image_loaded=True,
                           area_selected=True, area_stale=False, required_calibrations=(), can_start=True)
    steps = {s["id"]: s for s in onboarding.build(SimpleNamespace(preparation=prep), SimpleNamespace(),
                                                    brush_ready=False)["steps"]}
    for step in ("image", "area", "colour", "start"):
        assert steps[step]["hint"].startswith("file:") and steps[step]["hint"].endswith(f"/rust_sign/{step}.webp")
    # the optional automatic brush is in no guide: nothing to show (owner, 2026-10-08)
    assert steps["brush"]["hint"] == "" and steps["brush"]["guide_url"] == ""
    # «Смотреть в гайде» opens the whole guide at the step
    assert steps["colour"]["guide_url"] == "https://youtu.be/hRVb8BMvJvQ?t=37"
    other = SimpleNamespace(**{**vars(prep), "current_place_id": "universal"})
    assert all(s["hint"] == "" for s in onboarding.build(SimpleNamespace(preparation=other), SimpleNamespace(),
                                                         brush_ready=False)["steps"])
    # every clip is a playable animation of the size the popup expects
    from PIL import Image
    from app_paths import get_app_paths
    clips = list((get_app_paths().assets_root / "hints").glob("*/*.webp"))
    assert len(clips) == 24 and not any(clip.stem == "brush" for clip in clips)
    for clip in clips:
        with Image.open(clip) as img:
            assert img.is_animated and img.n_frames >= 10 and img.size == (640, 360), clip
