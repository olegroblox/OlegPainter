"""Manual mixing settings and real Qt pickers without system input."""
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QPointF, Qt
from PySide6.QtWidgets import QApplication
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtQuick import QQuickItem
from PySide6.QtTest import QTest
from PIL import Image

from application.color_mixing import alpha_slider_from_points, valid_alpha_slider
from ui.helpers.config_store import ConfigStore
from ui.helpers.config_payload import collect_payload
from ui.quick.application import QuickApplication
from ui.overlays.region_pick import _BasePickOverlay
from tests.test_quick_presentation import pump, visual_item


@pytest.fixture
def quick(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Basic")
    monkeypatch.setattr(_BasePickOverlay, "_start_listeners", lambda _: None)
    quick = QuickApplication(config_store=ConfigStore(tmp_path / "profiles"), desktop=True)
    quick.presenter.setChoice("color_picking_method", "manual_palette")
    yield quick
    quick.dispose(save=False)
    assert not quick.qml_warnings
    quick.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


@pytest.mark.parametrize("points,expected", [
    (((-100, 20), (100, 20)), ("horizontal", 20, 100, -100)),
    (((100, 20), (-100, 20)), ("horizontal", 20, -100, 100)),
    (((10, 200), (10, 100)), ("vertical", 10, 100, 200)),
    (((10, 100), (10, 200)), ("vertical", 10, 200, 100)),
])
def test_endpoint_directions_reach_correct_input_coordinates(quick, monkeypatch, points, expected):
    engine = quick.service.engine
    result = alpha_slider_from_points(points)
    assert result == expected and valid_alpha_slider(result)
    engine.alpha_slider_params = result
    clicks = []
    monkeypatch.setattr(engine, "_click_abs", lambda x, y: clicks.append((x, y)))
    monkeypatch.setattr(engine, "_input_click", lambda **_: None)
    monkeypatch.setattr(engine, "_sleep_with_abort", lambda *_: True)
    for alpha in (0, .5, 1):
        assert engine._set_alpha_slider_value(alpha)
    assert clicks == [points[0], tuple((a + b) // 2 for a, b in zip(*points)), points[1]]


def test_alpha_picker_release_commit_and_profile_roundtrip(quick):
    assert quick.presenter.action("calibrate_alpha_slider")
    overlays = quick.controller.desktop.desktop_overlays
    picker = overlays.picker
    assert not quick.window.isVisible()
    session = picker._capture_session
    picker._handle_event("down", (-120, 50))
    picker._handle_event("up", (-120, 50))
    picker._handle_event("down", (120, 50))
    assert quick.service.engine.alpha_slider_params is None
    assert not session.closed
    picker._handle_event("up", (120, 50))
    pump(lambda: quick.presenter.view["manual_mix"]["calibrated"])
    assert quick.window.isVisible() and session.closed
    assert quick.presenter.setManualMix(dict(enabled=True, canvas_hex="#102030"))
    assert quick.presenter.preset("save", "Mix")
    slug = quick.presenter.view["presets"][0]["slug"]
    quick.service.engine.alpha_slider_params = None
    assert quick.presenter.setManualMix(dict(enabled=False, canvas_hex="#FFFFFF"))
    assert quick.presenter.preset("apply", slug)
    assert quick.service.engine.alpha_slider_params == ("horizontal", 50, 120, -120)
    assert quick.presenter.view["manual_mix"]["enabled"]
    assert quick.presenter.view["manual_mix"]["canvas_hex"] == "#102030"
    palette_only = collect_payload(quick.service, None, {"palette"}, strict=True)["painter"]
    assert palette_only["manual_mix_canvas_rgb"] == [16, 32, 48]


@pytest.mark.parametrize("finish", ["cancel", "stop", "bad_points", "stale", "close", "listener_error"])
def test_cancel_error_or_stale_alpha_keeps_old_settings(quick, monkeypatch, finish):
    engine = quick.service.engine
    previous = ("vertical", 12, 30, 90)
    engine.alpha_slider_params = previous
    if finish == "listener_error":
        def fail(_): raise RuntimeError("input unavailable")
        monkeypatch.setattr(_BasePickOverlay, "_start_listeners", fail)
    assert quick.presenter.action("calibrate_alpha_slider")
    overlays = quick.controller.desktop.desktop_overlays
    picker = overlays.picker
    if finish == "listener_error":
        assert picker is None and quick.window.isVisible()
    else:
        picker._handle_event("down", (10, 10))
        picker._handle_event("up", (10, 10))
        if finish == "cancel":
            picker.cancel()
        elif finish == "stop":
            assert quick.presenter.action("stop")
            pump(lambda: not quick.service._stop_pending)
        elif finish == "close":
            quick.presenter.closeApplication()
            pump(lambda: quick.controller._closed)
        else:
            if finish == "stale":
                engine.color_picking_method = "hex_field"
            picker._handle_event("down", (100, 100))
            picker._handle_event("up", (100, 100))
            assert quick.presenter.messageError
        pump(lambda: overlays.picker is None)
    assert engine.alpha_slider_params == previous


@pytest.mark.parametrize("patch", [dict(enabled=True, canvas_hex="wrong"), dict(alpha=float("nan")),
                                  dict(enabled="yes"), dict(alpha=2), dict(unknown=1)])
def test_bad_patch_does_not_partially_change_mixing(quick, patch):
    before = quick.service.manual_mix_snapshot()
    assert not quick.presenter.setManualMix(patch)
    assert quick.service.manual_mix_snapshot() == before
    assert quick.presenter.messageError


def test_mixing_updates_preview_and_blocks_mutations_while_busy(quick, monkeypatch):
    changes = []
    monkeypatch.setattr(quick.service, "_invalidate_heavy_preview", lambda **_: changes.append(1))
    assert quick.presenter.setManualMix(dict(enabled=True, canvas_hex="#000000"))
    assert changes
    assert quick.presenter.action("calibrate_alpha_slider")
    assert not quick.presenter.setManualMix(dict(enabled=False))
    assert quick.service.engine.manual_palette_mix_enabled
    quick.controller.desktop.desktop_overlays.picker.cancel()


def test_preflight_requires_alpha_only_for_manual_mixing(quick):
    service, engine = quick.service, quick.service.engine
    engine.source_pil_image = Image.new("RGB", (8, 8), "red")
    service.apply_viewport_state(draw_region_desktop_px=(10, 10, 8, 8))
    engine.manual_palette_coords = [dict(x=1, y=2, rgb=[255, 0, 0], hex="#FF0000")]
    engine.manual_palette_mix_enabled = True
    service._emit_manual_palette_changed()
    quick.presenter.refresh()
    assert "calibrate_alpha_slider" in service.get_preparation_snapshot()["missing_action_codes"]
    assert not service._preflight_check()[0]
    assert quick.presenter.view["next_step"]["action"] == "calibrate_alpha_slider"
    engine.alpha_slider_params = ("horizontal", 10, 100, 0)
    assert "calibrate_alpha_slider" not in service.get_preparation_snapshot()["missing_action_codes"]
    engine.alpha_slider_params = None
    engine.manual_palette_mix_enabled = False
    assert "calibrate_alpha_slider" not in service.get_preparation_snapshot()["missing_action_codes"]
    engine.manual_palette_mix_enabled = True
    engine.color_picking_method = "hex_field"
    assert "calibrate_alpha_slider" not in service.get_preparation_snapshot()["missing_action_codes"]


@pytest.mark.parametrize("value", [None, ["horizontal", 10, 10, 10], ["diagonal", 0, 10, 20],
                                  ["vertical", float("nan"), 100, 0]])
def test_loading_uncalibrated_profile_clears_previous_alpha(quick, value):
    engine = quick.service.engine
    engine.alpha_slider_params = ("horizontal", 5, 100, 0)
    assert engine.load_config({"alpha_slider_params": value})
    assert engine.alpha_slider_params is None


def test_qml_mixing_toggle_and_hex_apply(quick):
    quick.window.setProperty("page", 4)
    pump()
    checkbox = visual_item(quick.window.contentItem(), "mixEnabled")
    QTest.qWait(60)
    QTest.mouseClick(quick.window, Qt.LeftButton,
                    pos=checkbox.mapToScene(QPointF(15, checkbox.height() / 2)).toPoint())
    pump(lambda: quick.presenter.view["manual_mix"]["enabled"])
    field = quick.window.findChild(QQuickItem, "mixCanvasColor")
    field.setProperty("text", "#ABCDEF")
    field.accepted.emit()
    pump(lambda: quick.presenter.view["manual_mix"]["canvas_hex"] == "#ABCDEF")
    assert quick.window.findChild(QQuickItem, "calibrateAlphaButton").isEnabled()
    quick.window.resize(960, 640)
    QTest.qWait(60)
    scroll = quick.window.findChild(QQuickItem, "pagesScroll")
    flickable = scroll.property("contentItem")
    flickable.setProperty("contentY", max(0, flickable.property("contentHeight") - flickable.height()))
    QTest.qWait(60)
    button = quick.window.findChild(QQuickItem, "saveMixCanvas")
    point = button.mapToScene(QPointF(button.width() / 2, button.height() / 2)).toPoint()
    assert 0 < point.y() < quick.window.height()
    field.setProperty("text", "#112233")
    QTest.mouseClick(quick.window, Qt.LeftButton, pos=point)
    pump(lambda: quick.presenter.view["manual_mix"]["canvas_hex"] == "#112233")
