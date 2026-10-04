"""User-reported settings failures through real QML, with no native input."""
from unittest.mock import Mock

import pytest
from PySide6.QtCore import QPointF, QUrl, Qt
from PySide6.QtGui import QImage, QColor, QPainter
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from application.settings import SETTINGS, GROUPS
from engine.olegpainter.drawing_events import DrawingEvent, DrawingPhase
from tests.test_quick_presentation import quick, pump, visual_item  # noqa: F401


TIMINGS = ("draw_delay", "pen_button_delay", "pen_settle_delay")
# Input pacing the user may correct while paused: the timings and the press nudge.
PAUSE_EDITABLE = TIMINGS + ("pen_press_nudge",)


def show_group_of(quick, key):
    """Advanced settings open the group that holds `key` (SETTINGS-004)."""
    quick.presenter.setAdvancedSettings(True)
    index = next(i for i, (_, _, settings) in enumerate(GROUPS) if any(s.key == key for s in settings))
    quick.window.findChild(QQuickItem, "settingsGroup").setProperty("currentIndex", index)
    pump()


@pytest.mark.parametrize("key", TIMINGS)
def test_paused_timing_slider_applies_without_repreparing(quick, monkeypatch, key):
    quick.window.setProperty("page", 1)
    show_group_of(quick, key)
    assert quick.presenter.setSetting(key, .02)
    service, engine = quick.service, quick.service.engine
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.RUNNING))
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.PAUSED))
    quick.presenter.refresh()
    slider = visual_item(quick.window.contentItem(), "setting_" + key)
    field = visual_item(quick.window.contentItem(), "value_" + key)
    assert slider.isEnabled() and field.isEnabled()
    editability = quick.presenter.view["setting_editability"]
    assert not editability["brush_size"] and not editability["k_clusters"] and not editability["mode"]
    hint = quick.window.findChild(QQuickItem, "timingPauseHint")
    assert hint.isVisible() and "На паузе" in hint.property("text")
    writes = Mock(wraps=getattr(service, "set_" + key))
    rebuild = Mock()
    monkeypatch.setattr(service, "set_" + key, writes)
    monkeypatch.setattr(service, "_invalidate_visuals", rebuild)
    before = (engine.cluster_map, engine.drawn_mask, quick.presenter.previewUrl)
    reveal(quick.window, slider)
    drag(quick.window, slider, field, .02, .06, lambda: getattr(engine, key), writes, return_to_start=False)
    assert service.snapshot_painter_config()[key] == getattr(engine, key)
    assert engine.cluster_map is before[0] and engine.drawn_mask is before[1]
    assert quick.presenter.previewUrl == before[2]
    assert service.drawing_phase == DrawingPhase.PAUSED
    assert service._drawing_run_id == 0
    rebuild.assert_not_called()
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.RUNNING))
    quick.presenter.refresh()
    assert not slider.isEnabled()
    assert not quick.presenter.setSetting(key, .03)  # late release after resume
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.STOPPED))


@pytest.mark.parametrize("phase", [DrawingPhase.RUNNING, DrawingPhase.PAUSED, DrawingPhase.STOPPING])
def test_active_drawing_only_allows_three_timings_on_pause(quick, phase):
    service = quick.service
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.RUNNING))
    service._on_drawing_event(DrawingEvent(0, phase))
    quick.presenter.refresh()
    values = service.snapshot_painter_config()
    for key in SETTINGS:
        expected = phase == DrawingPhase.PAUSED and key in PAUSE_EDITABLE
        assert quick.presenter.view["setting_editability"][key] == expected
        assert quick.presenter.setSetting(key, values[key]) == expected, key
    assert service.snapshot_painter_config() == values
    service._on_drawing_event(DrawingEvent(0, DrawingPhase.STOPPED))


def test_paused_timing_permission_does_not_bypass_other_exclusions(quick):
    from dataclasses import replace
    from application.state import CaptureState
    state = replace(quick.controller.state, drawing=DrawingPhase.PAUSED)
    for key in TIMINGS:
        assert SETTINGS[key].is_editable(state)
        for exclusion in (dict(closing=True), dict(brush_learning=True), dict(hotkey_capture=True),
                          dict(desktop_mode="stencil"), dict(capture=CaptureState(active=True))):
            assert not SETTINGS[key].is_editable(replace(state, **exclusion))


