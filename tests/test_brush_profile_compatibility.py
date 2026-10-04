"""Saved calibration cannot grant readiness by trusting an old valid flag."""
from copy import deepcopy
from unittest.mock import Mock

import pytest

from engine.olegpainter.core import OlegPainter


def learned_config():
    engine = OlegPainter()
    engine.brush_size = 1
    engine.draw_region = (100, 100, 200, 200)
    engine.dynamic_brush_coord = (20, 30)
    engine.dynamic_brush_scratch_zone = (400, 100, 500, 500)
    engine.dynamic_brush_enabled = True
    calibration = engine._build_dynamic_brush_calibration_payload(
        [engine._dynamic_brush_v2_make_sample(v, dict(radius_px=r, area_px=3.14*r*r))
         for v, r in ((.05, 2), (.5, 20), (1.2, 40))],
        validation=dict(passed=True, inside_coverage=1, outside_pixels=0))
    engine.dynamic_brush_profile = dict(
        version=engine._DYNAMIC_BRUSH_PROFILE_VERSION, valid=True, control_mode="text",
        control_params=engine._dynamic_brush_normalize_point(engine.dynamic_brush_coord),
        scratch_zone=engine._dynamic_brush_normalize_rect(engine.dynamic_brush_scratch_zone),
        range=dict(min_value=.05, max_value=1.2, default_value=.1, step_value=.01),
        cached_calibration=calibration, learned_at=100)
    engine.dynamic_brush_calibration = calibration
    return engine.get_config()


def test_old_profile_keeps_captures_and_samples_across_repeated_saves():
    config = learned_config()
    for cached in (config["dynamic_brush_profile"]["cached_calibration"], config["dynamic_brush_calibration"]):
        cached.pop("measurement_revision", None)
        cached.pop("required_values", None)
    original = deepcopy(config)
    for _ in range(3):
        engine = OlegPainter()
        engine.load_config(config)
        assert engine.dynamic_brush_profile_state == "invalid"
        assert engine.get_dynamic_brush_settings()["profile_issue"] == "outdated"
        assert engine.dynamic_brush_coord == (20, 30)
        assert engine.dynamic_brush_scratch_zone == (400, 100, 500, 500)
        assert engine.dynamic_brush_profile["cached_calibration"]["samples"] == original["dynamic_brush_profile"]["cached_calibration"]["samples"]
        assert not engine.prepare_dynamic_brush_for_draw()
        config = engine.get_config()
    assert original["dynamic_brush_profile"]["valid"] is True


@pytest.mark.parametrize("fault", ["missing", "unverified", "nonfinite", "reversed", "flat", "missing_sample", "unknown_revision", "negative", "nan_validation", "string_validation"])
def test_invalid_saved_profile_cannot_enable_drawing(fault):
    config = learned_config()
    profile = config["dynamic_brush_profile"]
    cache = profile["cached_calibration"]
    if fault == "missing":
        profile["cached_calibration"] = None
    elif fault == "unverified":
        cache["validation"]["passed"] = False
    elif fault == "nonfinite":
        cache["samples"][1]["brush_px"] = float("inf")
    elif fault == "reversed":
        cache["samples"][0]["point_reach_cells"] = 211
    elif fault == "flat":
        for row in cache["samples"]:
            row["point_reach_cells"] = 211
    elif fault == "missing_sample":
        cache["samples"].pop(1)
    elif fault == "unknown_revision":
        cache["measurement_revision"] = 999
    elif fault == "negative":
        cache["samples"][0]["point_reach_cells"] = -2
    elif fault == "nan_validation":
        cache["validation"]["inside_coverage"] = float("nan")
    elif fault == "string_validation":
        cache["validation"]["passed"] = "false"
    engine = OlegPainter()
    engine._apply_dynamic_brush_value = Mock(side_effect=AssertionError("must not inject input"))
    engine.load_config(config)
    assert engine.dynamic_brush_profile_state == "invalid"
    assert engine.get_dynamic_brush_settings()["profile_message"]
    assert not engine.prepare_dynamic_brush_for_draw()
    assert not engine.dynamic_brush_session_active
    engine._apply_dynamic_brush_value.assert_not_called()
    # Saving/normalizing the bad data cannot turn it back into a trusted profile.
    reloaded = OlegPainter()
    reloaded.load_config(engine.get_config())
    assert reloaded.dynamic_brush_profile_state == "invalid"


def test_current_profile_survives_restart_and_grid_size_change():
    config = learned_config()
    config["brush_size"] = 8
    for _ in range(3):
        engine = OlegPainter()
        engine.load_config(config)
        assert engine.dynamic_brush_profile_state == "ready"
        assert not engine.get_dynamic_brush_settings()["profile_issue"]
        assert engine.prepare_dynamic_brush_for_draw()
        assert engine._dynamic_brush_v2_radius_px_for_value(1.2) == 40
        config = engine.get_config()


def test_normalization_does_not_turn_reversed_observations_into_flat_curve():
    engine = OlegPainter()
    cache = learned_config()["dynamic_brush_profile"]["cached_calibration"]
    cache["samples"][0]["point_reach_cells"] = 211
    normalized = engine._normalize_dynamic_brush_calibration_payload(cache)
    assert [r["point_reach_cells"] for r in normalized["samples"]] == [211, 20, 40]
    assert normalized["measurement_invalid"]
