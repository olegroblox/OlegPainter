# -*- coding: utf-8 -*-
"""Dynamic brush v2 — rest-machining tier ladder, lean probe calibration and the
DRAW-TIME closed loop.

Covers:
  * value<->radius curve from calibration samples (with brush_px units guard);
  * stamp measurement from screenshot diffs (solid sqrt(area/pi), spray p90)
    and the closed-loop verification probe;
  * the tier planner's NO-SPILL invariant (erosion margin covers the swept path);
  * draw-time verification: a lying curve cannot widen a stroke (tiers re-plan
    from the MEASURED radius), an unreachable base size falls back to static;
  * the "points" control mode (fixed on-screen size presets).
"""
from __future__ import annotations

import math
import os
import sys
import unittest

import numpy as np
import cv2
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.core import OlegPainter  # noqa: E402


def _calibration_payload(engine, rows):
    """Build a normalized calibration payload from (value, radius_px) pairs."""
    brush = max(1.0, float(engine.brush_size))
    samples = []
    for value, radius_px in rows:
        radius_cells = radius_px / brush
        samples.append(
            {
                "value": float(value),
                "point_reach_cells": radius_cells,
                "stroke_reach_h_cells": radius_cells,
                "stroke_reach_v_cells": radius_cells,
                "half_width_cells": radius_cells,
                "half_height_cells": radius_cells,
                "area_cells": max(1e-4, math.pi * radius_cells * radius_cells),
                "shape_hint": "circle",
                "confidence": 0.9,
                "density": 1.0,
                "brush_px": brush,
            }
        )
    payload = engine._build_dynamic_brush_calibration_payload(
        samples,
        shape="circle",
        shape_confidence=0.9,
        validation={"target_rect": None, "outside_pixels": 0, "inside_coverage": 1.0, "passed": True},
    )
    assert isinstance(payload, dict)
    return payload


def _white(size=120):
    return Image.new("RGB", (size, size), (255, 255, 255))


class RadiusCurveTests(unittest.TestCase):
    def test_radius_table_inversion_and_extras_survive_normalize(self):
        e = OlegPainter()
        e.brush_size = 1
        payload = _calibration_payload(e, [(0.1, 4.0), (0.5, 20.0), (1.0, 40.0)])
        e.dynamic_brush_calibration = payload
        # density / brush_px must survive the normalize round-trip (v2 extras)
        for row in payload["samples"]:
            self.assertIn("density", row)
            self.assertEqual(row["brush_px"], 1.0)
        self.assertAlmostEqual(e._dynamic_brush_v2_radius_px_for_value(0.5), 20.0, places=4)
        self.assertAlmostEqual(e._dynamic_brush_v2_value_for_radius_px(20.0), 0.5, places=4)
        self.assertAlmostEqual(e._dynamic_brush_v2_value_for_radius_px(12.0), 0.3, places=4)
        # clamps at both ends instead of extrapolating
        self.assertAlmostEqual(e._dynamic_brush_v2_value_for_radius_px(500.0), 1.0, places=4)
        self.assertAlmostEqual(e._dynamic_brush_v2_radius_px_for_value(99.0), 40.0, places=4)

    def test_brush_px_guard_keeps_radii_when_brush_changes(self):
        e = OlegPainter()
        e.brush_size = 2
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.1, 4.0), (1.0, 40.0)])
        before = e._dynamic_brush_v2_radius_px_for_value(1.0)
        e.brush_size = 8  # user changes the grid later -> radii must NOT rescale
        after = e._dynamic_brush_v2_radius_px_for_value(1.0)
        self.assertAlmostEqual(before, 40.0, places=4)
        self.assertAlmostEqual(after, 40.0, places=4)

    def test_base_value_gate(self):
        e = OlegPainter()
        e.brush_size = 2  # base stamp radius ~1 px
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.1, 4.0), (1.0, 40.0)])
        self.assertIsNone(e._dynamic_brush_v2_base_value())  # min radius 4 px >> base
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.1, 1.0), (1.0, 40.0)])
        self.assertIsNotNone(e._dynamic_brush_v2_base_value())


class StampMeasureTests(unittest.TestCase):
    def test_solid_circle_radius_from_area(self):
        e = OlegPainter()
        before = _white()
        canvas = np.full((120, 120, 3), 255, np.uint8)
        cv2.circle(canvas, (60, 60), 20, (0, 0, 0), -1)
        probe = e._dynamic_brush_v2_measure_stamp(before, Image.fromarray(canvas))
        self.assertIsInstance(probe, dict)
        self.assertGreaterEqual(probe["density"], 0.6)
        self.assertEqual(probe["shape"], "circle")
        self.assertAlmostEqual(probe["radius_px"], 20.0, delta=2.0)

    def test_tiny_stamp_survives_measurement(self):
        e = OlegPainter()
        before = _white()
        canvas = np.full((120, 120, 3), 255, np.uint8)
        canvas[59:61, 59:61] = 0  # 2x2 stamp — the morphology pipeline erases it
        probe = e._dynamic_brush_v2_measure_stamp(before, Image.fromarray(canvas))
        self.assertIsInstance(probe, dict)
        self.assertLessEqual(probe["radius_px"], 3.0)

    def test_spray_radius_from_percentile(self):
        e = OlegPainter()
        rng = np.random.default_rng(7)
        before = _white()
        canvas = np.full((120, 120, 3), 255, np.uint8)
        for _ in range(60):
            ang = rng.uniform(0, 2 * math.pi)
            rad = 30.0 * math.sqrt(rng.uniform(0, 1))
            cx = int(round(60 + rad * math.cos(ang)))
            cy = int(round(60 + rad * math.sin(ang)))
            cv2.circle(canvas, (cx, cy), 2, (0, 0, 0), -1)
        probe = e._dynamic_brush_v2_measure_stamp(before, Image.fromarray(canvas))
        self.assertIsInstance(probe, dict)
        self.assertLess(probe["density"], 0.6)  # detected as stochastic spray
        self.assertGreaterEqual(probe["radius_px"], 22.0)
        self.assertLessEqual(probe["radius_px"], 33.0)


