"""Real engine/session scenarios without constructing the legacy UI."""
from pathlib import Path

import pytest
from PIL import Image
from PySide6.QtWidgets import QApplication

from application.controller import ApplicationController
from infrastructure.documents import read_document
from ui.helpers.config_store import ConfigStore
from ui.services.painter_service import PainterService


@pytest.fixture
def controllers(tmp_path):
    app = QApplication.instance() or QApplication([])
    created = []
    def make(directory=None):
        service = PainterService()
        # Profile/session tests exercise actual engine data, not preview scheduling.
        service._rebuild_preview_if_possible = lambda: None
        service._rebuild_preview_strict = lambda: None
        controller = ApplicationController(service, config_store=ConfigStore(directory or tmp_path))
        created.append((controller, service))
        return controller
    yield make
    for controller, service in reversed(created):
        controller.close(save=False)
        service.shutdown()
        controller.deleteLater()
        service.deleteLater()
    app.processEvents()


def test_controller_needs_no_main_window_or_settings_page(controllers):
    before = set(QApplication.allWidgets())
    controller = controllers()
    assert controller.preparation.current_method_id == "manual_palette"  # «Другая программа» by default
    controller.profiles.select("universal", "outline_and_paint")
    controller.service.set_brush_size(7)
    controller.update_shell_state({"theme": "dark", "selected_page": "draw"})
    assert controller.save()
    assert controller.preparation.current_place_id == "universal"
    assert controller.preparation.current_method_id == "manual_palette"
    # Saving services queued deletions from earlier tests. Only new widgets
    # violate this contract; disappearance of an older widget is legitimate.
    assert set(QApplication.allWidgets()) <= before


def test_explicit_saved_colour_method_survives_restore(controllers):
    first = controllers()
    first.profiles.apply_place_state({"place_id": "speed_draw", "algo_code": "dfs_4dir", "method_id": "hex_field"})
    first.save()
    first.close(save=False)
    second = controllers()
    assert second.restore()
    assert second.service.engine.color_picking_method == "hex_field"
    assert second.profiles.export_place_state()["method_id"] == "hex_field"
    assert second.preparation.current_method_id == "hex_field"


def test_all_profiles_survive_restart_and_active_profile_is_saved(controllers):
    first = controllers()
    first.profiles.select("speed_draw", "dfs_4dir")
    first.service.set_brush_size(3)
    first.profiles.select("speed_draw", "outline_and_fill")
    first.service.set_brush_size(9)
    first.update_shell_state({"theme": "light", "geometry": [1, 2, 1200, 800]})
    first.save()
    first.close(save=False)
    second = controllers()
    assert second.restore()
    assert second.profiles.algorithm == "outline_and_fill"
    assert second.service.engine.brush_size == 9
    assert second.export_shell_state()["theme"] == "light"
    second.profiles.select("speed_draw", "dfs_4dir")
    assert second.service.engine.brush_size == 3
    second.profiles.select("speed_draw", "outline_and_fill")
    assert second.service.engine.brush_size == 9
    assert second.restore() is False


def test_restore_keeps_image_when_original_file_is_gone(controllers, tmp_path):
    source = tmp_path / "source.png"
    Image.new("RGB", (19, 13), (20, 180, 50)).save(source)
    first = controllers()
    assert first.service.open_image(str(source))
    first.profiles.select("universal", "outline_and_paint")
    first.service.apply_viewport_state(draw_region_desktop_px=(10, 20, 30, 40), emit=True)
    first.service.set_brush_size(5)
    first.save()
    first.close(save=False)
    source.unlink()
    second = controllers()
    assert second.restore()
    assert second.profiles.place_id == "universal"
    assert second.service.engine.source_pil_image.size == (19, 13)
    assert second.service.engine.draw_region == (10, 20, 30, 40)
    assert second.service.engine.brush_size == 5


