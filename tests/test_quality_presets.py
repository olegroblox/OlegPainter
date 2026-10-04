"""Quality presets «Быстро / Баланс / Точно» (PRESETS-001) and the essentials view (SETTINGS-004)."""
import pytest
from PySide6.QtCore import QPointF, QSettings, Qt
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest

from application import presets
from application.settings import GROUPS, Setting
from tests.test_application_controller import controllers  # noqa: F401
from tests.test_quick_presentation import quick, pump, visual_item  # noqa: F401


def test_fresh_settings_are_the_balanced_preset(controllers):
    controller = controllers()
    assert presets.detect(controller.service.snapshot_painter_config()) == "balanced"


def test_preset_changes_only_its_own_settings(controllers):
    controller = controllers()
    before = controller.service.snapshot_painter_config()
    presets.apply(controller, "precise")
    after = controller.service.snapshot_painter_config()
    precise = next(p for p in presets.QUALITY_PRESETS if p["id"] == "precise")
    assert {key: after[key] for key in precise["values"]} == precise["values"]
    changed = {key for key in after if after[key] != before.get(key)}
    assert changed <= set(precise["values"])
    assert presets.detect(after) == "precise"
    controller.service.set_k_clusters(20)
    assert presets.detect(controller.service.snapshot_painter_config()) == "custom"


def test_unknown_or_locked_preset_changes_nothing(controllers, monkeypatch):
    controller = controllers()
    before = controller.service.snapshot_painter_config()
    with pytest.raises(ValueError):
        presets.apply(controller, "turbo")
    # One locked setting blocks the whole preset instead of half-applying it.
    original = Setting.is_editable
    monkeypatch.setattr(Setting, "is_editable",
                        lambda self, state: self.key != "post_draw_repair_enabled" and original(self, state))
    with pytest.raises(RuntimeError):
        presets.apply(controller, "fast")
    assert controller.service.snapshot_painter_config() == before


def _click(window, item):
    QTest.qWait(35)
    position = item.mapToScene(QPointF(item.width() / 2, item.height() / 2)).toPoint()
    assert 0 <= position.x() < window.width() and 0 <= position.y() < window.height()
    QTest.mouseClick(window, Qt.LeftButton, pos=position)
    pump()


def test_settings_tiles_and_drawing_page_share_one_preset(quick):
    window, presenter = quick.window, quick.presenter
    choice = window.findChild(QQuickItem, "drawingQuality")
    assert presenter.view["quality_preset"] == "balanced"
    assert choice.property("currentValue") == "balanced"
    window.setProperty("page", 1)
    _click(window, visual_item(window.contentItem(), "qualityPreset_fast"))
    pump(lambda: presenter.view["quality_preset"] == "fast")
    assert presenter.view["settings"]["k_clusters"] == 8
    assert visual_item(window.contentItem(), "qualityPreset_fast").property("selected")
    window.setProperty("page", 0)
    pump(lambda: choice.property("currentValue") == "fast")
    # Choices on the drawing page apply at once: «Баланс», then «Точно».
    choice.forceActiveFocus()
    QTest.keyClick(window, Qt.Key_Down)
    pump(lambda: presenter.view["quality_preset"] == "balanced")
    QTest.keyClick(window, Qt.Key_Down)
    pump(lambda: presenter.view["quality_preset"] == "precise")
    assert presenter.view["settings"]["tone_sequence"] == "details_last"
    # Any manual change shows «Свои настройки» instead of a wrong preset.
    presenter.setNumber("k_clusters", 20)
    pump(lambda: presenter.view["quality_preset"] == "custom")
    pump(lambda: choice.property("currentValue") == "custom")
    assert "custom" not in [p["id"] for p in presenter.qualityPresets]


def test_advanced_switch_reveals_groups_and_is_remembered(quick):
    window, presenter = quick.window, quick.presenter
    window.setProperty("page", 1)
    pump()
    groups = window.findChild(QQuickItem, "settingsGroup")
    assert not groups.isVisible()
    essentials = {s.key for s in GROUPS[0][2]}
    assert visual_item(window.contentItem(), "setting_k_clusters") is not None
    assert "prep_color_space" not in essentials
    assert visual_item(window.contentItem(), "setting_prep_color_space") is None
    _click(window, window.findChild(QQuickItem, "advancedSettings"))
    pump(lambda: presenter.advancedSettings)
    assert groups.isVisible()
    assert QSettings().value("quick/advancedSettings", False, type=bool)