class VerifyLoopTests(unittest.TestCase):
    def _samples(self, engine, rows):
        return [s for s in _calibration_payload(engine, rows)["samples"]]

    def test_honest_brush_passes_first_round(self):
        e = OlegPainter()
        e.brush_size = 1
        probed = []

        def fake_probe(value, x, y, half, *, region_rect=None):
            probed.append(float(value))
            r = 40.0 * float(value)
            return {"radius_px": r, "area_px": math.pi * r * r, "density": 1.0, "shape": "circle", "confidence": 0.9}

        e._dynamic_brush_v2_probe = fake_probe  # type: ignore[assignment]
        samples = self._samples(e, [(0.1, 4.0), (1.0, 40.0)])
        result = e._dynamic_brush_v2_verify(samples, iter([(10, 10), (40, 40), (70, 70)]), 50)
        self.assertTrue(result["passed"])
        self.assertLessEqual(result["relative_error"], 0.15)
        self.assertEqual(len(probed), 1)
        self.assertEqual(len(samples), 3)  # verification probe refined the curve

    def test_lying_brush_fails_but_refines(self):
        e = OlegPainter()
        e.brush_size = 1

        def liar_probe(value, x, y, half, *, region_rect=None):
            r = 120.0 * float(value)  # 3x what the seeded curve claims
            return {"radius_px": r, "area_px": math.pi * r * r, "density": 1.0, "shape": "circle", "confidence": 0.9}

        e._dynamic_brush_v2_probe = liar_probe  # type: ignore[assignment]
        samples = self._samples(e, [(0.1, 4.0), (1.0, 40.0)])
        result = e._dynamic_brush_v2_verify(samples, iter([(10, 10), (40, 40), (70, 70)]), 50)
        self.assertFalse(result["passed"])
        self.assertEqual(len(samples), 4)  # both rounds fed the curve
        self.assertLess(result["relative_error"], 1.0)  # round 2 error << round 1 (~2.0)


class TierPlannerTests(unittest.TestCase):
    def test_coarse_valid_centers_cannot_spill(self):
        e = OlegPainter()
        mask = np.ones((50, 50), dtype=bool)
        mask[20:30, 20:30] = False  # a hole the stamp must not touch
        clearance = e._dynamic_brush_v2_clearance_cells(mask)
        r_cells, s_cells = 6.0, 8
        r_req = r_cells + s_cells / 2.0 + 1.0
        valid, rows, cols = e._dynamic_brush_v2_coarse_valid(clearance, r_req, s_cells)
        self.assertGreater(int(np.count_nonzero(valid)), 0)
        spill = np.zeros((50, 50), np.uint8)
        for R, C in np.argwhere(valid):
            cv2.circle(spill, (int(cols[C]), int(rows[R])), int(r_cells), 255, -1)
        self.assertEqual(int(np.count_nonzero((spill > 0) & (~mask))), 0, "stamp disc left the mask")

    def test_grid_is_anchored_at_the_deepest_cell(self):
        e = OlegPainter()
        mask = np.zeros((60, 60), dtype=bool)
        mask[20:39, 20:39] = True  # a 19x19 core whose centre is NOT on a free grid
        clearance = e._dynamic_brush_v2_clearance_cells(mask)
        deepest = np.unravel_index(int(np.argmax(clearance)), clearance.shape)
        valid, rows, cols = e._dynamic_brush_v2_coarse_valid(clearance, float(clearance.max()) - 0.5, 13)
        self.assertIn(int(deepest[0]), [int(r) for r in rows])
        self.assertIn(int(deepest[1]), [int(c) for c in cols])
        self.assertGreaterEqual(int(np.count_nonzero(valid)), 1, "the deepest cell must be a usable node")

    def test_plan_grid_falls_back_to_isolated_stamps(self):
        e = OlegPainter()
        e.brush_size = 2
        mask = np.zeros((60, 60), dtype=bool)
        mask[10:36, 10:36] = True  # 26x26 cells: stroke margin can't fit a 20px brush
        clearance = e._dynamic_brush_v2_clearance_cells(mask)
        plan = e._dynamic_brush_v2_plan_grid(clearance, 20.0)
        self.assertIsNotNone(plan)
        self.assertTrue(plan["stamps_only"], "deep-core stamps must be used when strokes can't fit")
        # and the stamp discs still cannot leave the mask (r + 1 margin)
        spill = np.zeros((60, 60), np.uint8)
        for R, C in np.argwhere(plan["valid"]):
            cv2.circle(spill, (int(plan["cols"][C]), int(plan["rows"][R])), 10, 255, -1)
        self.assertEqual(int(np.count_nonzero((spill > 0) & (~mask))), 0)

    def test_points_candidates_are_the_presets_themselves(self):
        e = OlegPainter()
        e.brush_size = 2
        e.dynamic_brush_control_mode = "points"
        e.dynamic_brush_points = [
            {"x": 1, "y": 1, "value": 1.0},
            {"x": 2, "y": 2, "value": 5.0},
            {"x": 3, "y": 3, "value": 20.0},
        ]
        e.dynamic_brush_calibration = _calibration_payload(e, [(1.0, 2.0), (5.0, 16.0), (20.0, 40.0)])
        candidates = e._dynamic_brush_v2_tier_candidates(100.0, 5.0)
        self.assertEqual(candidates, [40.0, 16.0])  # big presets first, base preset below tier_min

    def test_clearance_treats_region_border_as_outside(self):
        e = OlegPainter()
        mask = np.ones((30, 30), dtype=bool)  # color runs edge-to-edge
        clearance = e._dynamic_brush_v2_clearance_cells(mask)
        self.assertLessEqual(float(clearance[0, 15]), 1.5)  # border cell is NOT deep interior


