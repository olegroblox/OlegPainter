from __future__ import annotations

import os
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import engine.olegpainter.core as core_module
from engine.olegpainter.core import OlegPainter


def _v2_calibration(samples, *, shape: str = "circle", margin: float = 0.18, passed: bool = True) -> dict:
    rows = []
    for item in samples:
        rows.append(
            {
                "value": float(item["value"]),
                "point_reach_cells": float(item["point"]),
                "stroke_reach_h_cells": float(item.get("stroke_h", item["point"])),
                "stroke_reach_v_cells": float(item.get("stroke_v", item["point"])),
                "half_width_cells": float(item.get("half_w", item.get("stroke_v", item["point"]))),
                "half_height_cells": float(item.get("half_h", item.get("stroke_h", item["point"]))),
                "area_cells": float(item.get("area", max(1.0, item["point"] * item["point"]))),
                "shape_hint": str(item.get("shape_hint", shape)),
                "confidence": float(item.get("confidence", 0.9)),
            }
        )
    return {
        "version": 2,
        "samples": rows,
        "shape": shape,
        "shape_confidence": 0.9,
        "validation": {
            "target_rect": [0, 0, 20, 20],
            "outside_pixels": 0,
            "inside_coverage": 1.0 if passed else 0.0,
            "passed": bool(passed),
        },
        "runtime_correction": {
            "anchor_low_value": None,
            "anchor_high_value": None,
            "reach_scale": 1.0,
            "reach_bias": 0.0,
        },
        "safety_margin_cells": float(margin),
        "timestamp": 1.0,
    }


