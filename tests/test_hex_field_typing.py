"""HEX field typing that survives Roblox Spray Paint (live 2026-09-29)."""
import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.core import OlegPainter
import engine.olegpainter.color_picking as color_picking
from tests.test_quick_presentation import quick  # noqa: F401


def test_code_replaces_selection_one_character_at_a_time(monkeypatch):
    monkeypatch.setattr(color_picking.time, "sleep", lambda *_: None)
    engine = OlegPainter(status_callback=lambda _m: None)
    engine.hex_input_coord = (10, 20)
    engine.hex_add_hash = True
    events = engine._input.backend.events
    events.clear()
    engine._pick_color_hex_field("#0040FF")
    typed = [event[1] for event in events if event[0] == "text"]
    keys = [event[1] for event in events if event[0] == "keys"]
    assert typed == list("#0040FF")        # repeated F would be lost in one burst
    assert keys == ["ctrl+a", "enter"]     # no Delete: an emptied box re-inserts "#"


def test_spray_paint_place_turns_on_the_hash_prefix(quick):
    quick.service.set_hex_add_hash(False)
    quick.controller.profiles.select("spray_paint", None)
    config = quick.service.snapshot_painter_config()
    assert config["hex_add_hash"] is True and config["color_picking_method"] == "hex_field"


def test_spray_paint_place_sets_press_nudge_and_fractional_size_range(quick):
    # The size box takes 0.1..1.2: the integer auto ladder would only try 1 and 1.2.
    quick.service.set_pen_press_nudge(False)
    quick.controller.profiles.select("universal", None)
    quick.controller.profiles.select("spray_paint", None)
    config = quick.service.snapshot_painter_config()
    brush = quick.service.engine.get_dynamic_brush_settings()
    assert config["pen_press_nudge"] is True
    assert brush["control_mode"] == "text" and brush["text_auto"] is False
    assert (brush["min_value"], brush["max_value"], brush["step_value"], brush["default_value"]) == (0.1, 1.2, 0.1, 0.1)


def test_brush_probe_colour_contrasts_with_the_free_spot(monkeypatch):
    from PIL import Image
    engine = OlegPainter(status_callback=lambda _m: None)
    monkeypatch.setattr(engine, "_grab_patch", lambda *_a: Image.new("RGB", (8, 8), (40, 28, 20)))
    assert engine._dynamic_brush_probe_color((0, 0, 100, 100)) == engine._DYNAMIC_BRUSH_LIGHT_PROBE_COLOR
    monkeypatch.setattr(engine, "_grab_patch", lambda *_a: Image.new("RGB", (8, 8), (250, 250, 250)))
    assert engine._dynamic_brush_probe_color((0, 0, 100, 100)) == engine._DYNAMIC_BRUSH_VALIDATION_COLOR