class PointsModeTests(unittest.TestCase):
    def _engine(self):
        e = OlegPainter()
        e.brush_size = 2
        e.dynamic_brush_control_mode = "points"
        e.dynamic_brush_points = [
            {"x": 100, "y": 50, "value": 1.0},
            {"x": 140, "y": 50, "value": 5.0},
            {"x": 180, "y": 50, "value": 20.0},
        ]
        return e

    def test_quantize_snaps_to_nearest_point_label(self):
        e = self._engine()
        self.assertEqual(e._quantize_dynamic_brush_value(4.2), 5.0)
        self.assertEqual(e._quantize_dynamic_brush_value(0.0), 1.0)
        self.assertEqual(e._quantize_dynamic_brush_value(999.0), 20.0)

    def test_value_for_radius_floor_selects_smaller_preset(self):
        e = self._engine()
        e.dynamic_brush_calibration = _calibration_payload(
            e, [(1.0, 2.0), (5.0, 10.0), (20.0, 40.0)]
        )
        # requested 30px: the 40px preset would spill — must take the 10px one
        self.assertEqual(e._dynamic_brush_v2_value_for_radius_px(30.0), 5.0)
        self.assertEqual(e._dynamic_brush_v2_value_for_radius_px(40.0), 20.0)
        self.assertEqual(e._dynamic_brush_v2_value_for_radius_px(1.0), 1.0)  # below min -> smallest

    def test_apply_clicks_nearest_point_with_press_hold(self):
        e = self._engine()
        clicks = []
        e._click_abs = lambda x, y: clicks.append((x, y))  # type: ignore[assignment]
        from unittest.mock import patch
        with patch("engine.olegpainter.core.time.sleep", lambda *_a, **_k: None):
            with patch.object(e._input.backend, "button") as fake:
                self.assertTrue(e._apply_dynamic_brush_value(4.0, force=True))
        self.assertEqual(clicks, [(140, 50)])
        # Hold/release crosses the device boundary twice (focus insurance).
        self.assertEqual([call.args for call in fake.call_args_list],
                         [("left", True), ("left", False), ("left", True), ("left", False)])

    def test_drag_slider_glides_from_last_value_to_target(self):
        e = OlegPainter()
        e.dynamic_brush_control_mode = "slider"
        e.dynamic_brush_slider_params = ("vertical", 100.0, 500.0, 100.0)  # track y: 100(min)..500(max)
        e.update_dynamic_brush_settings(min_value=0.0, max_value=1.0, default_value=0.0, step=0.01)
        e.dynamic_brush_drag_enabled = True
        events = []
        e._click_abs = lambda x, y: events.append(("move", x, y))  # type: ignore[assignment]
        from unittest.mock import patch
        with patch("engine.olegpainter.core.time.sleep", lambda *_a, **_k: None):
            with patch.object(e._input.backend, "button") as fake:
                fake.side_effect = lambda button, down: events.append(("down" if down else "up",))
                self.assertTrue(e._apply_dynamic_brush_value(0.5, force=True))   # first touch: from track MIN
                self.assertTrue(e._apply_dynamic_brush_value(1.0, force=True))   # then: from the 0.5 position
        downs = [i for i, ev in enumerate(events) if ev[0] == "down"]
        ups = [i for i, ev in enumerate(events) if ev[0] == "up"]
        self.assertEqual(len(downs), 2)
        self.assertEqual(len(ups), 2)
        # presses stay one pixel inside the (screen-refined) track ends
        # drag 1: press at the min end (y=101), glide, release at the 0.5 position (y=300)
        first_press_move = events[downs[0] - 1]
        first_release_move = events[ups[0] - 1]
        self.assertEqual(first_press_move, ("move", 100, 101))
        self.assertEqual(first_release_move[2], 300)
        self.assertGreater(ups[0] - downs[0], 1, "the glide must move while the button is held")
        # glide is monotone towards the target, then the held button rocks ±2 px
        # along the track and returns to the target before release
        glide_ys = [ev[2] for ev in events[downs[0] + 1:ups[0]]]
        self.assertEqual(glide_ys[-3:], [302, 298, 300])
        self.assertEqual(glide_ys[:-3], sorted(glide_ys[:-3]))
        # drag 2: press where drag 1 released (y=300), release at the very end
        # of the track (y=500) — never past it, onto neighbouring buttons
        second_press_move = events[downs[1] - 1]
        second_release_move = events[ups[1] - 1]
        self.assertEqual(second_press_move, ("move", 100, 300))
        self.assertEqual(second_release_move[2], 500)
        self.assertTrue(all(100 <= ev[2] <= 500 for ev in events if ev[0] == "move"))

    def test_drag_points_glides_between_presets(self):
        e = self._engine()
        e.dynamic_brush_drag_enabled = True
        events = []
        e._click_abs = lambda x, y: events.append(("move", x, y))  # type: ignore[assignment]
        from unittest.mock import patch
        with patch("engine.olegpainter.core.time.sleep", lambda *_a, **_k: None):
            with patch.object(e._input.backend, "button") as fake:
                fake.side_effect = lambda button, down: events.append(("down" if down else "up",))
                self.assertTrue(e._apply_dynamic_brush_value(5.0, force=True))   # first: press&release at (140,50)
                self.assertTrue(e._apply_dynamic_brush_value(20.0, force=True))  # then: glide (140,50)->(180,50)
        downs = [i for i, ev in enumerate(events) if ev[0] == "down"]
        ups = [i for i, ev in enumerate(events) if ev[0] == "up"]
        self.assertEqual(events[downs[0] - 1], ("move", 140, 50))
        self.assertEqual(events[ups[0] - 1][1:], (140, 50))      # in-place press-release first time
        self.assertEqual(events[downs[1] - 1], ("move", 140, 50))
        self.assertEqual(events[ups[1] - 1][1:], (180, 50))      # released on the bigger preset

    def test_drag_flag_round_trips_in_config(self):
        e = OlegPainter()
        e.dynamic_brush_drag_enabled = True
        e2 = OlegPainter()
        e2.load_config(e.get_config())
        self.assertTrue(e2.dynamic_brush_drag_enabled)

    def test_ui_click_at_orders_press_after_move(self):
        e = OlegPainter()
        events = []
        e._click_abs = lambda x, y: events.append(("move", x, y))  # type: ignore[assignment]
        from unittest.mock import patch
        with patch("engine.olegpainter.core.time.sleep", lambda *_a, **_k: None):
            with patch.object(e._input.backend, "button") as fake:
                fake.side_effect = lambda button, down: events.append(("down" if down else "up",))
                e._ui_click_at(10, 20, clicks=2, hold=0.0, settle=0.0)
        self.assertEqual(events, [("move", 10, 20), ("down",), ("up",), ("down",), ("up",)])

    def test_control_ready_and_mode_switch_preserves_captures(self):
        e = self._engine()
        self.assertTrue(e._dynamic_brush_control_ready())
        e.dynamic_brush_coord = (5, 6)
        e.dynamic_brush_slider_params = ("vertical", 10.0, 20.0, 200.0)
        e.dynamic_brush_scratch_zone = (0, 0, 100, 100)
        e.set_dynamic_brush_control_mode("slider")
        e.set_dynamic_brush_control_mode("text")
        e.set_dynamic_brush_control_mode("points")
        self.assertEqual(len(e.dynamic_brush_points), 3)
        self.assertEqual(e.dynamic_brush_coord, (5, 6))
        self.assertIsNotNone(e.dynamic_brush_slider_params)
        self.assertEqual(e.dynamic_brush_scratch_zone, (0, 0, 100, 100))

    def test_points_config_round_trip(self):
        e = self._engine()
        data = e.get_config()
        e2 = OlegPainter()
        e2.load_config(data)
        self.assertEqual(e2.dynamic_brush_control_mode, "points")
        self.assertEqual(len(e2.dynamic_brush_points), 3)
        self.assertEqual(e2.dynamic_brush_points[0]["value"], 1.0)

    def test_scratch_zone_round_trip_and_wins_over_profile_relative_copy(self):
        """The scratch zone lives at ABSOLUTE desktop coordinates. It must survive
        a restart verbatim, even when the profile carries a draw-region-RELATIVE
        copy that would resolve somewhere else (the 'scratch zone moved after
        restart' bug)."""
        e = self._engine()
        e.draw_region = (0, 0, 200, 200)
        e.dynamic_brush_scratch_zone = (700, 40, 480, 420)
        e.dynamic_brush_profile = {
            "version": 2,
            "valid": False,
            "control_mode": "points",
            "control_params": None,
            # normalized against SOME draw region; resolving it against another
            # region would land far away from (700, 40)
            "scratch_zone": {"x_ratio": 0.1, "y_ratio": 0.1, "w_ratio": 0.5, "h_ratio": 0.5},
            "range": {"min_value": 1.0, "max_value": 20.0, "default_value": 1.0, "step_value": 0.01},
            "cached_calibration": None,
            "learned_at": 1.0,
        }
        data = e.get_config()
        e2 = OlegPainter()
        e2.load_config(dict(data, draw_region=[50, 60, 400, 300]))  # region moved between sessions
        self.assertEqual(tuple(e2.dynamic_brush_scratch_zone), (700, 40, 480, 420))

    def test_points_calibration_probes_each_preset(self):
        e = self._engine()
        e.dynamic_brush_scratch_zone = (0, 0, 1600, 400)
        probed = []

        def fake_probe(value, x, y, half, *, region_rect=None):
            probed.append(float(value))
            r = 2.0 * float(value)
            return {"radius_px": r, "area_px": math.pi * r * r, "density": 1.0, "shape": "circle", "confidence": 0.9}

        e._dynamic_brush_v2_probe = fake_probe  # type: ignore[assignment]
        e._apply_dynamic_brush_value = lambda *a, **k: True  # type: ignore[assignment]
        payload = e._run_dynamic_brush_calibration(
            calibration_region=e.dynamic_brush_scratch_zone, persist=False, status_messages=False
        )
        self.assertIsInstance(payload, dict)
        self.assertEqual(sorted(set(probed)), [1.0, 5.0, 20.0])  # each preset plus independent verification of a preset
        self.assertTrue(payload["validation"]["passed"])  # independent verification repeats a preset