def test_real_worker_resumes_with_new_timings_and_preserves_prepared_image(quick, tmp_path, monkeypatch):
    from threading import Event

    service, engine = quick.service, quick.service.engine
    image = QImage(12, 12, QImage.Format_RGB32)
    image.fill(QColor("#326ab2"))
    path = tmp_path / "timing-drawing.png"
    assert image.save(str(path))
    assert quick.presenter.selectProfile("universal", "dfs_4dir")
    assert quick.presenter.setSetting("color_picking_method", "hex_field")
    assert quick.presenter.setSetting("k_clusters", 1)
    assert quick.presenter.setSetting("brush_size", 1)
    engine.hex_input_coord = (100, 100)
    service.apply_viewport_state(draw_region_desktop_px=(0, 0, 12, 12))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(path)))
    pump(lambda: service._preview_thread is None and quick.presenter.view["can_start"])
    engine.dynamic_brush_enabled = engine.post_draw_repair_enabled = False
    engine.run_length_merge_enabled = engine.euler_greedy_pairing_enabled = False
    monkeypatch.setattr(engine, "pick_color", lambda *args: None)
    # Only the device is fake (the suite's RecordingInputBackend). Hold the first
    # move so the real command worker can pause before the small drawing finishes.
    entered, release, pen_released = Event(), Event(), Event()
    move = engine._input.backend.move
    button = engine._input.backend.button
    def first_move(x, y):
        if not entered.is_set():
            entered.set()
            assert release.wait(4)
        move(x, y)
    def track_button(name, down):
        button(name, down)
        if not down and not engine.drawing_enabled:
            pen_released.set()
    monkeypatch.setattr(engine._input.backend, "move", first_move)
    monkeypatch.setattr(engine._input.backend, "button", track_button)
    waits = []
    sleep = engine._sleep_with_abort
    def track_wait(duration, *args, **kwargs):
        waits.append(duration)
        return sleep(duration, *args, **kwargs)
    monkeypatch.setattr(engine, "_sleep_with_abort", track_wait)
    try:
        service.start_pause()
        pump(lambda: entered.is_set() and not service._thread_is_running(service._worker_thread))
        service.start_pause()
        pump(lambda: service.drawing_phase == DrawingPhase.PAUSED)
        release.set()
        pump(pen_released.is_set)
        pump(lambda: not service._thread_is_running(service._worker_thread))
        prepared, drawn = engine.cluster_map, engine.drawn_mask
        completed_cells = int(drawn.sum())
        run = service._drawing_run_id
        assert 0 < completed_cells < prepared.size
        for key, value in zip(TIMINGS, (.0015, .0025, .0035)):
            assert quick.presenter.setSetting(key, value)
        assert engine.cluster_map is prepared and engine.drawn_mask is drawn
        assert int(drawn.sum()) == completed_cells
        waits.clear()
        service.start_pause()
        pump(lambda: service.drawing_phase == DrawingPhase.COMPLETED)
        assert service._drawing_run_id == run
        assert drawn[prepared != -1].all()
        assert .0015 in waits and .0025 in waits
        assert engine.pen_settle_delay == .0035
    finally:
        release.set()


def reveal(window, item, scroll=None):
    scroll = scroll or window.findChild(QQuickItem, "pagesScroll")
    QTest.qWait(50)
    flickable = scroll.property("contentItem")
    delta = item.mapToItem(scroll, QPointF()).y() - scroll.height() / 2
    flickable.setProperty("contentY", max(0, min(flickable.property("contentY") + delta,
                                               flickable.property("contentHeight") - flickable.height())))
    QTest.qWait(40)


def point(slider, value):
    fraction = (value - slider.property("from")) / (slider.property("to") - slider.property("from"))
    handle_width = slider.property("handle").width()
    x = slider.property("leftPadding") + handle_width / 2 + fraction * (slider.property("availableWidth") - handle_width)
    return slider.mapToScene(QPointF(x, slider.height() / 2)).toPoint()


def drag(window, slider, field, before, changed, read_saved, writes, *, return_to_start):
    QTest.mousePress(window, Qt.LeftButton, pos=point(slider, before))
    QTest.mouseMove(window, point(slider, changed), delay=30)
    pump()
    assert slider.property("pressed")
    assert float(field.property("text")) == slider.property("draftValue")
    assert float(field.property("text")) != before
    assert read_saved() == before
    writes.assert_not_called()
    if return_to_start:
        QTest.mouseMove(window, point(slider, before), delay=30)
        pump()
    target = slider.property("draftValue")
    QTest.mouseRelease(window, Qt.LeftButton, pos=point(slider, before if return_to_start else changed))
    pump()
    assert read_saved() == (before if return_to_start else target)
    assert writes.call_count == (0 if return_to_start else 1)


