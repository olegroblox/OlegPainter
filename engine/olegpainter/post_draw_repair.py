"""Mixin extracted from engine/olegpainter/core.py.

Post-draw repair: gap detection, dense repair runs, repair-pass execution.
"""
from __future__ import annotations

import logging

import math
from collections import deque
from pathlib import Path  # noqa: F401  used by some method annotations

from .common import *  # noqa: F401,F403  shared numpy/Pillow/etc.
from .translations import tr, LANGUAGES, current_lang  # noqa: F401
from .calibration import (  # noqa: F401
    calib_grab_screen,
    calib_show_fullscreen_top,
    calib_select_two_points,
    calib_select_region,
)
from .cluster_cleanup import apply_cleanup_mode  # noqa: F401
from .color_spaces import perceptual_lightness_from_rgb  # noqa: F401
from .prep_pipeline import build_perceptual_cluster_map  # noqa: F401
from .viewport_state import (  # noqa: F401
    VIEWPORT_UNSET,
    full_area_rect,
    normalize_area_rect,
    normalize_desktop_rect,
)


log = logging.getLogger("olegpainter.engine.olegpainter.post_draw_repair")


class PostDrawRepairMixin:
    """Post-draw repair: gap detection, dense repair runs, repair-pass execution."""

    def _post_draw_repair_sample_box_size(self) -> int:
        brush = max(1, int(getattr(self, "brush_size", 1) or 1))
        if brush <= 2:
            return 1
        if brush <= 4:
            return 2
        return 3

    def _post_draw_repair_sensitivity_factor(self) -> float:
        try:
            percent = float(getattr(self, "post_draw_repair_sensitivity", 100) or 100.0)
        except Exception:
            percent = 100.0
        percent = max(50.0, min(200.0, percent))
        return percent / 100.0

    def _post_draw_repair_mode_value(self) -> str:
        return self._normalize_post_draw_repair_mode(getattr(self, "post_draw_repair_mode", "conservative"))

    def _post_draw_repair_detects_color_mismatch(self) -> bool:
        return self._post_draw_repair_mode_value() == "aggressive"

    def _post_draw_repair_color_mismatch_factor(self) -> float:
        try:
            percent = float(getattr(self, "post_draw_repair_color_mismatch_sensitivity", 100) or 100.0)
        except Exception:
            percent = 100.0
        percent = max(50.0, min(200.0, percent))
        return percent / 100.0

    def _post_draw_repair_thresholds(self) -> dict[str, float]:
        sensitivity = self._post_draw_repair_sensitivity_factor()
        color_sensitivity = self._post_draw_repair_color_mismatch_factor()
        verifiable_scale = max(0.5, min(1.6, 1.0 / sensitivity))
        same_bg_scale = max(0.75, min(1.5, 0.5 + (0.5 * sensitivity)))
        relative_scale = max(0.85, min(1.25, 0.75 + (0.25 * sensitivity)))
        distance_scale = max(0.9, min(1.2, 1.1 - (0.15 * (sensitivity - 1.0))))
        partial_ratio_scale = max(0.55, min(1.35, 1.0 / sensitivity))
        partial_bg_scale = max(0.85, min(1.2, 0.8 + (0.2 * sensitivity)))
        mismatch_scale = max(0.45, min(1.6, 1.0 / color_sensitivity))
        mismatch_ratio_scale = max(0.45, min(1.45, 1.0 / color_sensitivity))
        return {
            "min_expected_euclid_delta": self._POST_DRAW_REPAIR_MIN_EXPECTED_EUCLID_DELTA * verifiable_scale,
            "min_expected_max_channel_delta": self._POST_DRAW_REPAIR_MIN_EXPECTED_MAX_CHANNEL_DELTA * verifiable_scale,
            "same_bg_euclid_delta": self._POST_DRAW_REPAIR_SAME_BG_EUCLID_DELTA * same_bg_scale,
            "same_bg_max_channel_delta": self._POST_DRAW_REPAIR_SAME_BG_MAX_CHANNEL_DELTA * same_bg_scale,
            "bg_relative_euclid_ratio": self._POST_DRAW_REPAIR_BG_RELATIVE_EUCLID_RATIO * relative_scale,
            "bg_expected_distance_ratio": self._POST_DRAW_REPAIR_BG_EXPECTED_DISTANCE_RATIO * distance_scale,
            "bg_relative_max_channel_ratio": self._POST_DRAW_REPAIR_BG_RELATIVE_MAX_CHANNEL_RATIO * relative_scale,
            "bg_relative_max_channel_min": self._POST_DRAW_REPAIR_BG_RELATIVE_MAX_CHANNEL_MIN * relative_scale,
            "color_mismatch_euclid_delta": max(
                12.0,
                self._POST_DRAW_REPAIR_COLOR_MISMATCH_EUCLID_DELTA * mismatch_scale,
            ),
            "color_mismatch_max_channel_delta": max(
                8.0,
                self._POST_DRAW_REPAIR_COLOR_MISMATCH_MAX_CHANNEL_DELTA * mismatch_scale,
            ),
            "color_mismatch_expected_ratio": max(
                0.16,
                min(0.70, self._POST_DRAW_REPAIR_COLOR_MISMATCH_EXPECTED_RATIO * mismatch_ratio_scale),
            ),
            "color_mismatch_expected_max_ratio": max(
                0.16,
                min(0.70, self._POST_DRAW_REPAIR_COLOR_MISMATCH_EXPECTED_MAX_RATIO * mismatch_ratio_scale),
            ),
            "partial_min_expected_euclid_delta": max(
                10.0,
                (self._POST_DRAW_REPAIR_MIN_EXPECTED_EUCLID_DELTA * 0.45) * verifiable_scale,
            ),
            "partial_min_expected_max_channel_delta": max(
                6.0,
                (self._POST_DRAW_REPAIR_MIN_EXPECTED_MAX_CHANNEL_DELTA * 0.45) * verifiable_scale,
            ),
            "partial_missing_expected_ratio": max(0.16, min(0.50, 0.35 * partial_ratio_scale)),
            "partial_final_bg_ratio": max(0.65, min(1.0, 0.85 * partial_bg_scale)),
            "partial_ratio_threshold_scale": partial_ratio_scale,
            "partial_color_mismatch_euclid_delta": max(
                10.0,
                (self._POST_DRAW_REPAIR_COLOR_MISMATCH_EUCLID_DELTA * 0.75) * mismatch_scale,
            ),
            "partial_color_mismatch_max_channel_delta": max(
                6.0,
                (self._POST_DRAW_REPAIR_COLOR_MISMATCH_MAX_CHANNEL_DELTA * 0.75) * mismatch_scale,
            ),
            "partial_color_mismatch_expected_ratio": max(
                0.14,
                min(0.60, self._POST_DRAW_REPAIR_PARTIAL_COLOR_MISMATCH_EXPECTED_RATIO * mismatch_ratio_scale),
            ),
            "partial_color_mismatch_expected_max_ratio": max(
                0.14,
                min(0.60, self._POST_DRAW_REPAIR_PARTIAL_COLOR_MISMATCH_EXPECTED_MAX_RATIO * mismatch_ratio_scale),
            ),
            "partial_color_ratio_threshold": max(
                0.03,
                min(0.30, self._POST_DRAW_REPAIR_PARTIAL_COLOR_RATIO_THRESHOLD * mismatch_ratio_scale),
            ),
        }

    # Largest per-channel difference at which a canvas pixel counts as already
    # the target color (screen capture of a solid fill is exact or off by a few units).
    _CANVAS_MATCH_TOLERANCE = 8

    def _mark_canvas_matching_cells(self, canvas_rgb) -> int:
        """Count cells whose every canvas pixel already shows the target color as drawn.

        The target is the prepared preview, so it covers every color mode; background
        and hidden small components (alpha 0) are left to their own rules.
        """
        drawn = getattr(self, "drawn_mask", None)
        preview = getattr(self, "quantized_preview_image", None)
        if drawn is None or self.cluster_map is None or preview is None or canvas_rgb is None:
            return 0
        try:
            target = np.asarray(preview.convert("RGBA"), dtype=np.int16)
            canvas = np.asarray(canvas_rgb, dtype=np.int16)[:, :, :3]
        except Exception:
            log.debug("canvas match: bad images", exc_info=True)
            return 0
        brush = max(1, int(getattr(self, "brush_size", 1) or 1))
        rows, cols = drawn.shape[:2]
        h = min(target.shape[0], canvas.shape[0], rows * brush)
        w = min(target.shape[1], canvas.shape[1], cols * brush)
        full_rows, full_cols = h // brush, w // brush
        if full_rows <= 0 or full_cols <= 0:
            return 0
        h, w = full_rows * brush, full_cols * brush
        pixel_ok = (target[:h, :w, 3] > 0) & (
            np.abs(target[:h, :w, :3] - canvas[:h, :w]).max(axis=2) <= self._CANVAS_MATCH_TOLERANCE)
        cell_ok = pixel_ok.reshape(full_rows, brush, full_cols, brush).all(axis=(1, 3))
        region = drawn[:full_rows, :full_cols]
        fresh = cell_ok & ~region
        count = int(np.count_nonzero(fresh))
        if count:
            region[fresh] = True
            # Repair must still see these cells: a neighbour's stroke may spill onto
            # them, and "expected == baseline" alone would make them unverifiable.
            matched = np.zeros(drawn.shape[:2], dtype=bool)
            matched[:full_rows, :full_cols] = fresh
            self._canvas_matched_mask = matched
            self._log(f"Canvas already matches {count} cells; they are not drawn again.")
        return count

    @staticmethod
    def _post_draw_repair_axis_ranges(length_px: int, cell_count: int, brush: int, sample_size: int):
        if cell_count <= 0 or length_px <= 0:
            empty = np.zeros((0,), dtype=np.int32)
            return empty, empty, empty
        starts = np.arange(cell_count, dtype=np.int32) * int(max(1, brush))
        ends = np.minimum(starts + int(max(1, brush)), int(length_px))
        widths = np.maximum(1, ends - starts)
        sizes = np.minimum(widths, int(max(1, sample_size))).astype(np.int32, copy=False)
        centers = starts + ((widths - 1) // 2)
        offsets = sizes // 2
        range_starts = centers - offsets
        max_starts = ends - sizes
        range_starts = np.maximum(starts, np.minimum(range_starts, max_starts))
        range_ends = range_starts + sizes
        return range_starts.astype(np.int32, copy=False), range_ends.astype(np.int32, copy=False), sizes

    def _perform_post_draw_repair_with_current_algorithm(self, color_data_tuple, repair_mask: np.ndarray) -> bool:
        if self.drawn_mask is None:
            return False
        try:
            mask = np.asarray(repair_mask, dtype=bool)
        except Exception:
            return False
        if mask.shape != self.drawn_mask.shape or not np.any(mask):
            return False
        rows, cols = np.where(mask)
        if rows.size == 0:
            return False
        previous_values = self.drawn_mask[rows, cols].copy()
        try:
            self.drawn_mask[rows, cols] = False
            self._perform_draw_for_one_color(color_data_tuple)
        finally:
            try:
                if not self._automation_cancelled() and not self.stop_flag:
                    self.drawn_mask[rows, cols] = True
                else:
                    self.drawn_mask[rows, cols] = previous_values
            except Exception:
                log.debug('ignored exception in if not self._automation_cancelled() and (not self.stop_flag): self.drawn_mask...', exc_info=True)
        return not self._automation_cancelled() and not self.stop_flag

    @staticmethod
    def _dense_repair_runs(line_mask: np.ndarray) -> list[tuple[int, int]]:
        active = np.flatnonzero(line_mask)
        if active.size == 0:
            return []
        runs: list[tuple[int, int]] = []
        run_start = int(active[0])
        previous = int(active[0])
        for value in active[1:]:
            current = int(value)
            if current == previous + 1:
                previous = current
                continue
            runs.append((run_start, previous))
            run_start = current
            previous = current
        runs.append((run_start, previous))
        return runs

    def _dense_repair_draw_run(self, start_x: int, start_y: int, end_x: int, end_y: int) -> bool:
        while not self.drawing_enabled and not self.stop_flag:
            if not self._sleep_with_abort(0.01, step=0.01):
                return False
        if self._automation_cancelled():
            return False
        try:
            self._click_abs(start_x, start_y)
            time.sleep(0.005)
            if start_x == end_x and start_y == end_y:
                self._input_click(button="left")
            else:
                self._input.button_down("left")
                time.sleep(0.005)
                self.draw_line(start_x, start_y, end_x, end_y)
        except Exception as exc:
            self._log(f"Post-draw dense repair failed: {exc}", True)
            self.stop_flag = True
            return False
        finally:
            self._mouse_up_with_settle()
        return not self._automation_cancelled() and not self.stop_flag

    def _dense_repair_fill_pixel_mask(self, pixel_mask: np.ndarray, *, x0: int, y0: int, orientation: str) -> bool:
        if self._automation_cancelled():
            return False
        try:
            mask = np.asarray(pixel_mask, dtype=bool)
        except Exception:
            return False
        if mask.ndim != 2 or not np.any(mask):
            return False
        orientation_key = str(orientation or "horizontal").strip().lower()
        if orientation_key == "vertical":
            width = int(mask.shape[1])
            for px in range(width):
                if self._automation_cancelled():
                    return False
                runs = self._dense_repair_runs(mask[:, px])
                if not runs:
                    continue
                if px % 2 == 1:
                    runs = list(reversed(runs))
                screen_x = x0 + px
                for start_row, end_row in runs:
                    start_y = y0 + (end_row if px % 2 == 1 else start_row)
                    end_y = y0 + (start_row if px % 2 == 1 else end_row)
                    if not self._dense_repair_draw_run(screen_x, start_y, screen_x, end_y):
                        return False
            return not self._automation_cancelled() and not self.stop_flag

        height = int(mask.shape[0])
        for py in range(height):
            if self._automation_cancelled():
                return False
            runs = self._dense_repair_runs(mask[py])
            if not runs:
                continue
            if py % 2 == 1:
                runs = list(reversed(runs))
            screen_y = y0 + py
            for start_col, end_col in runs:
                start_x = x0 + (end_col if py % 2 == 1 else start_col)
                end_x = x0 + (start_col if py % 2 == 1 else end_col)
                if not self._dense_repair_draw_run(start_x, screen_y, end_x, screen_y):
                    return False
        return not self._automation_cancelled() and not self.stop_flag

    def _dense_repair_orientations(self, pixel_mask: np.ndarray) -> tuple[str, ...]:
        try:
            mask = np.asarray(pixel_mask, dtype=bool)
        except Exception:
            return ("horizontal",)
        if mask.ndim != 2 or not np.any(mask):
            return ("horizontal",)
        rows, cols = np.where(mask)
        if rows.size == 0:
            return ("horizontal",)
        width = int(cols.max() - cols.min() + 1)
        height = int(rows.max() - rows.min() + 1)
        area = int(rows.size)
        bbox_area = max(1, width * height)
        fill_ratio = float(area) / float(bbox_area)
        brush = max(1, int(getattr(self, "brush_size", 1) or 1))
        primary = "horizontal" if width >= height else "vertical"
        max_passes = max(1, int(getattr(self, "_POST_DRAW_REPAIR_DENSE_PASSES", 2) or 1))
        if max_passes <= 1:
            return (primary,)
        thin_span = min(width, height) <= max(2, brush)
        small_region = area <= max(16, brush * brush * 3)
        sparse_shape = fill_ratio <= 0.65
        if not (thin_span or (small_region and sparse_shape)):
            return (primary,)
        secondary = "vertical" if primary == "horizontal" else "horizontal"
        return (primary, secondary)[:max_passes]

    def _dense_repair_fill_cluster_cells(self, cluster_mask: np.ndarray, cluster_id: int) -> bool:
        region = getattr(self, "draw_region", None)
        if region is None or len(region) < 4:
            return False
        try:
            x0, y0, region_w, region_h = (int(region[0]), int(region[1]), int(region[2]), int(region[3]))
        except Exception:
            return False
        if region_w <= 0 or region_h <= 0:
            return False
        expanded_mask = self._expand_post_draw_repair_cluster_mask(cluster_mask, cluster_id)
        if expanded_mask is None or not np.any(expanded_mask):
            return False
        pixel_mask = self._cluster_cell_mask_to_pixel_mask(expanded_mask, region_w, region_h)
        if pixel_mask is None or not np.any(pixel_mask):
            return False
        orientations = self._dense_repair_orientations(pixel_mask)
        for orientation in orientations:
            if not self._dense_repair_fill_pixel_mask(pixel_mask, x0=x0, y0=y0, orientation=orientation):
                return False
        return not self._automation_cancelled() and not self.stop_flag

    def _perform_post_draw_repair_pass(self, hole_mask: np.ndarray, layer_assignments: dict[int, dict[str, object]] | None = None) -> int:
        if self.cluster_map is None or self.drawn_mask is None:
            return 0
        try:
            repair_targets = np.asarray(hole_mask, dtype=bool)
        except Exception:
            return 0
        if repair_targets.shape != self.cluster_map.shape or not np.any(repair_targets):
            return 0
        if layer_assignments is None:
            layer_assignments = self._color_layer_assignments()
        repaired_cells = 0
        for color_data in self.color_palette or []:
            if self._automation_cancelled():
                break
            while not self.drawing_enabled and not self.stop_flag:
                if not self._sleep_with_abort(0.1, step=0.05):
                    break
            if self._automation_cancelled():
                break
            try:
                cluster_id = int(color_data[1])
            except Exception:
                continue
            if cluster_id < 0:
                continue
            cluster_mask = repair_targets & (self.cluster_map == cluster_id)
            if not np.any(cluster_mask):
                continue
            expanded_mask = self._expand_post_draw_repair_cluster_mask(cluster_mask, cluster_id)
            if isinstance(expanded_mask, np.ndarray) and expanded_mask.shape == cluster_mask.shape and np.any(expanded_mask):
                repair_mask = np.asarray(expanded_mask, dtype=bool)
            else:
                repair_mask = cluster_mask
            if getattr(self, "draw_with_layers_enabled", False) and getattr(self, "target_app_layer_coords", None):
                assignment = (layer_assignments or {}).get(cluster_id)
                if assignment is not None:
                    layer_index = assignment.get("layer_index")
                    try:
                        if layer_index is not None and not self._activate_app_layer(int(layer_index)):
                            break
                    except Exception:
                        break
            rows, cols = np.where(cluster_mask)
            if rows.size == 0:
                continue
            repaired_cells += int(rows.size)
            if not self._perform_post_draw_repair_with_current_algorithm(color_data, repair_mask):
                break
        return repaired_cells