class DegenerateCalibrationTests(unittest.TestCase):
    def test_inconsistent_or_incomplete_run_preserves_previous_profile(self):
        from unittest.mock import Mock

        # (a missing LARGEST size is an upper limit, not a failure: TooBigForZoneTests)
        for failure in ("reversed", "verification_reversal"):
            with self.subTest(failure=failure):
                e = OlegPainter()
                e.brush_size = 1
                e.dynamic_brush_control_mode = "text"
                e.dynamic_brush_text_auto = False  # this case covers a manually given range
                e.dynamic_brush_coord = (5, 5)
                e.update_dynamic_brush_settings(min_value=0, max_value=1, default_value=.2, step=.01)
                previous = _calibration_payload(e, [(0, 2), (1, 40)])
                e.dynamic_brush_calibration = previous
                e._dynamic_brush_v2_clean_spots = lambda *a: [(i * 100, 200) for i in range(11)]
                e._apply_dynamic_brush_value = Mock(return_value=True)

                def probe(value, *a, **k):
                    if failure == "missing_max" and value == 1:
                        return None
                    radius = 211 if failure == "reversed" and value == 0 else 2 + 40 * value
                    return dict(radius_px=radius, area_px=math.pi * radius ** 2, density=1, shape="circle", confidence=1)

                def verify(samples, *a, **k):
                    if failure == "verification_reversal":
                        samples.append(e._dynamic_brush_v2_make_sample(.5, dict(radius_px=100, area_px=10000)))
                    return dict(passed=True, relative_error=0)

                e._dynamic_brush_v2_probe = probe
                e._dynamic_brush_v2_verify = Mock(side_effect=verify)
                result = e._run_dynamic_brush_calibration(calibration_region=(0, 0, 2000, 600), persist=True, status_messages=False)
                self.assertIsNone(result)
                self.assertIs(e.dynamic_brush_calibration, previous)
                e._apply_dynamic_brush_value.assert_called_with(.2, force=True)
                self.assertEqual(e._dynamic_brush_v2_verify.call_count, int(failure == "verification_reversal"))

    def test_raw_consistency_respects_units_repeats_and_small_pixel_noise(self):
        e = OlegPainter()
        def rows(radii):
            return [dict(value=v, point_reach_cells=r / 2, brush_px=2) for v, r in radii]
        self.assertTrue(e._dynamic_brush_samples_consistent(rows([(0, 2), (.5, 20), (.5, 20.5), (1, 40)]), [0, .5, 1]))
        for radii in ([(0, 211), (.5, 35), (1, 123)], [(0, 2), (.5, 20), (.5, 30), (1, 40)], [(0, 2), (1, float("nan"))]):
            self.assertFalse(e._dynamic_brush_samples_consistent(rows(radii), [0, 1]))

    def test_flat_radii_are_degenerate(self):
        e = OlegPainter()
        e.brush_size = 2
        rows = [{"point_reach_cells": 0.63} for _ in range(5)]  # every point the same brush
        degenerate, msg = e._dynamic_brush_calibration_degenerate(rows)
        self.assertTrue(degenerate)
        self.assertTrue(msg)

    def test_real_spread_is_not_degenerate(self):
        e = OlegPainter()
        e.brush_size = 2
        rows = [{"point_reach_cells": v} for v in (0.6, 1.3, 3.4, 7.0, 13.0)]
        self.assertFalse(e._dynamic_brush_calibration_degenerate(rows)[0])

    def test_calibration_refuses_when_control_never_changes_brush(self):
        """End-to-end: a control whose probes always measure the same radius must
        make _run_dynamic_brush_calibration return None (refuse), not save a
        profile that would silently draw with the smallest brush."""
        e = OlegPainter()
        e.brush_size = 2
        e.dynamic_brush_control_mode = "text"
        e.dynamic_brush_coord = (5, 5)
        e.dynamic_brush_scratch_zone = (0, 0, 2000, 600)
        e.update_dynamic_brush_settings(min_value=0.0, max_value=1.0, default_value=0.0, step=0.01)
        # every probe returns the SAME radius regardless of the value applied
        e._dynamic_brush_v2_probe = lambda value, x, y, half, *, region_rect=None: {  # type: ignore[assignment]
            "radius_px": 2.0, "area_px": math.pi * 4, "density": 1.0, "shape": "circle", "confidence": 0.9,
        }
        e._apply_dynamic_brush_value = lambda *a, **k: True  # type: ignore[assignment]
        result = e._run_dynamic_brush_calibration(
            calibration_region=(0, 0, 2000, 600), persist=False, status_messages=False
        )
        self.assertIsNone(result, "degenerate calibration must be refused, not saved")
        self.assertIsNone(e.dynamic_brush_calibration)


class CleanScratchSpotTests(unittest.TestCase):
    def test_blank_spot_finder_avoids_painted_area(self):
        """Re-learn on a dirty scratch must stamp on BLANK canvas, not on leftover
        stamps from a previous run (the 'can't collect enough samples' bug)."""
        from unittest.mock import patch
        from PIL import Image
        e = OlegPainter()
        scratch = (0, 0, 400, 200)
        # left half painted black (old stamps), right half blank white
        img = np.full((200, 400, 3), 255, np.uint8)
        img[:, :200] = 0
        with patch("engine.olegpainter.dynamic_brush.capture_screen") as grab:
            grab.return_value = Image.fromarray(img)
            spots = e._dynamic_brush_v2_clean_spots(scratch, 30, 4)
        self.assertGreaterEqual(len(spots), 1)
        # every chosen spot must lie in the blank right half
        for sx, sy in spots:
            self.assertGreater(sx, 200, f"spot {sx},{sy} landed on the painted half")

    def test_falls_back_to_grid_when_grab_fails(self):
        from unittest.mock import patch
        e = OlegPainter()
        with patch("engine.olegpainter.dynamic_brush.capture_screen") as grab:
            grab.side_effect = RuntimeError("no screen")
            spots = e._dynamic_brush_v2_clean_spots((0, 0, 400, 200), 30, 4)
        self.assertGreaterEqual(len(spots), 1)  # grid fallback still yields spots

    def test_patch_half_sized_from_scratch_not_value_labels(self):
        e = OlegPainter()
        # huge value labels must NOT blow up the patch (points-mode values are labels)
        e.dynamic_brush_control_mode = "points"
        e.update_dynamic_brush_settings(min_value=1.0, max_value=999.0, default_value=1.0, step=0.01)
        half = e._dynamic_brush_patch_half(region=(0, 0, 600, 300))
        self.assertLessEqual(half, 210)  # bounded by the scratch short side, not the 999/1 ratio
        self.assertGreaterEqual(half, 20)