@pytest.mark.parametrize("key,before,changed", [("brush_size", 42, 65), ("draw_delay", .02, .06)])
@pytest.mark.parametrize("return_to_start", [True, False])
def test_settings_slider_live_number_and_single_changed_commit(quick, monkeypatch, key, before, changed, return_to_start):
    quick.window.setProperty("page", 1)
    assert quick.presenter.setSetting(key, before)
    slider = visual_item(quick.window.contentItem(), "setting_" + key)
    field = visual_item(quick.window.contentItem(), "value_" + key)
    original = getattr(quick.service, "set_" + key)
    writes = Mock(wraps=original)
    monkeypatch.setattr(quick.service, "set_" + key, writes)
    reveal(quick.window, slider)
    drag(quick.window, slider, field, before, changed, lambda: getattr(quick.service.engine, key), writes,
         return_to_start=return_to_start)


@pytest.mark.parametrize("return_to_start", [True, False])
def test_background_slider_uses_same_draft_commit_contract(quick, monkeypatch, return_to_start):
    from PySide6.QtCore import QObject
    popup = quick.window.findChild(QObject, "backgroundEditor")
    quick.presenter.setBackground({"enabled": True, "color_tolerance": 30})    # «По цвету»
    popup.open()
    slider = visual_item(popup.property("contentItem"), "backgroundThreshold")
    field = visual_item(popup.property("contentItem"), "backgroundThresholdValue")
    writes = Mock(wraps=quick.service.set_background_removal_state)
    monkeypatch.setattr(quick.service, "set_background_removal_state", writes)
    QTest.qWait(80)
    drag(quick.window, slider, field, 30, 90, lambda: quick.service.engine.background_color_tolerance,
         writes, return_to_start=return_to_start)
    popup.close()


def test_same_setting_does_not_invalidate_preview_or_session(quick, monkeypatch):
    writes = Mock()
    monkeypatch.setattr(quick.service, "set_brush_size", writes)
    assert quick.presenter.setSetting("brush_size", quick.service.engine.brush_size)
    writes.assert_not_called()


def test_nearest_area_order_reaches_engine_and_roundtrips_preset(quick):
    assert quick.presenter.setSetting("area_sequence", "nearest")
    assert quick.service.engine.area_sequence == "nearest"
    assert quick.presenter.preset("save", "Nearest areas")
    slug = quick.presenter.view["presets"][0]["slug"]
    assert quick.presenter.setSetting("area_sequence", "large_to_small")
    assert quick.presenter.preset("apply", slug)
    assert quick.service.engine.area_sequence == "nearest"


@pytest.mark.parametrize("space", ["oklab", "cielab"])
@pytest.mark.parametrize("display_threshold", [0, 1, 2, 5])
def test_small_display_threshold_preserves_distinct_colors_in_real_preparation(quick, tmp_path, space, display_threshold):
    for key, value in dict(mode="color", prep_color_space=space, k_clusters=3, brush_size=1,
                           fast_min_region_area=0, prep_cleanup_mode="off", prep_preserve_accents=False).items():
        assert quick.presenter.setSetting(key, value)
    assert quick.presenter.setDisplayedSetting("color_merge_threshold", display_threshold)
    assert quick.service.engine.color_merge_threshold == display_threshold / 100
    image = QImage(48, 16, QImage.Format_RGB32)
    painter = QPainter(image)
    for i, color in enumerate(("#FF0000", "#00FF00", "#0000FF")):
        painter.fillRect(i * 16, 0, 16, 16, QColor(color))
    painter.end()
    path = tmp_path / "colors.png"
    assert image.save(str(path))
    quick.service.apply_viewport_state(draw_region_desktop_px=(0, 0, 48, 16))
    assert quick.presenter.openSource(QUrl.fromLocalFile(str(path)))
    pump(lambda: quick.presenter.view["preview_ready"] and quick.service._preview_thread is None)
    palette = quick.service.engine.color_palette
    assert len(palette) == 3
    assert all(max(rgb) - min(rgb) > 100 for _, _, _, rgb in palette)