def test_legacy_selection_and_algorithm_alias_restore(controllers):
    controller = controllers()
    controller.profiles.restore_session_profiles({"universal::dfs_4dir_dynamic": {"brush_size": 6}})
    controller.profiles.apply_place_state({"place_file": "Универсальный", "algo_code": "dfs_4dir_dynamic"})
    assert controller.profiles.place_id == "universal"
    assert controller.profiles.algorithm == "dfs_4dir"
    # A restored selection does not apply another stored profile over the session.
    controller.profiles.select("universal", "outline_and_fill")
    controller.profiles.restore_session_profiles({"universal::dfs_4dir_dynamic": {"brush_size": 6}})
    controller.profiles.select("universal", "dfs_4dir")
    assert controller.service.engine.brush_size == 6


def test_unknown_selection_does_not_change_engine_or_profile(controllers):
    controller = controllers()
    before = controller.service.snapshot_painter_config()
    place = controller.profiles.place_id
    with pytest.raises(ValueError):
        controller.profiles.select("not-a-place")
    with pytest.raises(ValueError):
        controller.profiles.apply_place_state({"place_id": "missing"})
    with pytest.raises(ValueError):
        controller.profiles.select("universal", "missing-algorithm")
    assert controller.service.snapshot_painter_config() == before
    assert controller.profiles.place_id == place


def test_failed_autosave_is_reported_and_can_be_retried(controllers, monkeypatch):
    controller = controllers()
    errors = []
    controller.errorOccurred.connect(errors.append)
    controller.service.set_brush_size(11)
    write = controller.session.store.write_autosave
    def fail(*_):
        raise OSError("disk unavailable")
    monkeypatch.setattr(controller.session.store, "write_autosave", fail)
    controller.session._autosave()
    from PySide6.QtTest import QTest
    for _ in range(200):
        if errors:
            break
        QTest.qWait(5)
    assert errors == ["disk unavailable"]
    monkeypatch.setattr(controller.session.store, "write_autosave", write)
    assert controller.session.flush_now()
    document = read_document(controller.session.store.session_path)
    assert document["payload"]["painter"]["brush_size"] == 11


def test_closed_controller_stops_tracking_service_changes(controllers):
    controller = controllers()
    controller.close(save=False)
    controller.service.set_brush_size(12)
    controller.profiles.select("universal")
    assert not controller.session._timer.isActive()
    assert not controller.session.store.session_path.exists()


def test_workspace_retries_capture_teardown_before_marking_closed(controllers, monkeypatch):
    controller = controllers()
    desktop = controller.enable_desktop()
    original = desktop.desktop_overlays.shutdown
    def failed_stop():
        raise RuntimeError("hook stop failed")
    monkeypatch.setattr(desktop.desktop_overlays, "shutdown", failed_stop)
    with pytest.raises(RuntimeError, match="hook stop failed"):
        controller.close(save=False)
    assert not desktop._closed
    assert not controller._closed
    assert not controller.service._shutdown_completed
    monkeypatch.setattr(desktop.desktop_overlays, "shutdown", original)
    controller.close(save=False)
    assert desktop._closed and controller._closed


def test_desktop_tools_restore_without_a_legacy_shell(controllers):
    before = set(QApplication.allWidgets())
    first = controllers()
    desktop = first.enable_desktop()
    assert first.enable_desktop() is desktop
    assert not any(type(w).__name__ in ("MainWindow", "SettingsPage")
                   for w in set(QApplication.allWidgets()) - before)
    desktop.kalka_overlay.set_stencil_opacity(0.42)
    desktop.kalka_overlay.set_border_visible(False)
    desktop.hud_overlay.restore_state({"layout_version": 2, "visible": True,
                                       "panel_position": [0.2, 0.3], "scale": 1.2})
    first.close()
    assert first.service._shutdown_completed
    second = controllers()
    assert second.restore()
    # The retained document also supports enabling desktop tools after restore.
    state = second.export_shell_state()
    assert state["kalka"]["stencil_opacity"] == 0.42
    restored = second.enable_desktop().export_state()
    assert restored["kalka"]["show_border"] is False
    assert restored["kalka"]["stencil_opacity"] == 0.42
    assert restored["overlay"]["visible"] is True
    assert restored["overlay"]["panel_position"] == [0.2, 0.3]
    assert restored["overlay"]["scale"] == 1.2