class AdaptiveCalibrationTests(unittest.TestCase):
    def test_nonlinear_slider_gets_densified(self):
        e = OlegPainter()
        e.brush_size = 1
        e.dynamic_brush_control_mode = "text"
        e.dynamic_brush_coord = (10, 10)
        e.update_dynamic_brush_settings(min_value=0.05, max_value=1.0, default_value=0.1, step=0.01)
        probed = []

        def fake_probe(value, x, y, half, *, region_rect=None):
            probed.append(float(value))
            r = max(0.5, 400.0 * float(value) ** 2)  # strongly non-linear control
            return {"radius_px": r, "area_px": math.pi * r * r, "density": 1.0, "shape": "circle", "confidence": 0.9}

        e._dynamic_brush_v2_probe = fake_probe  # type: ignore[assignment]
        e._apply_dynamic_brush_value = lambda *a, **k: True  # type: ignore[assignment]
        payload = e._run_dynamic_brush_calibration(
            calibration_region=(0, 0, 2000, 400), persist=False, status_messages=False
        )
        self.assertIsInstance(payload, dict)
        # 5 base probes + at least one adaptive subdivision of the steep gap
        self.assertGreaterEqual(len(probed), 6)
        self.assertGreaterEqual(payload["sample_count"], 6)


class DynamicFillSmokeTests(unittest.TestCase):
    def _engine(self):
        e = OlegPainter()
        e.brush_size = 2
        e.draw_region = (0, 0, 160, 160)
        e.draw_delay = 0.0
        e.pen_settle_delay = 0.0
        e.pen_button_delay = 0.0
        e.mouse_release_settle = 0.0
        e.drawing_enabled = True
        e.stop_flag = False
        # physical IO -> recorders/no-ops
        e.applied_values = []
        e._apply_dynamic_brush_value = lambda value, force=False, _e=e: (_e.applied_values.append(float(value)) or True)  # type: ignore[assignment]
        e._move_abs = lambda *a, **k: None  # type: ignore[assignment]
        e._pen_down = lambda *a, **k: None  # type: ignore[assignment]
        e._mouse_up_with_settle = lambda *a, **k: True  # type: ignore[assignment]
        e.draw_line = lambda *a, **k: None  # type: ignore[assignment]
        return e

    def _color_setup(self, e, blob=slice(10, 70)):
        h = w = 80
        e.cluster_map = np.full((h, w), -1, dtype=int)
        e.cluster_map[blob, blob] = 0
        e.drawn_mask = np.zeros((h, w), dtype=bool)
        return ((e.cluster_map == 0) & (~e.drawn_mask)).astype(np.uint8) * 255

    def test_tiers_then_base_full_coverage_no_spill_few_size_changes(self):
        e = self._engine()
        # radii 1..24 px: base brush reachable, big tier available
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.05, 1.0), (0.5, 12.0), (1.0, 24.0)])
        mask_u8 = self._color_setup(e)
        e.dfs_4dir_dynamic_fill_current_color(mask_u8, 0, 0, 0)
        color = e.cluster_map == 0
        self.assertTrue(bool(np.all(e.drawn_mask[color])), "color not fully covered")
        self.assertEqual(int(np.count_nonzero(e.drawn_mask & (~color))), 0, "claimed cells outside the color")
        # base verify + >=1 tier + base restore; bounded by verify attempts
        self.assertGreaterEqual(len(e.applied_values), 3)
        self.assertLessEqual(len(e.applied_values), 7)
        base_value = e._dyn2_base_value
        self.assertAlmostEqual(e.applied_values[-1], float(base_value), places=4)
        # at least one TIER value larger than the base was applied in between
        self.assertTrue(any(v > float(base_value) for v in e.applied_values[1:-1]))

    def test_small_region_skips_dynamic_entirely(self):
        e = self._engine()
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.05, 1.0), (1.0, 24.0)])
        mask_u8 = self._color_setup(e, blob=slice(10, 18))  # 8x8 cells: no tier can fit
        e.dfs_4dir_dynamic_fill_current_color(mask_u8, 0, 0, 0)
        color = e.cluster_map == 0
        self.assertTrue(bool(np.all(e.drawn_mask[color])))
        # No tier fits, but the detail size is still set explicitly: the control
        # must never keep a random size left by calibration (live Paint failure).
        # (the real control deduplicates a repeated identical value)
        self.assertEqual(set(e.applied_values), {float(e._dyn2_base_value)})

    def test_run_tiers_gates_out_without_touching_control_on_small_blob(self):
        e = self._engine()
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.05, 1.0), (1.0, 24.0)])
        self._color_setup(e, blob=slice(10, 22))  # 12x12: pre-gate rejects before any apply
        self.assertFalse(e._dynamic_brush_v2_run_tiers(0, 0, 0))
        self.assertEqual(len(e.applied_values), 0)

    def test_unreachable_base_uses_tiers_and_smallest_size_for_details(self):
        e = self._engine()
        # calibrated brush can't go small enough to match the 2 px base
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.05, 6.0), (1.0, 24.0)])
        mask_u8 = self._color_setup(e)
        e.dfs_4dir_dynamic_fill_current_color(mask_u8, 0, 0, 0)
        color = e.cluster_map == 0
        self.assertTrue(bool(np.all(e.drawn_mask[color])))
        self.assertEqual(int(np.count_nonzero(e.drawn_mask & (~color))), 0)
        self.assertTrue(any(v > 0.05 for v in e.applied_values[:-1]), "big tier was not used")
        self.assertAlmostEqual(e.applied_values[-1], 0.05, places=4)


