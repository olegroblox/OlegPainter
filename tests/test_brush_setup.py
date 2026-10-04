"""Simplified brush setup: learned values survive publication and profile reload."""
import math

import numpy as np
import pytest
from PIL import Image

from engine.olegpainter.core import OlegPainter
from engine.olegpainter.brush_measurement import measure_stamp


@pytest.fixture
def learner(monkeypatch):
    engine = OlegPainter()
    engine.brush_size = 1
    engine.draw_region = (100, 100, 200, 200)
    engine.dynamic_brush_coord = (30, 30)
    engine.dynamic_brush_scratch_zone = (400, 100, 600, 600)
    monkeypatch.setattr(engine, "_dynamic_brush_v2_clean_spots",
                        lambda rect, half, count: [(450 + i * 25, 200) for i in range(count)])
    monkeypatch.setattr(engine, "_dynamic_brush_can_pick_calibration_color", lambda: False)
    monkeypatch.setattr(engine, "_apply_dynamic_brush_value", lambda *a, **kw: True)
    return engine


def probe_curve(engine, monkeypatch, curve):
    values = []
    def probe(value, *args, **kwargs):
        values.append(value)
        radius = curve(value)
        return dict(radius_px=radius, area_px=math.pi * radius**2,
                    density=1., confidence=1., shape="circle", offset_x=-.5, offset_y=-.5)
    monkeypatch.setattr(engine, "_dynamic_brush_v2_probe", probe)
    return values


def test_auto_range_skips_minimum_plateau_and_round_trips(learner, monkeypatch):
    values = probe_curve(learner, monkeypatch, lambda v: min(13., max(2., v)))
    assert learner.learn_dynamic_brush_profile()
    assert 3 in values and 34 in values and 55 not in values
    assert learner.dynamic_brush_max_value == 13
    assert learner.dynamic_brush_min_value == 1
    config = learner.get_config()
    restored = OlegPainter()
    restored.load_config(config)
    assert restored.dynamic_brush_text_auto
    assert restored.dynamic_brush_profile_state == "ready"
    assert restored.dynamic_brush_max_value == 13
    assert restored._dynamic_brush_stamp_offset(1) == (-.5, -.5)
    assert restored.dynamic_brush_recommended_cell() == 4


def test_unchanging_field_does_not_replace_saved_setup(learner, monkeypatch):
    previous = learner.get_dynamic_brush_settings()
    probe_curve(learner, monkeypatch, lambda v: 3.)
    assert not learner.learn_dynamic_brush_profile()
    current = learner.get_dynamic_brush_settings()
    for key in ("min_value", "max_value", "default_value", "step_value", "profile", "calibration"):
        assert current[key] == previous[key]


def test_numeric_validator_rejecting_large_values_keeps_only_verified_range(learner, monkeypatch):
    probe_curve(learner, monkeypatch, lambda v: v if v < 8 else 1.)
    assert learner.learn_dynamic_brush_profile()
    assert learner.dynamic_brush_max_value == 5
    assert learner.dynamic_brush_profile["cached_calibration"]["validation"]["passed"]


@pytest.mark.parametrize("orientation", ["horizontal", "vertical"])
def test_reversed_slider_learns_and_reloads_same_physical_positions(learner, monkeypatch, orientation):
    learner.set_dynamic_brush_control_mode("slider")
    original = (orientation, 50., 10., 110.)
    learner.dynamic_brush_slider_params = original
    def radius(value):
        # Physical end at 110 is the largest, irrespective of orientation.
        _, _, maximum, minimum = learner.dynamic_brush_slider_params
        position = minimum * (1 - value) + maximum * value
        return 2 + (position - 10) / 10
    probe_curve(learner, monkeypatch, radius)
    assert learner.learn_dynamic_brush_profile()
    assert learner.dynamic_brush_slider_params == (orientation, 50., 110., 10.)
    restored = OlegPainter()
    restored.load_config(learner.get_config())
    assert restored.dynamic_brush_profile_state == "ready"
    assert restored.dynamic_brush_slider_params == learner.dynamic_brush_slider_params


def test_reversed_slider_failure_restores_direction(learner, monkeypatch):
    learner.set_dynamic_brush_control_mode("slider")
    learner.dynamic_brush_slider_params = ("horizontal", 50., 10., 110.)
    previous = learner.dynamic_brush_slider_params
    probe_curve(learner, monkeypatch, lambda v: 12 - v * 10)
    monkeypatch.setattr(learner, "_dynamic_brush_v2_verify", lambda *a, **kw: dict(passed=False, relative_error=1.))
    assert not learner.learn_dynamic_brush_profile()
    assert learner.dynamic_brush_slider_params == previous
    assert learner.dynamic_brush_profile is None


def test_legacy_fractional_range_is_not_replaced_by_integer_auto_mode():
    engine = OlegPainter()
    config = engine.get_config()
    config.pop("dynamic_brush_text_auto")
    engine.load_config(config)
    assert not engine.dynamic_brush_text_auto
    assert engine.dynamic_brush_min_value == .05
    assert not engine.get_config()["dynamic_brush_text_auto"]
    engine.load_config({})
    assert engine.dynamic_brush_text_auto


def test_small_stamp_offset_is_measured_from_cursor_pixel():
    before = np.full((20, 20, 3), 255, np.uint8)
    after = before.copy()
    after[9:11, 9:11] = 0
    result = measure_stamp(Image.fromarray(before), Image.fromarray(after), anchor=(10, 10))
    assert result["offset_x"] == -.5 and result["offset_y"] == -.5


def test_grid_step_rounds_measured_diameter_without_reporting_no_op(learner, monkeypatch):
    probe_curve(learner, monkeypatch, lambda v: .9772 * min(v, 13))
    assert learner.learn_dynamic_brush_profile()
    assert learner.dynamic_brush_recommended_cell() == 2
    learner.brush_size = 2
    assert learner.dynamic_brush_recommended_cell() is None


def test_wide_slider_learns_from_its_small_end_without_blind_big_stamps(learner, monkeypatch):
    # «Нарисуй меня!»: the track spans 1..265 px; stamps at quarters of it were
    # bigger than the test zone and painted over the drawing.
    learner.set_dynamic_brush_control_mode("slider")
    learner.dynamic_brush_slider_params = ("horizontal", 740., 1445., 1170.)
    learner.brush_size = 4
    applied, probed = [], []
    monkeypatch.setattr(learner, "_apply_dynamic_brush_value", lambda value, **kw: applied.append(value) or True)

    def probe(value, *args, **kwargs):
        probed.append(value)
        radius = 1 + 264 * value
        if radius > 40:
            return None                      # clipped by the test patch
        return dict(radius_px=radius, area_px=math.pi * radius**2, density=1., confidence=1., shape="circle")
    monkeypatch.setattr(learner, "_dynamic_brush_v2_probe", probe)
    assert learner.learn_dynamic_brush_profile()
    assert max(probed) <= .17 + 1e-9                     # one step past the zone, never the far end
    values = sorted(s["value"] for s in learner.dynamic_brush_calibration["samples"])
    assert values[0] == 0 and values[-1] == .12
    assert applied[-1] == 0                              # left at the drawing's own small size
    assert learner.dynamic_brush_slider_params == ("horizontal", 740., 1445., 1170.)