def test_close_flushes_last_stencil_move_before_saving(controllers):
    controller = controllers()
    desktop = controller.enable_desktop()
    controller.service.engine.source_pil_image = Image.new("RGB", (100, 100), "blue")
    desktop.edit_stencil()
    QApplication.processEvents()
    stencil = desktop.kalka_overlay
    stencil.move(180, 190)
    stencil.request_save()
    region, _ = stencil.get_stencil_geometry()
    assert stencil._content_timer.isActive()
    controller.close()
    document = read_document(controller.session.store.session_path)
    assert document["payload"]["painter"]["draw_region"] == list(region)
    assert not controller.session._timer.isActive()


def test_close_cancels_f1_without_saving_full_screen_region(controllers):
    from types import SimpleNamespace
    controller = controllers()
    desktop = controller.enable_desktop()
    controller.service.engine.source_pil_image = Image.new("RGB", (100, 100), "blue")
    desktop.edit_stencil()
    QApplication.processEvents()
    stencil = desktop.kalka_overlay
    stencil.move(180, 190)
    desktop._on_kalka_changed()
    region = tuple(controller.service.engine.draw_region)
    stencil.begin_area_placement(SimpleNamespace(logical_rect=(0, 0, 1920, 1080)))
    assert stencil._placement_active
    controller.close()
    document = read_document(controller.session.store.session_path)
    assert document["payload"]["painter"]["draw_region"] == list(region)


def test_failed_close_preserves_desktop_and_service_for_retry(controllers, monkeypatch):
    controller = controllers()
    desktop = controller.enable_desktop()
    desktop.set_hud_visible(True)
    write = controller.session.store.write_autosave
    def fail(*_):
        raise OSError("disk unavailable")
    monkeypatch.setattr(controller.session.store, "write_autosave", fail)
    with pytest.raises(OSError, match="disk unavailable"):
        controller.close()
    assert not controller._closed
    assert not desktop._closed
    assert not getattr(controller.service, "_shutdown_started", False)
    assert desktop.hud_overlay.isVisible()
    monkeypatch.setattr(controller.session.store, "write_autosave", write)
    controller.close()
    assert controller._closed
    assert controller.service._shutdown_completed


def test_closed_desktop_disconnects_requests_and_deferred_capture(controllers, monkeypatch):
    controller = controllers()
    desktop = controller.enable_desktop()
    controller.service.engine.source_pil_image = Image.new("RGB", (100, 100), "blue")
    desktop.toggle_stencil()
    controller.close(save=False)
    def unexpected(*_):
        pytest.fail("A closed workspace must not capture or open an overlay")
    monkeypatch.setattr(controller.service, "capture_from_kalka", unexpected)
    monkeypatch.setattr(desktop.kalka_overlay, "show", unexpected)
    controller.service.kalkaToggleRequested.emit()
    desktop._on_kalka_changed()
    desktop._restore_kalka_geometry_once((0, 0, 100, 100))
    assert not desktop.kalka_overlay.isVisible()
    assert not desktop.hud_overlay.isVisible()


def test_frame_palette_place_moves_to_other_program_with_its_method(controllers):
    # PLACES-002: «Палитра рамкой» is a colour method of «Другая программа» now.
    controller = controllers()
    assert controller.profiles.place_id == "universal"  # no Roblox preset for a newcomer
    controller.profiles.restore_session_profiles({"screen_palette::dfs_4dir": {"brush_size": 7},
                                                  "universal::line_cover": {"brush_size": 3}})
    controller.profiles.apply_place_state({"place_id": "screen_palette", "algo_code": "dfs_4dir"})
    assert controller.profiles.place_id == "universal"
    assert controller.service.snapshot_painter_config()["color_picking_method"] == "screen_palette"
    profiles = controller.profiles.export_session_profiles()
    assert "universal::line_cover" in profiles and not any(key.startswith("screen_palette::") for key in profiles)
    # Very old documents name the place by its label only.
    controller.profiles.apply_place_state({"place_text": "Палитра рамкой"})
    assert controller.profiles.place_id == "universal"


def test_roblox_speed_draw_nudges_the_cursor_before_each_press(controllers):
    # Live Speed Draw! 2026-10-01: jumps between areas were painted as lines without it.
    controller = controllers()
    controller.profiles.select("speed_draw", None)
    assert controller.service.snapshot_painter_config()["pen_press_nudge"] is True