class DrawTimeClosedLoopTests(unittest.TestCase):
    def _engine(self, *, verify=False):
        e = OlegPainter()
        e.brush_size = 2
        e.draw_region = (0, 0, 160, 160)
        e.draw_delay = 0.0
        e.pen_settle_delay = 0.0
        e.pen_button_delay = 0.0
        e.drawing_enabled = True
        e.stop_flag = False
        e.dynamic_brush_verify_at_draw = bool(verify)
        e.cluster_map = np.full((80, 80), -1, dtype=int)
        e.cluster_map[10:70, 10:70] = 0
        e.drawn_mask = np.zeros((80, 80), dtype=bool)
        e.applied = []
        e._apply_dynamic_brush_value = lambda v, force=False: (e.applied.append(float(v)) or True)  # type: ignore[assignment]
        e._move_abs = lambda *a, **k: None  # type: ignore[assignment]
        e._pen_down = lambda *a, **k: None  # type: ignore[assignment]
        e._mouse_up_with_settle = lambda *a, **k: True  # type: ignore[assignment]
        e.draw_line = lambda *a, **k: None  # type: ignore[assignment]
        return e

    def test_lying_curve_cannot_spill_tiers_get_gated_by_measurement(self):
        # opt-in verify path: a brush that really paints 90px must be gated out
        e = self._engine(verify=True)
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.05, 1.0), (1.0, 24.0)])
        measured = []

        def fat_reality(value):
            measured.append(float(value))
            return True, 90.0  # the real stamp is 90px — way beyond any tier plan

        e._dynamic_brush_v2_apply_and_measure = fat_reality  # type: ignore[assignment]
        ran = e._dynamic_brush_v2_run_tiers(0, 0, 0)
        self.assertFalse(ran, "a 90px-real brush must never paint a tier planned for <=24px")
        self.assertEqual(int(np.count_nonzero(e.drawn_mask)), 0)
        self.assertGreaterEqual(len(measured), 1)

    def test_measured_radius_shrinks_the_tier_plan(self):
        e = self._engine(verify=True)
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.05, 1.0), (1.0, 24.0)])

        def honest_but_smaller(value):
            # the app actually paints 60% of what the curve promises
            predicted = e._dynamic_brush_v2_radius_px_for_value(float(value)) or 0.0
            return True, max(1.0, predicted * 0.6)

        e._dynamic_brush_v2_apply_and_measure = honest_but_smaller  # type: ignore[assignment]
        ran = e._dynamic_brush_v2_run_tiers(0, 0, 0)
        self.assertTrue(ran)
        color = e.cluster_map == 0
        self.assertGreater(int(np.count_nonzero(e.drawn_mask[color])), 0)
        self.assertEqual(int(np.count_nonzero(e.drawn_mask & (~color))), 0)

    def test_base_verify_corrects_down_the_curve_then_caches(self):
        e = self._engine(verify=True)
        e.brush_size = 8  # base target radius 4px, acceptance limit 7.6px
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.05, 1.0), (0.5, 12.0), (1.0, 24.0)])
        calls = []

        def reality(value):
            calls.append(float(value))
            # the app paints 3x the curve for small values: the first base guess
            # lands fat (12px > 7.6px) and must be corrected down the curve
            predicted = e._dynamic_brush_v2_radius_px_for_value(float(value)) or 1.0
            return True, predicted * 3.0

        e._dynamic_brush_v2_apply_and_measure = reality  # type: ignore[assignment]
        e._dyn2_base_value = None
        value = e._dynamic_brush_v2_ensure_base_value()
        self.assertIsNotNone(value)
        self.assertGreaterEqual(len(calls), 2)  # initial guess + >=1 correction
        self.assertLess(calls[-1], calls[0])    # corrected DOWN the curve
        before = len(calls)
        self.assertEqual(e._dynamic_brush_v2_ensure_base_value(), value)
        self.assertEqual(len(calls), before)    # cached, no re-probing

    def test_base_verify_declares_unreachable_at_curve_bottom(self):
        e = self._engine(verify=True)
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.05, 1.0), (0.5, 12.0), (1.0, 24.0)])
        calls = []

        def reality(value):
            calls.append(float(value))
            return True, 5.0  # whatever the value, the stamp stays fat (>1.9px limit)

        e._dynamic_brush_v2_apply_and_measure = reality  # type: ignore[assignment]
        e._dyn2_base_value = None
        self.assertIsNone(e._dynamic_brush_v2_ensure_base_value())
        self.assertIs(e._dyn2_base_value, False)
        before = len(calls)
        self.assertIsNone(e._dynamic_brush_v2_ensure_base_value())
        self.assertEqual(len(calls), before)  # cached verdict, no re-probing

    def test_big_brush_is_chosen_when_it_fits(self):
        """THE complaint: 'big brushes in the arsenal, still picks the small one'.
        TRUST default (no draw-time stamps): the very first tier must apply a
        radius close to the largest one that geometrically fits."""
        e = self._engine()  # trust (verify off)
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.05, 1.0), (0.5, 20.0), (1.0, 40.0)])
        ran = e._dynamic_brush_v2_run_tiers(0, 0, 0)
        self.assertTrue(ran)
        # 60x60-cell square, brush 2 -> the biggest calibrated brush (40px, value
        # 1.0) fits and must be the FIRST applied — never demoted to a small one.
        self.assertAlmostEqual(max(e.applied), 1.0, places=4)
        color = e.cluster_map == 0
        self.assertGreater(int(np.count_nonzero(e.drawn_mask[color])), 200)
        self.assertEqual(int(np.count_nonzero(e.drawn_mask & (~color))), 0)

    def test_drawtime_measure_failure_does_not_demote_to_small_brush(self):
        """The user's MS Paint bug: a hostile draw-time re-measure must NOT cause
        the big brush to be skipped. In trust mode the re-measure isn't even
        called, so the big brush is always used."""
        e = self._engine()  # trust (verify off)
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.05, 1.0), (0.5, 20.0), (1.0, 40.0)])
        measured = []
        e._dynamic_brush_v2_apply_and_measure = lambda v: (measured.append(v) or (True, 4.0))  # type: ignore[assignment]
        # also run the FULL fill (base-verify must not bail to static either)
        mask0 = ((e.cluster_map == 0) & (~e.drawn_mask)).astype(np.uint8) * 255
        e.dfs_4dir_dynamic_fill_current_color(mask0, 0, 0, 0)
        self.assertEqual(measured, [], "trust mode must not re-measure during the draw")
        self.assertAlmostEqual(max(e.applied), 1.0, places=4)  # big brush used
        color = e.cluster_map == 0
        self.assertTrue(bool(np.all(e.drawn_mask[color])))  # fully covered
        self.assertEqual(int(np.count_nonzero(e.drawn_mask & (~color))), 0)

    def test_sparse_point_presets_use_the_big_preset(self):
        e = self._engine()  # trust
        e.dynamic_brush_control_mode = "points"
        e.dynamic_brush_points = [
            {"x": 1, "y": 1, "value": 1.0},   # base-size preset (2px stamp)
            {"x": 2, "y": 2, "value": 9.0},   # BIG preset (40px stamp)
        ]
        e.dynamic_brush_calibration = _calibration_payload(e, [(1.0, 2.0), (9.0, 40.0)])
        ran = e._dynamic_brush_v2_run_tiers(0, 0, 0)
        self.assertTrue(ran, "the big preset fits the 60x60 core and must be used")
        self.assertIn(9.0, e.applied)
        self.assertEqual(int(np.count_nonzero(e.drawn_mask & (~(e.cluster_map == 0)))), 0)

    def test_stamps_only_tier_never_draws_lines(self):
        e = self._engine()  # trust
        e.cluster_map = np.full((80, 80), -1, dtype=int)
        e.cluster_map[10:36, 10:36] = 0  # 26x26 core: only isolated stamps fit a 20px brush
        e.drawn_mask = np.zeros((80, 80), dtype=bool)
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.05, 1.0), (1.0, 20.0)])
        lines = []
        e.draw_line = lambda *a, **k: lines.append(a)  # type: ignore[assignment]
        ran = e._dynamic_brush_v2_run_tiers(0, 0, 0)
        self.assertTrue(ran)
        self.assertEqual(len(lines), 0, "stamps-only tier must keep the pen lifted between nodes")
        self.assertGreater(int(np.count_nonzero(e.drawn_mask)), 0)
        self.assertEqual(int(np.count_nonzero(e.drawn_mask & (~(e.cluster_map == 0)))), 0)

    def test_base_value_trusts_calibration_without_stamping(self):
        e = self._engine()  # trust
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.05, 1.0), (1.0, 24.0)])
        calls = []
        e._dynamic_brush_v2_apply_and_measure = lambda v: (calls.append(v) or (True, 1.0))  # type: ignore[assignment]
        e._dyn2_base_value = None
        value = e._dynamic_brush_v2_ensure_base_value()
        self.assertIsNotNone(value)
        self.assertEqual(calls, [], "trust mode resolves the base value from the table, no stamp")
        self.assertEqual(e._dynamic_brush_v2_ensure_base_value(), value)  # cached

    def test_base_verify_accepts_honest_brush_and_caches_value(self):
        e = self._engine(verify=True)
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.05, 1.0), (1.0, 24.0)])
        calls = []

        def honest(value):
            calls.append(float(value))
            return True, e._dynamic_brush_v2_radius_px_for_value(float(value)) or 1.0

        e._dynamic_brush_v2_apply_and_measure = honest  # type: ignore[assignment]
        e._dyn2_base_value = None
        value = e._dynamic_brush_v2_ensure_base_value()
        self.assertIsNotNone(value)
        self.assertEqual(len(calls), 1)
        self.assertEqual(e._dynamic_brush_v2_ensure_base_value(), value)
        self.assertEqual(len(calls), 1)  # cached