class PostDrawRepairTests(unittest.TestCase):
    def test_draw_image_resets_progress_when_previous_run_finished_palette(self) -> None:
        engine = OlegPainter()
        engine.color_picking_method = "hex_field"
        engine.status_callback = lambda *_args, **_kwargs: None
        engine.hex_input_coord = (10, 10)
        engine.draw_region = (0, 0, 10, 10)
        engine.color_palette = [("#111111", 0, 1, None)]
        engine.cluster_map = np.array([[0]], dtype=int)
        engine.drawn_mask = np.array([[False]], dtype=bool)
        engine.current_color_index = len(engine.color_palette)

        class _DummyThread:
            def __init__(self, target=None, daemon=None, name=None, args=()):
                self._target = target
                self._args = args
                self._alive = False

            def is_alive(self):
                return self._alive

            def start(self):
                self._alive = True

        with patch("engine.olegpainter.core.threading.Thread", _DummyThread):
            started = engine.draw_image()

        self.assertTrue(started)
        self.assertEqual(engine.current_color_index, 0)
        self.assertFalse(bool(engine.drawn_mask[0, 0]))

    def test_analyze_post_draw_repair_samples_detects_only_unchanged_background(self) -> None:
        engine = OlegPainter()
        baseline = np.full((2, 2, 3), 255, dtype=np.float32)
        expected = baseline.copy()
        final = baseline.copy()
        target_mask = np.array([[True, True], [False, True]], dtype=bool)

        expected[0, 0] = (10, 10, 10)
        expected[0, 1] = (245, 245, 245)
        expected[1, 1] = (180, 160, 140)
        final[1, 1] = (170, 150, 135)

        analysis = engine._analyze_post_draw_repair_samples(baseline, final, expected, target_mask)

        self.assertIsNotNone(analysis)
        assert analysis is not None
        self.assertEqual(analysis["detected_cells"], 1)
        self.assertEqual(analysis["unverifiable_cells"], 1)
        self.assertTrue(bool(analysis["hole_mask"][0, 0]))
        self.assertFalse(bool(analysis["hole_mask"][0, 1]))
        self.assertFalse(bool(analysis["hole_mask"][1, 1]))
        self.assertTrue(bool(analysis["unverifiable_mask"][0, 1]))

    def test_analyze_post_draw_repair_detects_weak_partial_change_as_hole(self) -> None:
        engine = OlegPainter()
        baseline = np.full((1, 1, 3), 255, dtype=np.float32)
        expected = np.array([[[60.0, 60.0, 60.0]]], dtype=np.float32)
        final = np.array([[[235.0, 235.0, 235.0]]], dtype=np.float32)
        target_mask = np.array([[True]], dtype=bool)

        analysis = engine._analyze_post_draw_repair_samples(baseline, final, expected, target_mask)

        self.assertIsNotNone(analysis)
        assert analysis is not None
        self.assertEqual(analysis["detected_cells"], 1)
        self.assertTrue(bool(analysis["hole_mask"][0, 0]))

    def test_partial_post_draw_repair_detects_gap_inside_brush_cell(self) -> None:
        engine = OlegPainter()
        engine.draw_region = (0, 0, 2, 2)
        engine.brush_size = 2
        engine.cluster_map = np.array([[0]], dtype=int)
        baseline = np.full((2, 2, 3), 255, dtype=np.uint8)
        expected = np.zeros((2, 2, 3), dtype=np.uint8)
        final = expected.copy()
        final[0, 0] = (255, 255, 255)

        hole_mask = engine._compute_partial_post_draw_repair_holes(
            baseline,
            final,
            expected,
            np.array([[True]], dtype=bool),
        )

        self.assertIsNotNone(hole_mask)
        assert hole_mask is not None
        self.assertTrue(bool(hole_mask[0, 0]))

    def test_post_draw_repair_sensitivity_can_promote_low_contrast_holes(self) -> None:
        engine = OlegPainter()
        baseline = np.full((1, 1, 3), 255, dtype=np.float32)
        expected = np.array([[[242.0, 242.0, 242.0]]], dtype=np.float32)
        final = baseline.copy()
        target_mask = np.array([[True]], dtype=bool)

        default_analysis = engine._analyze_post_draw_repair_samples(baseline, final, expected, target_mask)

        self.assertIsNotNone(default_analysis)
        assert default_analysis is not None
        self.assertEqual(default_analysis["detected_cells"], 0)
        self.assertEqual(default_analysis["unverifiable_cells"], 1)

        engine.post_draw_repair_sensitivity = 200
        aggressive_analysis = engine._analyze_post_draw_repair_samples(baseline, final, expected, target_mask)

        self.assertIsNotNone(aggressive_analysis)
        assert aggressive_analysis is not None
        self.assertEqual(aggressive_analysis["detected_cells"], 1)
        self.assertEqual(aggressive_analysis["unverifiable_cells"], 0)

    def test_post_draw_repair_aggressive_mode_detects_wrong_color_cells(self) -> None:
        engine = OlegPainter()
        baseline = np.full((1, 1, 3), 255, dtype=np.float32)
        expected = np.array([[[255.0, 0.0, 0.0]]], dtype=np.float32)
        final = np.array([[[0.0, 0.0, 255.0]]], dtype=np.float32)
        target_mask = np.array([[True]], dtype=bool)

        conservative = engine._analyze_post_draw_repair_samples(baseline, final, expected, target_mask)

        self.assertIsNotNone(conservative)
        assert conservative is not None
        self.assertEqual(conservative["detected_cells"], 0)
        self.assertEqual(conservative["color_mismatch_cells"], 0)

        engine.post_draw_repair_mode = "aggressive"
        aggressive = engine._analyze_post_draw_repair_samples(baseline, final, expected, target_mask)

        self.assertIsNotNone(aggressive)
        assert aggressive is not None
        self.assertEqual(aggressive["detected_cells"], 1)
        self.assertEqual(aggressive["color_mismatch_cells"], 1)
        self.assertTrue(bool(aggressive["color_mismatch_mask"][0, 0]))

    def test_post_draw_repair_color_mismatch_sensitivity_controls_aggressive_detection(self) -> None:
        engine = OlegPainter()
        engine.post_draw_repair_mode = "aggressive"
        baseline = np.full((1, 1, 3), 255, dtype=np.float32)
        expected = np.zeros((1, 1, 3), dtype=np.float32)
        final = np.array([[[64.0, 64.0, 64.0]]], dtype=np.float32)
        target_mask = np.array([[True]], dtype=bool)

        default_analysis = engine._analyze_post_draw_repair_samples(baseline, final, expected, target_mask)

        self.assertIsNotNone(default_analysis)
        assert default_analysis is not None
        self.assertEqual(default_analysis["detected_cells"], 0)

        engine.post_draw_repair_color_mismatch_sensitivity = 200
        aggressive_analysis = engine._analyze_post_draw_repair_samples(baseline, final, expected, target_mask)

        self.assertIsNotNone(aggressive_analysis)
        assert aggressive_analysis is not None
        self.assertEqual(aggressive_analysis["detected_cells"], 1)
        self.assertEqual(aggressive_analysis["color_mismatch_cells"], 1)

    def test_partial_post_draw_repair_aggressive_mode_detects_color_mismatch_inside_brush_cell(self) -> None:
        engine = OlegPainter()
        engine.post_draw_repair_mode = "aggressive"
        engine.draw_region = (0, 0, 2, 2)
        engine.brush_size = 2
        engine.cluster_map = np.array([[0]], dtype=int)
        baseline = np.full((2, 2, 3), 255, dtype=np.uint8)
        expected = np.zeros((2, 2, 3), dtype=np.uint8)
        final = expected.copy()
        final[0, 0] = (96, 96, 96)

        hole_mask = engine._compute_partial_post_draw_repair_holes(
            baseline,
            final,
            expected,
            np.array([[True]], dtype=bool),
        )

        self.assertIsNotNone(hole_mask)
        assert hole_mask is not None
        self.assertTrue(bool(hole_mask[0, 0]))

    def test_manual_palette_capture_ignores_rapid_duplicate_clicks(self) -> None:
        engine = OlegPainter()
        engine.status_callback = lambda *_args, **_kwargs: None
        self.addCleanup(lambda: engine._end_capture_session(wait=True))
        holder: dict[str, object] = {}

        class _DummyListener:
            def __init__(self, on_click=None, *args, **kwargs):
                holder["on_click"] = on_click

            def start(self):
                return None

            def is_alive(self):
                return True

            def stop(self):
                return None

        with patch("engine.olegpainter.core.mouse.Listener", _DummyListener), patch(
            "engine.olegpainter.palette_capture.WindowSampleRequest.at",
            return_value=SimpleNamespace(point=(100, 100)),
        ), patch(
            "engine.olegpainter.palette_capture.sample_window_pixel", return_value=(255, 255, 255)
        ) as sampler:
            engine.start_manual_palette_capture()
            engine.ignore_clicks_until = 0.0
            on_click = holder.get("on_click")
            self.assertIsNotNone(on_click)
            assert on_click is not None
            left_button = core_module.mouse.Button.left
            on_click(100, 100, left_button, True)
            on_click(100, 100, left_button, True)
            engine.finish_manual_palette_capture()
            self.assertTrue(engine._capture_session.finished.wait(2))
            sampler.assert_called_once()

        self.assertEqual(len(engine.manual_palette_coords), 1)

    def test_dense_repair_fill_cluster_cells_expands_same_cluster_and_runs_two_passes(self) -> None:
        engine = OlegPainter()
        engine.draw_region = (5, 7, 3, 3)
        engine.brush_size = 1
        engine.cluster_map = np.array(
            [
                [0, 0, 1],
                [0, 0, 1],
                [1, 1, 1],
            ],
            dtype=int,
        )
        calls: list[tuple[str, np.ndarray, int, int]] = []

        def fake_fill(pixel_mask, *, x0, y0, orientation):
            calls.append((orientation, np.asarray(pixel_mask, dtype=bool).copy(), x0, y0))
            return True

        engine._dense_repair_fill_pixel_mask = fake_fill  # type: ignore[assignment]

        ok = engine._dense_repair_fill_cluster_cells(
            np.array(
                [
                    [True, False, False],
                    [False, False, False],
                    [False, False, False],
                ],
                dtype=bool,
            ),
            0,
        )

        self.assertTrue(ok)
        self.assertEqual([call[0] for call in calls], ["horizontal", "vertical"])
        self.assertEqual((calls[0][2], calls[0][3]), (5, 7))
        expected_mask = np.array(
            [
                [True, True, False],
                [True, True, False],
                [False, False, False],
            ],
            dtype=bool,
        )
        self.assertTrue(np.array_equal(calls[0][1], expected_mask))
        self.assertTrue(np.array_equal(calls[1][1], expected_mask))

    def test_dense_repair_fill_cluster_cells_uses_single_pass_for_large_dense_region(self) -> None:
        engine = OlegPainter()
        engine.draw_region = (0, 0, 6, 6)
        engine.brush_size = 1
        engine.cluster_map = np.zeros((6, 6), dtype=int)
        calls: list[str] = []

        def fake_fill(pixel_mask, *, x0, y0, orientation):
            calls.append(str(orientation))
            return True

        engine._dense_repair_fill_pixel_mask = fake_fill  # type: ignore[assignment]

        ok = engine._dense_repair_fill_cluster_cells(np.ones((6, 6), dtype=bool), 0)

        self.assertTrue(ok)
        self.assertEqual(calls, ["horizontal"])

    def test_perform_post_draw_repair_pass_uses_draw_entrypoint_for_standard_modes(self) -> None:
        engine = OlegPainter()
        engine.drawing_enabled = True
        engine.stop_flag = False
        engine.color_picking_method = "hex_field"
        engine.draw_region = (10, 20, 2, 2)
        engine.cluster_map = np.array([[0, 0], [1, 1]], dtype=int)
        engine.drawn_mask = np.ones((2, 2), dtype=bool)
        engine.color_palette = [
            ("#111111", 0, 2, None),
            ("#222222", 1, 2, None),
        ]
        hole_mask = np.array([[True, False], [False, False]], dtype=bool)
        calls: list[tuple] = []

        def fake_draw(color_data):
            pending = (engine.cluster_map == int(color_data[1])) & (~engine.drawn_mask)
            calls.append((color_data, pending.copy()))
            pending = (engine.cluster_map == int(color_data[1])) & (~engine.drawn_mask)
            engine.drawn_mask[pending] = True

        engine._perform_draw_for_one_color = fake_draw  # type: ignore[assignment]

        repaired_cells = engine._perform_post_draw_repair_pass(hole_mask)

        self.assertEqual(repaired_cells, 1)
        self.assertEqual(calls[0][0], ("#111111", 0, 2, None))
        expected_pending = np.array([[True, True], [False, False]], dtype=bool)
        self.assertTrue(np.array_equal(calls[0][1], expected_pending))
        self.assertTrue(np.all(engine.drawn_mask))

    def test_draw_route_with_brush_single_cell_uses_nonzero_microstroke(self) -> None:
        engine = OlegPainter()
        engine.drawn_mask = np.zeros((1, 1), dtype=bool)
        moves: list[tuple[int, int, int, int]] = []

        engine._mouse_up_with_settle = lambda *args, **kwargs: True  # type: ignore[assignment]
        engine.draw_line = lambda x1, y1, x2, y2: moves.append((x1, y1, x2, y2))  # type: ignore[assignment]
        ok = engine._draw_route_with_brush([(0, 0)], 0, 0, (1, 1))

        self.assertTrue(ok)
        self.assertEqual(len(moves), 1)
        x1, y1, x2, y2 = moves[0]
        self.assertTrue((x1, y1) != (x2, y2))

    def test_color_layer_assignments_follow_distribution(self) -> None:
        engine = OlegPainter()
        engine.draw_with_layers_enabled = True
        engine.target_app_layer_coords = [(10, 10), (20, 20)]
        engine.color_palette = [
            ("#000001", 0, 1, None),
            ("#000002", 1, 1, None),
            ("#000003", 2, 1, None),
            ("#000004", 3, 1, None),
            ("#000005", 4, 1, None),
        ]

        distribution = engine._color_layer_distribution()
        assignments = engine._color_layer_assignments()

        self.assertEqual(distribution, [3, 2])
        self.assertEqual(assignments[0]["layer_index"], 0)
        self.assertEqual(assignments[1]["layer_index"], 0)
        self.assertEqual(assignments[2]["layer_index"], 0)
        self.assertEqual(assignments[3]["layer_index"], 1)
        self.assertEqual(assignments[4]["layer_index"], 1)

    def test_manual_mix_repair_pass_uses_same_draw_entrypoint(self) -> None:
        engine = OlegPainter()
        engine.drawing_enabled = True
        engine.stop_flag = False
        engine.color_picking_method = "manual_palette"
        engine.manual_palette_mix_enabled = True
        engine.cluster_map = np.array([[0]], dtype=int)
        engine.drawn_mask = np.ones((1, 1), dtype=bool)
        engine.color_palette = [("#111111", 0, 1, None)]
        calls: list[tuple] = []

        def fake_draw(color_data):
            calls.append(color_data)

        engine._perform_draw_for_one_color = fake_draw  # type: ignore[assignment]

        engine._perform_post_draw_repair_pass(np.array([[True]], dtype=bool))

        self.assertEqual(calls, [("#111111", 0, 1, None)])

    def test_dynamic_brush_quantization_uses_arbitrary_step_from_min_value(self) -> None:
        engine = OlegPainter()
        engine.update_dynamic_brush_settings(min_value=0.1, max_value=1.1, default_value=0.3, step=0.2)

        self.assertEqual(engine._quantize_dynamic_brush_value(0.34), 0.3)
        self.assertEqual(engine._quantize_dynamic_brush_value(0.96), 0.9)

    def test_legacy_slider_profile_preserves_capture_but_invalidates_old_calibration(self) -> None:
        engine = OlegPainter()
        engine.load_config(
            {
                "draw_region": [10, 10, 400, 300],
                "dynamic_brush_enabled": True,
                "dynamic_brush_control_mode": "slider",
                "dynamic_brush_min_value": 1.0,
                "dynamic_brush_max_value": 248.0,
                "dynamic_brush_default_value": 12.0,
                "dynamic_brush_step_value": 1.0,
                "dynamic_brush_profile": {
                    "version": 1,
                    "valid": True,
                    "control_mode": "slider",
                    "control_params": {
                        "orientation": "horizontal",
                        "fixed_ratio": 0.2,
                        "value_max_ratio": 0.95,
                        "value_min_ratio": 0.05,
                    },
                    "scratch_zone": {
                        "x_ratio": 0.1,
                        "y_ratio": 0.1,
                        "w_ratio": 0.25,
                        "h_ratio": 0.25,
                    },
                    "range": {
                        "min_value": 1.0,
                        "max_value": 248.0,
                        "default_value": 12.0,
                        "step_value": 1.0,
                    },
                    "cached_calibration": {
                        "slope": 40.0,
                        "intercept": 8.0,
                        "samples": 3,
                        "sample_points": [
                            {"reach_cells": 1.0, "value": 1.0},
                            {"reach_cells": 3.0, "value": 64.0},
                            {"reach_cells": 6.0, "value": 248.0},
                        ],
                    },
                },
                "dynamic_brush_calibration": {
                    "slope": 40.0,
                    "intercept": 8.0,
                    "samples": 3,
                    "sample_points": [
                        {"reach_cells": 1.0, "value": 1.0},
                        {"reach_cells": 3.0, "value": 64.0},
                        {"reach_cells": 6.0, "value": 248.0},
                    ],
                },
            }
        )

        self.assertFalse(bool(engine.dynamic_brush_profile.get("valid")))
        self.assertIsNone(engine.dynamic_brush_profile.get("cached_calibration"))
        self.assertEqual(engine.dynamic_brush_profile.get("value_scale"), "normalized")
        self.assertEqual(engine.dynamic_brush_profile.get("range", {}).get("min_value"), 0.0)
        self.assertEqual(engine.dynamic_brush_profile.get("range", {}).get("max_value"), 1.0)
        self.assertEqual(engine.dynamic_brush_min_value, 0.0)
        self.assertEqual(engine.dynamic_brush_max_value, 1.0)
        self.assertEqual(engine.dynamic_brush_default_value, 0.2)
        self.assertEqual(engine.dynamic_brush_step_value, 0.005)  # fine slider positions (BRUSH-004)
        self.assertIsNone(engine.dynamic_brush_calibration)
        self.assertEqual(engine.dynamic_brush_profile_state, "invalid")
        self.assertIsNotNone(engine.dynamic_brush_slider_params)
        self.assertIsNotNone(engine.dynamic_brush_scratch_zone)


if __name__ == "__main__":
    unittest.main()