def test_saved_merge_threshold_keeps_legacy_units(quick):
    assert quick.presenter.setSetting("color_merge_threshold", .05)
    assert quick.presenter.preset("save", "Merge units")
    slug = quick.presenter.view["presets"][0]["slug"]
    assert quick.presenter.setDisplayedSetting("color_merge_threshold", 1)
    assert quick.presenter.preset("apply", slug)
    assert quick.service.snapshot_painter_config()["color_merge_threshold"] == .05
    quick.window.setProperty("page", 1)
    show_group_of(quick, "color_merge_threshold")
    QTest.qWait(40)
    field = visual_item(quick.window.contentItem(), "value_color_merge_threshold")
    assert float(field.property("text")) == 5


def test_close_shades_still_merge_at_moderate_threshold():
    import numpy as np
    from engine.olegpainter.prep_pipeline import _to_cluster_space, merge_similar_clusters
    rgb = np.array([[100, 100, 100], [101, 101, 101], [240, 20, 20]], dtype=np.uint8)
    centers = _to_cluster_space(rgb, "oklab")
    labels = np.array([0, 0, 1, 1, 2, 2])
    unchanged, _, _ = merge_similar_clusters(labels, centers, 0, "oklab")
    merged, _, _ = merge_similar_clusters(labels, centers, .05, "oklab")
    assert len(np.unique(unchanged)) == 3
    assert len(np.unique(merged)) == 2
    assert merged[0] == merged[2] and merged[0] != merged[4]


def test_setting_help_can_be_opened_and_read_in_narrow_window(quick):
    from PySide6.QtCore import QObject
    quick.window.resize(360, 640)
    quick.window.setProperty("page", 1)
    button = visual_item(quick.window.contentItem(), "explain_draw_delay")
    reveal(quick.window, button)
    position = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
    QTest.mouseMove(quick.window, position)
    QTest.qWait(450)
    tip = button.findChild(QObject, "settingTooltip")
    assert tip.property("visible") and tip.property("width") <= 336
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=position)
    explanation = visual_item(quick.window.contentItem(), "help_draw_delay")
    pump(lambda: explanation.isVisible())
    QTest.qWait(60)  # Visibility precedes the Qt Quick layout/polish pass.
    assert "миллисекунд" in explanation.property("text")
    assert explanation.width() <= 336


def test_categories_and_conditional_bw_settings_are_discoverable(quick):
    quick.window.setProperty("page", 1)
    assert not quick.window.findChild(QQuickItem, "settingsGroup").isVisible()  # newcomers: «Основное» only
    quick.presenter.setAdvancedSettings(True)
    pump()
    for group in quick.presenter.settingGroups:
        button = visual_item(quick.window.contentItem(), "settingsCategory_" + group["id"])
        assert button is not None and button.isVisible()
    assert quick.presenter.setSetting("mode", "color")
    bw = visual_item(quick.window.contentItem(), "setting_bw_draw_mode")
    assert not bw.isVisible()
    assert quick.presenter.setSetting("mode", "bw")
    pump(lambda: bw.isVisible())
    assert quick.presenter.setSetting("mode", "color")
    pump(lambda: not bw.isVisible())
    assert all(setting.descriptor()["help"] for setting in SETTINGS.values())


def test_empty_saved_hud_recovers_information_and_receives_live_stats(quick):
    from ui.widgets.hud_overlay import _BLOCK_ORDER, _DEFAULT_VISIBLE
    from engine.olegpainter.drawing_events import DrawingPhase
    hud = quick.controller.enable_desktop().hud_overlay
    hud.restore_state({"layout_version": 2, "hidden_blocks": list(_BLOCK_ORDER), "visible": False})
    assert {key for key, block in hud._blocks.items() if not block.isHidden()} == _DEFAULT_VISIBLE
    assert not hud.isVisible()
    quick.service.drawing_phase = DrawingPhase.RUNNING
    quick.controller.refresh_preparation()
    quick.service.drawingStatsChanged.emit({"percent": 42, "colors_done": 3, "colors_total": 7,
                                           "elapsed_seconds": 12, "eta_seconds": 18})
    from PySide6.QtGui import QTextDocument

    def shown(key):
        # What the user reads, not the markup: number and unit are styled apart.
        document = QTextDocument()
        document.setHtml(hud._blocks[key]._value.text())
        return "".join(document.toPlainText().split())

    assert "42%" in shown("progress")
    assert "3/7" in shown("colors")
    assert "00:12" in hud._blocks["time"]._value.text()
    state = hud.export_state()
    hud.restore_state(state)
    assert hud.export_state()["hidden_blocks"] == state["hidden_blocks"]
    quick.service.drawing_phase = DrawingPhase.IDLE
    quick.controller.refresh_preparation()