if __name__ == "__main__":
    unittest.main()


class BrushPassOrderTests(unittest.TestCase):
    def test_detail_pass_paints_thin_parts_last_and_coarse_skips_thin_colors(self):
        e = OlegPainter()
        e.brush_size = 1
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.0, 1.4), (0.5, 12.0), (1.0, 24.0)])
        e.cluster_map = np.zeros((80, 80), dtype=int)       # sky
        e.cluster_map[10:12, 5:75] = 1                      # 2 px line
        e.cluster_map[30:70, 20:60] = 2                     # big block
        e.drawn_mask = np.zeros((80, 80), dtype=bool)
        e.color_palette = [("#000000", 1, 140, (0, 0, 0)), ("#00A2E8", 0, 0, (0, 162, 232)),
                           ("#FFF200", 2, 1600, (255, 242, 0))]
        self.assertFalse(e._brush_pass_has_work(1, "coarse"))
        self.assertTrue(e._brush_pass_has_work(2, "coarse"))
        order = e._brush_pass_color_order("detail")
        self.assertEqual(e.color_palette[order[-1]][1], 1, "thin line must be drawn last")


class PocketFillTests(unittest.TestCase):
    def test_pocket_strokes_never_touch_other_colors_and_cover_the_interior(self):
        e = OlegPainter()
        e.brush_size = 1
        e.draw_region = (0, 0, 120, 90)
        e.pen_settle_delay = 0.0
        e.drawing_enabled = True
        e.stop_flag = False
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.0, 1.4), (0.1, 3.0), (0.2, 5.0), (0.3, 8.0),
                                                              (0.5, 12.0), (0.7, 18.0), (0.9, 26.0), (1.0, 30.0)])
        e.cluster_map = np.zeros((90, 120), dtype=int)
        yy, xx = np.mgrid[:90, :120]
        e.cluster_map[(yy - 45) ** 2 + (xx - 45) ** 2 <= 38 ** 2] = 1
        e.cluster_map[40:44, 10:80] = 2                      # thin bar across the disk
        e.drawn_mask = np.zeros((90, 120), dtype=bool)
        canvas = np.full((90, 120), -1, dtype=np.int32)
        state = {"pos": (0, 0), "down": False, "r": 0.5}
        sizes = []

        def paint(a, b):
            # exact round stamp swept along the segment (cv2 thick lines overshoot)
            (ax, ay), (bx, by) = a, b
            vx, vy = bx - ax, by - ay
            length = vx * vx + vy * vy
            t = np.clip(((xx - ax) * vx + (yy - ay) * vy) / length, 0, 1) if length else 0
            canvas[np.hypot(xx - (ax + t * vx), yy - (ay + t * vy)) <= state["r"]] = 1

        def move(x, y):
            if state["down"]:
                paint(state["pos"], (x, y))
            state["pos"] = (x, y)

        def down(*_a, **_k):
            state["down"] = True
            paint(state["pos"], state["pos"])

        def up(*_a, **_k):
            state["down"] = False
            return True

        def line(x1, y1, x2, y2, **_k):
            move(x1, y1)
            paint((x1, y1), (x2, y2))
            state["pos"] = (x2, y2)

        def apply(value, force=False):
            state["r"] = e._dynamic_brush_v2_radius_px_for_value(value)
            sizes.append(value)
            return True

        e._move_abs, e._pen_down, e._mouse_up_with_settle, e.draw_line = move, down, up, line
        e._apply_dynamic_brush_value = apply
        full = e.cluster_map == 1
        self.assertTrue(e._pocket_fill(1, 0, 0, e._pocket_coarse_radii(full)))
        self.assertEqual(int(np.count_nonzero((canvas == 1) & ~full)), 0, "stroke touched another color")
        self.assertEqual(int(np.count_nonzero(e.drawn_mask & ~(canvas == 1))), 0, "claimed unpainted cells")
        self.assertGreater(np.count_nonzero(e.drawn_mask & full) / np.count_nonzero(full), 0.85)
        self.assertLessEqual(len(sizes), e._POCKET_MAX_SIZES)


class SizeMajorAndMergeTests(unittest.TestCase):
    def test_identical_final_colours_become_one_cluster(self):
        e = OlegPainter()
        e.cluster_map = np.array([[1, 1, 2], [3, 3, 2]])
        e.color_palette = [("#99D9EA", 1, 2, (153, 217, 234)), ("#FFF200", 2, 2, (255, 242, 0)),
                           ("#99d9ea", 3, 2, (153, 217, 234))]
        self.assertEqual(e._merge_identical_palette_colors(-1), 1)
        self.assertEqual(set(np.unique(e.cluster_map)), {1, 2})
        self.assertEqual([(h, c, n) for h, c, n, _ in e.color_palette], [("#99D9EA", 1, 4), ("#FFF200", 2, 2)])

    def test_coarse_pass_sets_each_size_once_for_all_colours(self):
        e = OlegPainter()
        e.brush_size = 1
        e.draw_region = (0, 0, 160, 80)
        e.pen_settle_delay = 0.0
        e.drawing_enabled = True
        e.stop_flag = False
        e.dynamic_brush_calibration = _calibration_payload(e, [(0.0, 1.4), (0.3, 6.0), (0.6, 12.0), (1.0, 20.0)])
        e.cluster_map = np.zeros((80, 160), dtype=int)
        e.cluster_map[:, 80:] = 1
        e.drawn_mask = np.zeros((80, 160), dtype=bool)
        e.color_palette = [("#000000", 0, 6400, (0, 0, 0)), ("#FFFFFF", 1, 6400, (255, 255, 255))]
        applied, picks = [], []
        e._apply_dynamic_brush_value = lambda v, force=False: applied.append(v) or True
        e.pick_color = lambda hex_color, cluster_id=None: picks.append(cluster_id)
        e._move_abs = lambda *a: None
        e._pen_down = lambda *a, **k: None
        e._mouse_up_with_settle = lambda *a, **k: True
        e.draw_line = lambda *a, **k: None
        e._draw_coarse_size_major(0, 0)
        self.assertEqual(len(applied), len(set(applied)), "a size was set more than once")
        self.assertGreaterEqual(len(picks), 2)
        self.assertGreater(np.count_nonzero(e.drawn_mask), 0.7 * e.drawn_mask.size)



class SliderRobustnessTests(unittest.TestCase):
    def test_plateau_keeps_the_interior_value_so_a_missed_end_click_is_never_used(self):
        e = OlegPainter()
        e.brush_size = 1
        # live Paint: 0.76 and 1.0 measured the same stamp — the end click missed
        e.dynamic_brush_calibration = _calibration_payload(
            e, [(0.0, 1.1), (0.24, 12.5), (0.5, 36.5), (0.76, 71.5), (1.0, 71.5)])
        radii, values = e._dynamic_brush_v2_radius_table()
        self.assertEqual(list(values), [0.0, 0.24, 0.5, 0.76])
        self.assertAlmostEqual(e._dynamic_brush_v2_value_for_radius_px(71.5), 0.76)


class SliderTrackTests(unittest.TestCase):
    def test_track_line_is_found_beside_a_sloppy_frame(self):
        strip = np.full((120, 21, 3), 32, dtype=np.uint8)      # dark Paint panel
        strip[:, 14] = (90, 90, 90)                             # thin grey track, 4 px right of centre
        strip[50:58, 11:18] = (0, 120, 215)                     # the thumb
        self.assertEqual(OlegPainter.slider_track_offset(strip), 14)
        self.assertIsNone(OlegPainter.slider_track_offset(np.full((120, 21, 3), 32, dtype=np.uint8)))



class SliderPressTests(unittest.TestCase):
    def test_plain_slider_press_holds_on_target_and_rocks_before_release(self):
        e = OlegPainter()
        e.dynamic_brush_control_mode = "slider"
        e.dynamic_brush_slider_params = ("horizontal", 40.0, 500.0, 100.0)
        e.update_dynamic_brush_settings(min_value=0.0, max_value=1.0, default_value=0.0, step=0.01)
        events = []
        e._click_abs = lambda x, y: events.append(("move", x, y))  # type: ignore[assignment]
        from unittest.mock import patch
        with patch("engine.olegpainter.core.time.sleep", lambda *_a, **_k: None):
            with patch.object(e._input.backend, "button") as fake:
                fake.side_effect = lambda button, down: events.append(("down" if down else "up",))
                self.assertTrue(e._apply_dynamic_brush_value(0.5, force=True))
        down = events.index(("down",))
        up = events.index(("up",))
        target = events[down - 1]
        held = [ev[1] for ev in events[down + 1:up]]
        self.assertEqual(target, ("move", 300, 40))
        self.assertIn(302, held)
        self.assertIn(298, held)
        self.assertEqual(held[-1], 300)

    def test_ends_are_glided_to_from_inside_and_never_passed(self):
        # «Нарисуй меня!» (live 2026-10-01): a press on an end cap is ignored and a
        # step past an end lands on the ◀ ▶ buttons beside the track.
        e = OlegPainter()
        e.dynamic_brush_control_mode = "slider"
        e.dynamic_brush_slider_params = ("horizontal", 740.0, 1445.0, 1170.0)
        e.update_dynamic_brush_settings(min_value=0.0, max_value=1.0, default_value=0.0, step=0.005)
        from unittest.mock import patch
        for value, release_x in ((0.0, 1170), (1.0, 1445), (0.01, 1174)):
            events = []
            e._click_abs = lambda x, y, events=events: events.append(("move", x, y))  # type: ignore[assignment]
            with patch("engine.olegpainter.core.time.sleep", lambda *_a, **_k: None):
                with patch.object(e._input.backend, "button") as fake:
                    fake.side_effect = lambda button, down, events=events: events.append(("down" if down else "up",))
                    self.assertTrue(e._apply_dynamic_brush_value(value, force=True))
            press = events[events.index(("down",)) - 1][1]
            release = events[events.index(("up",)) - 1][1]
            self.assertTrue(1192 <= press <= 1423, (value, press))   # inside, clear of the end caps
            self.assertEqual(release, release_x)
            self.assertTrue(all(1170 <= ev[1] <= 1445 for ev in events if ev[0] == "move"), value)


class TooBigForZoneTests(unittest.TestCase):
    def test_largest_size_that_does_not_fit_the_zone_becomes_the_upper_limit(self):
        from unittest.mock import Mock
        e = OlegPainter()
        e.brush_size = 1
        e.draw_region = (0, 0, 400, 400)
        e.dynamic_brush_control_mode = "slider"
        e.dynamic_brush_slider_params = ("vertical", 10.0, 100.0, 400.0)
        e.update_dynamic_brush_settings(min_value=0.0, max_value=1.0, default_value=0.2, step=0.02)
        e._dynamic_brush_v2_clean_spots = lambda *a: [(i * 100, 200) for i in range(20)]
        e._apply_dynamic_brush_value = Mock(return_value=True)

        def probe(value, *a, **k):
            if value >= 1.0:
                return None                      # clipped: bigger than the test zone
            radius = 2 + 60 * value
            return dict(radius_px=radius, area_px=math.pi * radius ** 2, density=1, shape="circle", confidence=1)

        e._dynamic_brush_v2_probe = probe
        e._dynamic_brush_v2_verify = Mock(return_value=dict(passed=True, relative_error=0))
        result = e._run_dynamic_brush_calibration(calibration_region=(0, 0, 2000, 600), persist=False, status_messages=False)
        self.assertIsInstance(result, dict)
        self.assertEqual(max(result["required_values"]), 0.8)   # the last track step before 1.0



class SliderEndsTests(unittest.TestCase):
    def test_track_run_bridges_the_thumb_edge_and_ignores_stray_marks(self):
        line = np.zeros(200, dtype=bool)
        line[20:90] = True
        line[92:170] = True            # 2 px gap at the thumb's edge
        line[190] = True               # an unrelated mark further away
        self.assertEqual(OlegPainter.slider_track_run(line), (20, 169))
