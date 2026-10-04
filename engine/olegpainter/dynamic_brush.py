"""Mixin extracted from engine/olegpainter/core.py.

Contains every `_dynamic_brush_*` method that used to live directly on
`OlegPainter`. Mixed back in via class OlegPainter(DynamicBrushMixin):
so all `self.<attr>` access continues to refer to the engine instance.
"""
from __future__ import annotations

import logging

import math
from collections import deque
from pathlib import Path  # noqa: F401  used by some method annotations

from .common import *  # noqa: F401,F403  shared numpy/Pillow/etc.
from infrastructure.screen_capture import capture_screen
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


log = logging.getLogger("olegpainter.engine.olegpainter.dynamic_brush")


class DynamicBrushMixin:
    """Calibration, capture, planning and execution of the dynamic brush."""

    def _dynamic_brush_reference_rect(self, region=None):
        source = self.draw_region if region is None else region
        rect = normalize_desktop_rect(source)
        if rect is None:
            return None
        x, y, w, h = rect
        if w <= 0 or h <= 0:
            return None
        return int(x), int(y), int(w), int(h)

    def _dynamic_brush_normalize_point(self, point, *, reference_rect=None):
        rect = self._dynamic_brush_reference_rect(reference_rect)
        if rect is None or not isinstance(point, (tuple, list)) or len(point) < 2:
            return None
        try:
            x = float(point[0])
            y = float(point[1])
        except Exception:
            return None
        dx, dy, w, h = rect
        return {
            "x_ratio": (x - dx) / max(1.0, float(w)),
            "y_ratio": (y - dy) / max(1.0, float(h)),
        }

    def _dynamic_brush_resolve_point(self, point_data, *, reference_rect=None):
        rect = self._dynamic_brush_reference_rect(reference_rect)
        if rect is None or not isinstance(point_data, dict):
            return None
        try:
            x_ratio = float(point_data.get("x_ratio"))
            y_ratio = float(point_data.get("y_ratio"))
        except Exception:
            return None
        dx, dy, w, h = rect
        return (
            int(round(dx + x_ratio * float(w))),
            int(round(dy + y_ratio * float(h))),
        )

    def _dynamic_brush_normalize_points(self, points, *, reference_rect=None):
        """Points-mode control params: [{'x_ratio','y_ratio','value'}] or None."""
        if not isinstance(points, (list, tuple)) or not points:
            return None
        normalized = []
        for item in points:
            if not isinstance(item, dict):
                continue
            point = self._dynamic_brush_normalize_point(
                (item.get("x"), item.get("y")), reference_rect=reference_rect
            )
            if point is None:
                continue
            try:
                point["value"] = float(item["value"])
            except Exception:
                continue
            normalized.append(point)
        return normalized or None

    def _dynamic_brush_resolve_points(self, points_data, *, reference_rect=None):
        if not isinstance(points_data, (list, tuple)):
            return []
        resolved = []
        for item in points_data:
            if not isinstance(item, dict):
                continue
            point = self._dynamic_brush_resolve_point(item, reference_rect=reference_rect)
            if point is None:
                continue
            try:
                resolved.append({"x": int(point[0]), "y": int(point[1]), "value": float(item["value"])})
            except Exception:
                continue
        resolved.sort(key=lambda p: p["value"])
        return resolved

    def _dynamic_brush_normalize_rect(self, rect, *, reference_rect=None):
        ref = self._dynamic_brush_reference_rect(reference_rect)
        src = normalize_desktop_rect(rect)
        if ref is None or src is None:
            return None
        dx, dy, w, h = ref
        rx, ry, rw, rh = src
        return {
            "x_ratio": (float(rx) - dx) / max(1.0, float(w)),
            "y_ratio": (float(ry) - dy) / max(1.0, float(h)),
            "w_ratio": float(rw) / max(1.0, float(w)),
            "h_ratio": float(rh) / max(1.0, float(h)),
        }

    def _dynamic_brush_resolve_rect(self, rect_data, *, reference_rect=None):
        ref = self._dynamic_brush_reference_rect(reference_rect)
        if ref is None or not isinstance(rect_data, dict):
            return None
        try:
            x_ratio = float(rect_data.get("x_ratio"))
            y_ratio = float(rect_data.get("y_ratio"))
            w_ratio = float(rect_data.get("w_ratio"))
            h_ratio = float(rect_data.get("h_ratio"))
        except Exception:
            return None
        dx, dy, w, h = ref
        resolved = (
            int(round(dx + x_ratio * float(w))),
            int(round(dy + y_ratio * float(h))),
            max(1, int(round(w_ratio * float(w)))),
            max(1, int(round(h_ratio * float(h)))),
        )
        return normalize_desktop_rect(resolved)

    def _dynamic_brush_normalize_slider_params(self, params, *, reference_rect=None):
        rect = self._dynamic_brush_reference_rect(reference_rect)
        if rect is None or not isinstance(params, (tuple, list)) or len(params) != 4:
            return None
        dx, dy, w, h = rect
        try:
            orientation = str(params[0] or "vertical").strip().lower()
            if orientation not in ("vertical", "horizontal"):
                orientation = "vertical"
            fixed_coord = float(params[1])
            value_max_coord = float(params[2])
            value_min_coord = float(params[3])
        except Exception:
            return None
        if orientation == "vertical":
            return {
                "orientation": orientation,
                "fixed_ratio": (fixed_coord - dx) / max(1.0, float(w)),
                "value_max_ratio": (value_max_coord - dy) / max(1.0, float(h)),
                "value_min_ratio": (value_min_coord - dy) / max(1.0, float(h)),
            }
        return {
            "orientation": orientation,
            "fixed_ratio": (fixed_coord - dy) / max(1.0, float(h)),
            "value_max_ratio": (value_max_coord - dx) / max(1.0, float(w)),
            "value_min_ratio": (value_min_coord - dx) / max(1.0, float(w)),
        }

    def _dynamic_brush_resolve_slider_params(self, params_data, *, reference_rect=None):
        rect = self._dynamic_brush_reference_rect(reference_rect)
        if rect is None or not isinstance(params_data, dict):
            return None
        dx, dy, w, h = rect
        try:
            orientation = str(params_data.get("orientation") or "vertical").strip().lower()
            if orientation not in ("vertical", "horizontal"):
                orientation = "vertical"
            fixed_ratio = float(params_data.get("fixed_ratio"))
            value_max_ratio = float(params_data.get("value_max_ratio"))
            value_min_ratio = float(params_data.get("value_min_ratio"))
        except Exception:
            return None
        if orientation == "vertical":
            return (
                orientation,
                float(dx + fixed_ratio * float(w)),
                float(dy + value_max_ratio * float(h)),
                float(dy + value_min_ratio * float(h)),
            )
        return (
            orientation,
            float(dy + fixed_ratio * float(h)),
            float(dx + value_max_ratio * float(w)),
            float(dx + value_min_ratio * float(w)),
        )

    def _dynamic_brush_cached_calibration(self):
        profile = getattr(self, "dynamic_brush_profile", None)
        if isinstance(profile, dict):
            cached = profile.get("cached_calibration")
            if isinstance(cached, dict):
                return dict(cached)
        return None

    def _dynamic_brush_profile_issue(self, profile=None) -> str:
        """Readiness uses measurement provenance and evidence, not a saved flag."""
        if profile is None:
            profile = getattr(self, "dynamic_brush_profile", None)
        if not isinstance(profile, dict):
            return ""
        cached = profile.get("cached_calibration")
        if not isinstance(cached, dict):
            return "missing"
        if (profile.get("version") != self._DYNAMIC_BRUSH_PROFILE_VERSION
                or cached.get("version") != self._DYNAMIC_BRUSH_PROFILE_VERSION
                or cached.get("measurement_revision") != self._DYNAMIC_BRUSH_MEASUREMENT_REVISION):
            return "outdated"
        if cached.get("measurement_invalid"):
            return "inconsistent"
        validation = cached.get("validation")
        if not isinstance(validation, dict) or validation.get("passed") is not True:
            return "unverified"
        try:
            coverage = float(validation["inside_coverage"])
            if not math.isfinite(coverage) or not 1 - self._DYNAMIC_BRUSH_V2_VERIFY_TOLERANCE <= coverage <= 1:
                return "unverified"
            if int(validation.get("outside_pixels", 0)) != 0:
                return "unverified"
            samples, required = cached.get("samples"), cached.get("required_values")
            if not isinstance(samples, (list, tuple)) or not isinstance(required, (list, tuple)) or len(required) < 2:
                return "inconsistent"
            if not self._dynamic_brush_samples_consistent(samples, required):
                return "inconsistent"
            radii = [float(s["point_reach_cells"]) * float(s["brush_px"]) for s in samples]
            if max(radii) - min(radii) < 1 or max(radii) < min(radii) * 1.5:
                return "inconsistent"
        except (KeyError, TypeError, ValueError, OverflowError):
            return "inconsistent"
        return ""

    @staticmethod
    def _dynamic_brush_slider_auto_range() -> dict[str, float]:
        return {
            "min_value": 0.0,
            "max_value": 1.0,
            "default_value": 0.2,
            # fine positions: a steep slider (Paint: 1 px at 0, 12 px at 0.02)
            # hides whole size ranges between coarse steps
            "step_value": 0.005,
        }

    @staticmethod
    def _dynamic_brush_range_payload(
        range_data,
        *,
        fallback_min: float,
        fallback_max: float,
        fallback_default: float,
        fallback_step: float,
    ) -> dict[str, float]:
        try:
            min_value = float((range_data or {}).get("min_value", fallback_min))
        except Exception:
            min_value = float(fallback_min)
        try:
            max_value = float((range_data or {}).get("max_value", fallback_max))
        except Exception:
            max_value = float(fallback_max)
        try:
            default_value = float((range_data or {}).get("default_value", fallback_default))
        except Exception:
            default_value = float(fallback_default)
        try:
            step_value = float((range_data or {}).get("step_value", fallback_step))
        except Exception:
            step_value = float(fallback_step)
        min_value = max(0.0, min_value)
        max_value = max(min_value + 1e-9, max_value)
        default_value = min(max_value, max(min_value, default_value))
        step_value = max(0.0001, abs(step_value))
        return {
            "min_value": float(min_value),
            "max_value": float(max_value),
            "default_value": float(default_value),
            "step_value": float(step_value),
        }

    @staticmethod
    def _dynamic_brush_rescale_value(
        value: float,
        *,
        source_min: float,
        source_max: float,
        target_min: float,
        target_max: float,
    ) -> float:
        source_span = max(1e-9, float(source_max) - float(source_min))
        target_span = max(1e-9, float(target_max) - float(target_min))
        normalized = (float(value) - float(source_min)) / source_span
        normalized = max(0.0, min(1.0, normalized))
        return float(target_min) + normalized * target_span

    def _dynamic_brush_convert_calibration_range(
        self,
        calibration,
        *,
        source_range: dict[str, float] | None,
        target_range: dict[str, float] | None,
    ):
        if not isinstance(calibration, dict):
            return calibration
        src = self._dynamic_brush_range_payload(
            source_range,
            fallback_min=float(getattr(self, "dynamic_brush_min_value", 0.05)),
            fallback_max=float(getattr(self, "dynamic_brush_max_value", 1.2)),
            fallback_default=float(getattr(self, "dynamic_brush_default_value", 0.1)),
            fallback_step=float(getattr(self, "dynamic_brush_step_value", 0.01)),
        )
        dst = self._dynamic_brush_range_payload(
            target_range,
            fallback_min=float(getattr(self, "dynamic_brush_min_value", 0.05)),
            fallback_max=float(getattr(self, "dynamic_brush_max_value", 1.2)),
            fallback_default=float(getattr(self, "dynamic_brush_default_value", 0.1)),
            fallback_step=float(getattr(self, "dynamic_brush_step_value", 0.01)),
        )
        source_span = max(1e-9, src["max_value"] - src["min_value"])
        target_span = max(1e-9, dst["max_value"] - dst["min_value"])
        converted = dict(calibration)
        try:
            slope = float(converted.get("slope", 0.0))
            intercept = float(converted.get("intercept", 0.0))
            converted["slope"] = float(slope * (target_span / source_span))
            converted["intercept"] = self._dynamic_brush_rescale_value(
                intercept,
                source_min=src["min_value"],
                source_max=src["max_value"],
                target_min=dst["min_value"],
                target_max=dst["max_value"],
            )
        except Exception:
            log.debug("ignored exception in slope = float(converted.get('slope', 0.0))", exc_info=True)
        raw_points = converted.get("sample_points")
        if isinstance(raw_points, (list, tuple)):
            sample_points = []
            for item in raw_points:
                if not isinstance(item, dict):
                    continue
                try:
                    reach = float(item.get("reach_cells"))
                    value = float(item.get("value"))
                except Exception:
                    continue
                sample_points.append(
                    {
                        "reach_cells": reach,
                        "value": self._dynamic_brush_rescale_value(
                            value,
                            source_min=src["min_value"],
                            source_max=src["max_value"],
                            target_min=dst["min_value"],
                            target_max=dst["max_value"],
                        ),
                    }
                )
            converted["sample_points"] = sample_points
        return converted

    def _dynamic_brush_profile_uses_slider_auto_range(self, profile=None) -> bool:
        profile_obj = profile if isinstance(profile, dict) else getattr(self, "dynamic_brush_profile", None)
        control_mode = str(getattr(self, "dynamic_brush_control_mode", "text") or "text").strip().lower()
        if isinstance(profile_obj, dict):
            control_mode = str(profile_obj.get("control_mode") or control_mode).strip().lower()
            value_scale = str(profile_obj.get("value_scale") or "").strip().lower()
            if control_mode == "slider" and value_scale == "normalized":
                return True
        return control_mode == "slider" and not isinstance(profile_obj, dict)

    @staticmethod
    def _dynamic_brush_sample_count_from_payload(calibration) -> int:
        if not isinstance(calibration, dict):
            return 0
        raw_samples = calibration.get("samples")
        if isinstance(raw_samples, (list, tuple)):
            return len(raw_samples)
        try:
            return max(0, int(raw_samples or calibration.get("sample_count", 0) or 0))
        except Exception:
            return 0

    @staticmethod
    def _dynamic_brush_identity_runtime_correction() -> dict[str, float | None]:
        return {
            "anchor_low_value": None,
            "anchor_high_value": None,
            "reach_scale": 1.0,
            "reach_bias": 0.0,
        }

    def _dynamic_brush_requested_for_current_draw(self) -> bool:
        algo = str(getattr(self, "drawing_algorithm", "") or "").strip().lower()
        return bool(getattr(self, "dynamic_brush_enabled", False) and algo == "dfs_4dir")

    def _dynamic_brush_active_for_current_draw(self) -> bool:
        return bool(self._dynamic_brush_requested_for_current_draw() and getattr(self, "dynamic_brush_session_active", False))

    def _dynamic_brush_settings_snapshot(self) -> dict:
        calibration = getattr(self, "dynamic_brush_calibration", None)
        cached_calibration = self._dynamic_brush_cached_calibration()
        profile_issue = self._dynamic_brush_profile_issue()
        return {
            "enabled": bool(getattr(self, "dynamic_brush_enabled", False)),
            "profile_state": str(getattr(self, "dynamic_brush_profile_state", "needs_learning") or "needs_learning"),
            "profile_issue": profile_issue,
            "profile_message": tr("status_dynamic_brush_profile_" + profile_issue) if profile_issue else "",
            "runtime_state": str(getattr(self, "dynamic_brush_runtime_state", "idle") or "idle"),
            "control_mode": str(getattr(self, "dynamic_brush_control_mode", "text") or "text"),
            "min_value": float(getattr(self, "dynamic_brush_min_value", 0.05)),
            "max_value": float(getattr(self, "dynamic_brush_max_value", 1.2)),
            "default_value": float(getattr(self, "dynamic_brush_default_value", 0.1)),
            "step_value": float(getattr(self, "dynamic_brush_step_value", 0.01)),
            "coord": tuple(self.dynamic_brush_coord) if isinstance(self.dynamic_brush_coord, (tuple, list)) else None,
            "slider_params": tuple(self.dynamic_brush_slider_params) if isinstance(self.dynamic_brush_slider_params, (tuple, list)) else None,
            "points": [dict(p) for p in self.dynamic_brush_points if isinstance(p, dict)] if isinstance(getattr(self, "dynamic_brush_points", None), list) else [],
            "drag_enabled": bool(getattr(self, "dynamic_brush_drag_enabled", False)),
            "verify_at_draw": bool(getattr(self, "dynamic_brush_verify_at_draw", False)),
            "text_auto": bool(getattr(self, "dynamic_brush_text_auto", True)),
            "scratch_zone": tuple(self.dynamic_brush_scratch_zone) if isinstance(self.dynamic_brush_scratch_zone, (tuple, list)) else None,
            "calibration": dict(calibration) if isinstance(calibration, dict) else None,
            "cached_calibration": dict(cached_calibration) if isinstance(cached_calibration, dict) else None,
            "profile": dict(self.dynamic_brush_profile) if isinstance(self.dynamic_brush_profile, dict) else None,
        }

    def _dynamic_brush_calibration_params(self) -> tuple[float | None, float | None]:
        calib = getattr(self, "dynamic_brush_calibration", None)
        normalized = self._normalize_dynamic_brush_calibration_payload(calib)
        if isinstance(normalized, dict):
            return None, None
        if isinstance(calib, dict):
            try:
                slope = float(calib.get("slope", 0.0))
                intercept = float(calib.get("intercept", 0.0))
            except Exception:
                return None, None
            if math.isfinite(slope) and slope > 0 and math.isfinite(intercept):
                return slope, intercept
        return None, None

    def _dynamic_brush_calibration_sample_points(self) -> list[tuple[float, float]] | None:
        calib = getattr(self, "dynamic_brush_calibration", None)
        normalized = self._normalize_dynamic_brush_calibration_payload(calib)
        if isinstance(normalized, dict):
            points = []
            for row in normalized.get("samples", ()):
                if not isinstance(row, dict):
                    continue
                try:
                    reach = float(row.get("point_reach_cells"))
                    value = float(row.get("value"))
                except Exception:
                    continue
                if not math.isfinite(reach) or not math.isfinite(value) or reach <= 0:
                    continue
                points.append((reach, value))
            return points or None
        if not isinstance(calib, dict):
            return None
        raw_points = calib.get("sample_points")
        if not isinstance(raw_points, (list, tuple)):
            return None
        points: list[tuple[float, float]] = []
        for item in raw_points:
            if not isinstance(item, dict):
                continue
            try:
                reach = float(item.get("reach_cells"))
                value = float(item.get("value"))
            except Exception:
                continue
            if not math.isfinite(reach) or not math.isfinite(value) or reach <= 0:
                continue
            points.append((reach, value))
        if not points:
            return None
        points.sort(key=lambda pair: (pair[0], pair[1]))
        deduped: list[tuple[float, float]] = []
        for reach, value in points:
            if deduped and math.isclose(deduped[-1][0], reach, rel_tol=0.0, abs_tol=1e-6):
                deduped[-1] = (reach, max(deduped[-1][1], value))
            else:
                deduped.append((reach, value))
        return deduped or None

    def _dynamic_brush_calibration_samples(self, calibration=None) -> list[dict[str, float | str]] | None:
        source = calibration if calibration is not None else getattr(self, "dynamic_brush_calibration", None)
        normalized = self._normalize_dynamic_brush_calibration_payload(source)
        if not isinstance(normalized, dict):
            return None
        rows = normalized.get("samples")
        if not isinstance(rows, list):
            return None
        return [dict(item) for item in rows if isinstance(item, dict)] or None

    def _dynamic_brush_sample_count(self, calibration=None) -> int:
        source = calibration if calibration is not None else getattr(self, "dynamic_brush_calibration", None)
        normalized = self._normalize_dynamic_brush_calibration_payload(source)
        if isinstance(normalized, dict):
            return self._dynamic_brush_sample_count_from_payload(normalized)
        return self._dynamic_brush_sample_count_from_payload(source)

    def _dynamic_brush_patch_half(self, *, region=None) -> int:
        # The capture window must contain the BIGGEST stamp, whose pixel size is
        # unknown before calibration. The control's value range tells us nothing in
        # points mode (values are labels, not pixels), so size the window from the
        # scratch zone itself: a big fraction of its short side, capped.
        patch = 160
        region_rect = self._dynamic_brush_reference_rect(region)
        if region_rect is not None:
            try:
                _, _, width, height = map(int, region_rect)
                scratch_minor = max(1, min(width, height))
                patch = int(max(80, min(420, scratch_minor * 0.42)))
            except Exception:
                log.debug('ignored exception sizing patch from scratch', exc_info=True)
        if patch % 2:
            patch += 1
        return max(20, patch // 2)

    def _dynamic_brush_v2_clean_spots(self, region_rect, half: int, count: int) -> list[tuple[int, int]]:
        """Find the BLANKEST, well-separated spots inside the scratch zone to stamp
        test dots on. Grabs the scratch once, treats the most common colour as the
        background, and scores each candidate by how little it deviates from it —
        so calibration avoids old stamps left by previous runs (the 'can't collect
        samples after re-learning on a dirty scratch' bug). Falls back to an even
        grid if the grab fails."""
        try:
            dx, dy, w, h = map(int, region_rect)
        except Exception:
            return self._dynamic_brush_calibration_points(count, half, region=region_rect)
        margin = int(half) + 8
        if w - 2 * margin <= 0 or h - 2 * margin <= 0:
            return self._dynamic_brush_calibration_points(count, half, region=region_rect)
        try:
            img = np.asarray(capture_screen(bbox=(dx, dy, dx + w, dy + h)).convert("RGB"), dtype=np.float32)
        except Exception:
            return self._dynamic_brush_calibration_points(count, half, region=region_rect)
        H, W = img.shape[:2]
        # Background = the LIGHTER canvas (test stamps are dark): the per-channel
        # 70th percentile is ~white for a blank or partly-painted scratch and is
        # NOT fooled even when up to ~half the scratch is already covered in stamps.
        bg = np.percentile(img.reshape(-1, 3), 70, axis=0)
        deviation = np.linalg.norm(img - bg[None, None, :], axis=2)  # 0 = blank background
        min_sep = max(1.0, float(half) * 1.4)
        # Include the usable endpoints. Cell centres squeeze the grid inward;
        # on a 752x618 scratch this used to leave fewer than six valid spots.
        gx = max(1, int((W - 2 * margin) / min_sep) + 1)
        gy = max(1, int((H - 2 * margin) / min_sep) + 1)
        candidates: list[tuple[float, int, int]] = []
        for iy in range(gy):
            cy = H / 2 if gy == 1 else margin + iy * (H - 2 * margin) / (gy - 1)
            for ix in range(gx):
                cx = W / 2 if gx == 1 else margin + ix * (W - 2 * margin) / (gx - 1)
                y0 = int(max(0, cy - half)); y1 = int(min(H, cy + half))
                x0 = int(max(0, cx - half)); x1 = int(min(W, cx + half))
                win = deviation[y0:y1, x0:x1]
                score = float(win.mean()) if win.size else 1e9
                candidates.append((score, int(dx + cx), int(dy + cy)))
        candidates.sort(key=lambda c: c[0])  # cleanest first
        chosen: list[tuple[int, int]] = []
        for _score, sx, sy in candidates:
            if all(math.hypot(sx - px, sy - py) >= min_sep for px, py in chosen):
                chosen.append((sx, sy))
                if len(chosen) >= count:
                    break
        return chosen or self._dynamic_brush_calibration_points(count, half, region=region_rect)

    def _dynamic_brush_calibration_points(self, count: int, half: int, *, region=None) -> list[tuple[int, int]]:
        region_rect = self._dynamic_brush_reference_rect(region if region is not None else self.draw_region)
        if region_rect is None:
            return []
        dx, dy, w, h = map(int, region_rect)
        margin = half + 8
        usable_w = w - margin * 2
        usable_h = h - margin * 2
        if usable_w <= 0 or usable_h <= 0:
            return []
        points: list[tuple[int, int]] = []
        if usable_h >= usable_w:
            step = usable_h / max(1, count)
            cx = dx + w // 2
            for i in range(count):
                cy = int(dy + margin + step * (i + 0.5))
                points.append((cx, cy))
        else:
            step = usable_w / max(1, count)
            cy = dy + h // 2
            for i in range(count):
                cx = int(dx + margin + step * (i + 0.5))
                points.append((cx, cy))
        return points

    def _dynamic_brush_can_pick_calibration_color(self) -> bool:
        method = str(getattr(self, "color_picking_method", "") or "").strip().lower()
        if method == "hex_field":
            return isinstance(getattr(self, "hex_input_coord", None), (tuple, list)) and len(self.hex_input_coord) >= 2
        if method == "hsv_palette":
            return bool(getattr(self, "circle_params_calib", None) and getattr(self, "slider_params_calib", None))
        if method == "manual_palette":
            cache = self._get_manual_palette_cache()
            return bool(cache and cache.get("entries"))
        return False

    def _dynamic_brush_probe_color(self, region_rect) -> str:
        """Probe stamps must stand out from the free spot: the near-black probe
        vanished on a dark game floor (Spray Paint), so a dark spot gets a light one."""
        x, y, w, h = region_rect
        patch = self._grab_patch(x + w // 2, y + h // 2, max(4, min(w, h) // 2))
        if patch is None:
            return self._DYNAMIC_BRUSH_VALIDATION_COLOR
        lightness = float(np.asarray(patch.convert("L"), dtype=np.float32).mean())
        return self._DYNAMIC_BRUSH_VALIDATION_COLOR if lightness >= 110 else self._DYNAMIC_BRUSH_LIGHT_PROBE_COLOR

    def _dynamic_brush_apply_calibration_color(
        self,
        slot_index: int | None = None,
        *,
        color_hex: str | None = None,
    ) -> bool:
        if not self._dynamic_brush_can_pick_calibration_color():
            return False
        if color_hex is None:
            palette = self._DYNAMIC_BRUSH_CALIBRATION_COLORS
            if not palette:
                return False
            color_hex = str(palette[int(slot_index or 0) % len(palette)]).strip().upper()
        else:
            color_hex = str(color_hex).strip().upper()
        if not color_hex:
            return False
        method = str(getattr(self, "color_picking_method", "") or "").strip().lower()
        try:
            if method == "hex_field":
                self._pick_color_hex_field(color_hex)
            elif method == "hsv_palette":
                self._pick_color_hsv_palette(color_hex)
            elif method == "manual_palette":
                self._pick_color_manual_palette(color_hex)
            else:
                return False
        except Exception as exc:
            self._log(f"Dynamic brush calibration color switch failed: {exc}", True)
            return False
        return not self.stop_flag

    @staticmethod
    def _dynamic_brush_clamp_point_to_rect(
        x: float,
        y: float,
        rect: tuple[int, int, int, int] | None,
        *,
        inset: float = 0.0,
    ) -> tuple[int, int]:
        if rect is None:
            return int(round(x)), int(round(y))
        rx, ry, rw, rh = rect
        min_x = rx + inset
        max_x = rx + max(0.0, float(rw) - 1.0 - inset)
        min_y = ry + inset
        max_y = ry + max(0.0, float(rh) - 1.0 - inset)
        clamped_x = min(max_x, max(min_x, float(x)))
        clamped_y = min(max_y, max(min_y, float(y)))
        return int(round(clamped_x)), int(round(clamped_y))

    def _dynamic_brush_probe_span_px(
        self,
        patch_half: int,
        *,
        region_rect: tuple[int, int, int, int] | None = None,
    ) -> int:
        brush = max(1, int(getattr(self, "brush_size", 1) or 1))
        span = max(brush * 3, int(round(max(12, patch_half * 0.16))))
        if isinstance(region_rect, (tuple, list)) and len(region_rect) == 4:
            try:
                span = min(span, max(brush * 2, int(min(float(region_rect[2]), float(region_rect[3])) * 0.18)))
            except Exception:
                log.debug('ignored exception in span = min(span, max(brush * 2, int(min(float(region_rect[2]), float(region_r...', exc_info=True)
        return max(brush * 2, span)

    def _dynamic_brush_execute_probe(
        self,
        probe_kind: str,
        *,
        anchor: tuple[int, int],
        patch_half: int,
        region_rect: tuple[int, int, int, int] | None = None,
    ) -> bool:
        kind = str(probe_kind or "point").strip().lower()
        try:
            if kind == "point":
                # A dot exactly as the drawing pen puts one; held a little longer
                # since very short clicks can be swallowed by game UIs.
                self._move_abs(int(anchor[0]), int(anchor[1]))
                time.sleep(0.08)
                self._pen_down()
                time.sleep(0.06)
                self._mouse_up_with_settle()
                time.sleep(0.12)
                return True
            span = self._dynamic_brush_probe_span_px(patch_half, region_rect=region_rect)
            start_x, start_y = anchor
            end_x, end_y = anchor
            if kind == "horizontal":
                start_x, start_y = self._dynamic_brush_clamp_point_to_rect(
                    anchor[0] - span,
                    anchor[1],
                    region_rect,
                    inset=2.0,
                )
                end_x, end_y = self._dynamic_brush_clamp_point_to_rect(
                    anchor[0] + span,
                    anchor[1],
                    region_rect,
                    inset=2.0,
                )
            elif kind == "vertical":
                start_x, start_y = self._dynamic_brush_clamp_point_to_rect(
                    anchor[0],
                    anchor[1] - span,
                    region_rect,
                    inset=2.0,
                )
                end_x, end_y = self._dynamic_brush_clamp_point_to_rect(
                    anchor[0],
                    anchor[1] + span,
                    region_rect,
                    inset=2.0,
                )
            self._move_abs(int(start_x), int(start_y))
            time.sleep(0.05)
            self._pen_down()
            time.sleep(0.02)
            self._move_abs(int(end_x), int(end_y))
            time.sleep(max(0.03, float(getattr(self, "draw_delay", 0.0) or 0.0)))
            self._mouse_up_with_settle()
            time.sleep(0.08)
            return True
        except Exception as exc:
            self._log(f"Dynamic brush probe '{kind}' failed: {exc}", True)
            return False

    def _dynamic_brush_v2_probe(
        self,
        value: float,
        center_x: int,
        center_y: int,
        patch_half: int,
        *,
        region_rect: tuple[int, int, int, int] | None = None,
    ) -> dict[str, float | str] | None:
        """Set the control to `value`, stamp ONE dot at (center_x, center_y) in the
        scratch zone, and measure the stamp from a before/after screenshot diff."""
        if not self._apply_dynamic_brush_value(value, force=True):
            return None
        return self._dynamic_brush_v2_stamp_and_measure(
            int(center_x), int(center_y), int(patch_half), region_rect=region_rect
        )

    def _dynamic_brush_v2_measure_stamp(
        self,
        before_img: Image.Image,
        after_img: Image.Image,
        *,
        anchor: tuple[float, float] | None = None,
    ) -> dict[str, float | str] | None:
        from .brush_measurement import measure_stamp
        return measure_stamp(before_img, after_img, anchor=anchor)

    def _dynamic_brush_v2_make_sample(self, value: float, probe: dict) -> dict[str, float | str]:
        """Calibration-payload row from one v2 probe. The radius keys keep the v1
        'cells' schema (radius / brush at measurement time); `brush_px` records that
        brush so a later brush_size change cannot silently rescale the radii."""
        brush = max(1.0, float(self.brush_size))
        radius_cells = max(1e-4, float(probe["radius_px"]) / brush)
        self._log(f"Dynamic brush probe: value {float(value):g} -> radius {float(probe['radius_px']):.1f}px")
        return {
            "value": float(value),
            "point_reach_cells": radius_cells,
            "stroke_reach_h_cells": radius_cells,
            "stroke_reach_v_cells": radius_cells,
            "half_width_cells": radius_cells,
            "half_height_cells": radius_cells,
            "area_cells": max(1e-4, float(probe.get("area_px", 0.0) or 0.0) / (brush * brush)),
            "shape_hint": str(probe.get("shape", "unknown") or "unknown").strip().lower(),
            "confidence": float(probe.get("confidence", 0.0) or 0.0),
            "density": float(probe.get("density", 1.0) or 1.0),
            "brush_px": brush,
            "offset_x_px": float(probe.get("offset_x", 0.0) or 0.0),
            "offset_y_px": float(probe.get("offset_y", 0.0) or 0.0),
        }

    def _dynamic_brush_v2_verify(
        self,
        samples: list[dict[str, float | str]],
        point_iter,
        patch_half: int,
        *,
        region_rect: tuple[int, int, int, int] | None = None,
        rounds: int = 2,
    ) -> dict[str, float | bool]:
        """Closed-loop check of the fitted value->radius curve: predict the control
        value for a mid-range target radius, stamp it, compare measured vs target.
        A failed round ADDS the measured point to the curve (refining it) and
        retries — each round roughly halves the prediction error."""
        brush = max(1.0, float(self.brush_size))
        result: dict[str, float | bool] = {"passed": False, "relative_error": 1.0}
        for _ in range(max(1, int(rounds))):
            if self._automation_cancelled():
                break
            rows = sorted(samples, key=lambda r: float(r.get("point_reach_cells", 0.0) or 0.0))
            radii = [float(r.get("point_reach_cells", 0.0) or 0.0) * brush for r in rows]
            values = [float(r.get("value", 0.0) or 0.0) for r in rows]
            r_lo, r_hi = radii[0], radii[-1]
            if r_hi <= r_lo * 1.05:
                # Degenerate curve: the control barely changes the stamp. Nothing to
                # verify — and the tier ladder will refuse to run on such a range.
                break
            r_target = math.sqrt(r_lo * r_hi)
            v_pred = self._quantize_dynamic_brush_value(float(np.interp(r_target, radii, values)))
            # Quantisation can choose an existing preset. Verify it with a NEW
            # stamp anyway; fitting a sample does not prove repeatable control.
            by_value = sorted(zip(values, radii))
            r_target = float(np.interp(v_pred, [v for v, _ in by_value], [r for _, r in by_value]))
            try:
                px, py = next(point_iter)
            except StopIteration:
                break
            probe = self._dynamic_brush_v2_probe(v_pred, int(px), int(py), patch_half, region_rect=region_rect)
            if not isinstance(probe, dict):
                break
            samples.append(self._dynamic_brush_v2_make_sample(v_pred, probe))
            r_measured = float(probe["radius_px"])
            relative_error = abs(r_measured - r_target) / max(1.0, r_target)
            result = {
                "passed": relative_error <= self._DYNAMIC_BRUSH_V2_VERIFY_TOLERANCE,
                "relative_error": relative_error,
            }
            if result["passed"]:
                break
        return result

    def _dynamic_brush_shape_summary_from_samples(self, samples: list[dict[str, float | str]]) -> tuple[str, float]:
        shape_scores: dict[str, float] = {}
        for sample in samples or ():
            if not isinstance(sample, dict):
                continue
            shape_key = str(sample.get("shape_hint", "unknown") or "unknown").strip().lower()
            try:
                score = float(sample.get("confidence", 0.0) or 0.0)
            except Exception:
                score = 0.0
            if shape_key not in {"circle", "square"}:
                shape_key = "unknown"
            shape_scores[shape_key] = shape_scores.get(shape_key, 0.0) + max(0.0, score)
        if not shape_scores:
            return "unknown", 0.0
        shape = max(shape_scores, key=shape_scores.get)
        total_score = sum(shape_scores.values()) or 1.0
        return shape, max(0.0, min(1.0, float(shape_scores.get(shape, 0.0) / total_score)))

    def _dynamic_brush_control_ready(self) -> bool:
        mode = str(getattr(self, "dynamic_brush_control_mode", "text") or "text").strip().lower()
        if mode == "slider":
            return isinstance(getattr(self, "dynamic_brush_slider_params", None), (tuple, list)) and len(self.dynamic_brush_slider_params) == 4
        if mode == "points":
            points = getattr(self, "dynamic_brush_points", None)
            return isinstance(points, list) and len([p for p in points if isinstance(p, dict)]) >= 2
        return isinstance(getattr(self, "dynamic_brush_coord", None), (tuple, list)) and len(self.dynamic_brush_coord) >= 2

    def _dynamic_brush_precision(self) -> int:
        try:
            step = float(self.dynamic_brush_step_value)
        except Exception:
            step = 0.01
        return self._dynamic_brush_precision_from_step(step)

    @staticmethod
    def _dynamic_brush_precision_from_step(step: float) -> int:
        if not math.isfinite(step) or step <= 0:
            step = 0.01
        step_str = f"{step:.8f}".rstrip("0").rstrip(".")
        if "." not in step_str:
            return 0
        return min(4, len(step_str.split(".", 1)[1]))

    def _dynamic_brush_shape_kind(self) -> str:
        calib = getattr(self, "dynamic_brush_calibration", None)
        if isinstance(calib, dict):
            shape = str(calib.get("shape", "unknown") or "unknown").strip().lower()
            if shape in ("circle", "square"):
                return shape
        return "unknown"

    # ------------------------------------------------------------------
    # Dynamic brush v2 — the "rest machining" tier ladder.
    #
    # Changing the target app's brush size is the EXPENSIVE operation (a slider
    # or text-field interaction), so instead of modulating the size along the
    # route (the removed v1 planner did it per cell), the color is painted in a
    # few DISCRETE size tiers, largest first: each tier paints only where its
    # stamp provably fits (distance-transform erosion = the CAM "rest machining"
    # model), the static base brush then finishes the edge band. One size change
    # per tier per color.
    # ------------------------------------------------------------------

    def _dynamic_brush_v2_scratch_probe_spot(self):
        """Next free spot in the scratch zone for a draw-time test stamp (rotating
        so repeated stamps don't land on each other). Returns ((x, y), patch_half,
        scratch_rect) or (None, 0, None).

        STRICTLY the scratch zone: _dynamic_brush_reference_rect would fall back
        to the draw region when no scratch is set, and a test stamp inside the
        artwork is exactly what must never happen."""
        scratch = getattr(self, "dynamic_brush_scratch_zone", None)
        if not (isinstance(scratch, (tuple, list)) and len(scratch) == 4):
            return None, 0, None
        rect = self._dynamic_brush_reference_rect(scratch)
        if rect is None:
            return None, 0, None
        patch_half = self._dynamic_brush_patch_half(region=rect)
        spots = self._dynamic_brush_calibration_points(8, patch_half, region=rect)
        if not spots:
            return None, 0, None
        index = int(getattr(self, "_dyn2_scratch_probe_index", 0) or 0)
        self._dyn2_scratch_probe_index = index + 1
        return spots[index % len(spots)], patch_half, rect

    def _dynamic_brush_v2_stamp_and_measure(
        self,
        center_x: int,
        center_y: int,
        patch_half: int,
        *,
        region_rect=None,
    ) -> dict[str, float | str] | None:
        """Stamp ONE dot at the CURRENT control value and measure it (no apply)."""
        rect = self._dynamic_brush_reference_rect(region_rect)
        if rect is None or self._automation_cancelled():
            return None
        # Capture both images with the pointer outside the measured patch.
        # Cursor highlights otherwise become part of small-brush measurements.
        corners = [(x, y) for x in (rect[0] + 1, rect[0] + rect[2] - 2)
                   for y in (rect[1] + 1, rect[1] + rect[3] - 2)]
        park_x, park_y = max(corners, key=lambda p: math.hypot(p[0] - center_x, p[1] - center_y))
        self._click_abs(park_x, park_y)
        if self._drawing_cancel.wait(.08) or self._automation_cancelled():
            return None
        before = self._grab_patch(int(center_x), int(center_y), int(patch_half))
        if before is None:
            return None
        if not self._dynamic_brush_execute_probe(
            "point",
            anchor=(int(center_x), int(center_y)),
            patch_half=int(patch_half),
            region_rect=region_rect,
        ):
            return None
        if self._automation_cancelled():
            return None
        self._click_abs(park_x, park_y)
        if self._drawing_cancel.wait(.08) or self._automation_cancelled():
            return None
        after = self._grab_patch(int(center_x), int(center_y), int(patch_half))
        if after is None:
            return None
        return self._dynamic_brush_v2_measure_stamp(before, after, anchor=(float(patch_half), float(patch_half)))

    def _dynamic_brush_v2_apply_and_measure(self, value: float) -> tuple[bool, float | None]:
        """THE draw-time closed loop: set the control to `value`, then stamp once in
        the scratch zone and measure what the brush ACTUALLY paints right now. The
        tier planner trusts only this measurement — never the calibration curve
        alone — so an off-curve slider (non-linear track, app-side snapping, stale
        calibration) can no longer make a stroke wider than planned.
        Returns (applied, measured_radius_px): measured is None when the stamp is
        unmeasurable (no scratch zone / color invisible on the scratch background)."""
        if not self._apply_dynamic_brush_value(value, force=True):
            return False, None
        spot, patch_half, rect = self._dynamic_brush_v2_scratch_probe_spot()
        if spot is None:
            return True, None
        probe = self._dynamic_brush_v2_stamp_and_measure(int(spot[0]), int(spot[1]), patch_half, region_rect=rect)
        if not isinstance(probe, dict):
            return True, None
        return True, float(probe["radius_px"])

    def _dynamic_brush_samples_consistent(self, samples, required_values) -> bool:
        """Validate raw observations before normalization can hide a bad probe.

        Every scheduled size needs a measurement. Increasing a size control
        must not materially shrink its stamp; repeated sizes must agree.
        Compare all pairs so successive small errors cannot accumulate.
        """
        rows = []
        try:
            for sample in samples:
                value = float(sample["value"])
                brush = float(sample.get("brush_px", self.brush_size))
                radius = float(sample["point_reach_cells"]) * brush
                if not all(math.isfinite(v) for v in (value, brush, radius)) or brush <= 0 or radius <= 0:
                    return False
                rows.append((value, radius))
        except (KeyError, TypeError, ValueError, OverflowError):
            return False
        if len(rows) < 2 or any(
            not any(math.isclose(float(required), v, rel_tol=0.0, abs_tol=1e-6) for v, _ in rows)
            for required in required_values
        ):
            return False
        rows.sort()
        for index, (value, radius) in enumerate(rows):
            for next_value, next_radius in rows[index + 1:]:
                tolerance = max(.75, min(radius, next_radius) * self._DYNAMIC_BRUSH_V2_VERIFY_TOLERANCE)
                if radius - next_radius > tolerance:
                    return False
                if math.isclose(value, next_value, rel_tol=0.0, abs_tol=1e-6) and abs(radius - next_radius) > tolerance:
                    return False
        return True

    def _dynamic_brush_calibration_degenerate(self, samples) -> tuple[bool, str]:
        """Detect a calibration where the control never actually changed the brush
        (every probe measured the same radius) — the #1 dynamic-brush failure: the
        clicks/slider/points don't register in the target app, so the only size
        known is the smallest and the fill silently uses a tiny brush.

        Degenerate iff the biggest measured radius isn't meaningfully larger than
        the smallest (needs >= ~1.5x spread AND >= 1px absolute). Returns
        (is_degenerate, human message)."""
        rows = [r for r in (samples or ()) if isinstance(r, dict)]
        if len(rows) < 2:
            return False, ""
        radii_px = sorted(
            max(0.0, float(r.get("point_reach_cells", 0.0) or 0.0)) * max(1, int(self.brush_size))
            for r in rows
        )
        r_lo, r_hi = radii_px[0], radii_px[-1]
        spread_ok = (r_hi - r_lo) >= 1.0 and r_hi >= r_lo * 1.5
        if spread_ok:
            return False, ""
        mode = str(getattr(self, "dynamic_brush_control_mode", "text") or "text").strip().lower()
        try:
            msg = tr("status_dynamic_brush_calibration_no_change", mode=mode, lo=round(r_lo, 1), hi=round(r_hi, 1))
        except Exception:
            msg = (f"Dynamic brush calibration failed: the brush size never changed "
                   f"(every point measured ~{round(r_lo, 1)}px). The '{mode}' control isn't "
                   f"affecting the app — re-capture the points/slider or check how size is set there.")
        return True, msg

    def _dynamic_brush_v2_radius_table(self):
        """Calibrated (radii_px ascending, control values) arrays, or None."""
        samples = self._dynamic_brush_calibration_samples()
        if not samples:
            return None
        current_brush = max(1.0, float(self.brush_size))
        pairs: list[tuple[float, float]] = []
        for row in samples:
            try:
                value = float(row.get("value"))
                row_brush = float(row.get("brush_px", 0.0) or 0.0)
                scale = row_brush if row_brush > 0 else current_brush
                radius_px = float(row.get("point_reach_cells", 0.0) or 0.0) * scale
            except Exception:
                continue
            if math.isfinite(value) and math.isfinite(radius_px) and radius_px > 0:
                pairs.append((radius_px, value))
        if len(pairs) < 2:
            return None
        pairs.sort(key=lambda pair: (pair[0], pair[1]))
        # A plateau (several values, one stamp) means the control stopped
        # responding there: past the end of the track or a missed click. Keep
        # only the most interior value of each plateau, never an end one.
        lo_v = min(p[1] for p in pairs)
        hi_v = max(p[1] for p in pairs)
        middle = (lo_v + hi_v) / 2.0
        kept: list[tuple[float, float]] = []
        for radius, value in pairs:
            if kept and abs(radius - kept[-1][0]) <= max(0.3, kept[-1][0] * 0.03):
                if abs(value - middle) < abs(kept[-1][1] - middle):
                    kept[-1] = (radius, value)
                continue
            kept.append((radius, value))
        if len(kept) < 2:
            return None
        pairs = kept
        radii = np.asarray([pair[0] for pair in pairs], dtype=np.float64)
        values = np.asarray([pair[1] for pair in pairs], dtype=np.float64)
        return radii, values

    def _dynamic_brush_v2_value_for_radius_px(self, radius_px: float) -> float | None:
        table = self._dynamic_brush_v2_radius_table()
        if table is None:
            return None
        radii, values = table
        if str(getattr(self, "dynamic_brush_control_mode", "text") or "text").strip().lower() == "points":
            # Discrete presets: take the LARGEST measured radius that still fits
            # (floor), never a nearest-neighbour snap to a bigger stamp.
            fitting = [i for i in range(len(radii)) if float(radii[i]) <= float(radius_px) + 1e-9]
            index = fitting[-1] if fitting else 0
            return float(values[index])
        clipped = float(np.clip(float(radius_px), radii[0], radii[-1]))
        return self._quantize_dynamic_brush_value(float(np.interp(clipped, radii, values)))

    def _dynamic_brush_v2_radius_px_for_value(self, value: float) -> float | None:
        table = self._dynamic_brush_v2_radius_table()
        if table is None:
            return None
        radii, values = table
        order = np.argsort(values, kind="stable")
        sorted_values = values[order]
        sorted_radii = radii[order]
        clipped = float(np.clip(float(value), sorted_values[0], sorted_values[-1]))
        return float(np.interp(clipped, sorted_values, sorted_radii))

    def _dynamic_brush_v2_base_value(self) -> float | None:
        """Control value that reproduces the STATIC base brush (a stamp roughly
        brush_size px wide). None when the calibrated range cannot go that small —
        running tiers would then leave a fat brush for the edge pass and the base
        fill would spill outside its cells."""
        table = self._dynamic_brush_v2_radius_table()
        if table is None:
            return None
        radii, _values = table
        base_radius_px = max(0.5, float(self.brush_size) / 2.0)
        if float(radii[0]) > base_radius_px * 1.5:
            return None
        return self._dynamic_brush_v2_value_for_radius_px(base_radius_px)

    def _dynamic_brush_v2_ensure_base_value(self) -> float | None:
        """Cached base (edge-band) value for this draw session.

        Trusts the calibration: the smallest calibrated size that reproduces the
        base brush (~brush_size). Only declares the base unreachable when the
        calibrated table itself can't go small enough (radii[0] too big), so a fat
        'base' brush never paints the edge band. With the opt-in draw-time
        verification it additionally stamps once and corrects down the curve."""
        cached = getattr(self, "_dyn2_base_value", None)
        if cached is False:
            return None
        if cached is not None:
            return float(cached)
        value = self._dynamic_brush_v2_base_value()
        if value is None:
            self._dyn2_base_value = False
            return None
        if not bool(getattr(self, "dynamic_brush_verify_at_draw", False)):
            # TRUST: the base value comes straight from the calibrated table.
            self._dyn2_base_value = float(value)
            return float(value)
        brush = max(1, int(self.brush_size))
        target_radius = max(0.5, brush / 2.0)
        radius_limit = max(target_radius, brush * 0.95)  # stamp diameter < ~1.9 cells
        applied, measured = self._dynamic_brush_v2_apply_and_measure(value)
        if not applied:
            return None  # hard failure (stop_flag set); do not cache
        corrections = 0
        while measured is not None and measured > radius_limit and corrections < 2:
            corrections += 1
            aim = max(0.25, target_radius * (target_radius / float(measured)))
            corrected = self._dynamic_brush_v2_value_for_radius_px(aim)
            if corrected is None or math.isclose(corrected, value, rel_tol=0.0, abs_tol=1e-9):
                break
            value = corrected
            applied, measured = self._dynamic_brush_v2_apply_and_measure(value)
            if not applied:
                return None
        if measured is not None and measured > radius_limit:
            self._dyn2_base_value = False
            # Not an error: the detail pass falls back to the smallest calibrated size.
            self._log(tr("status_dynamic_brush_base_unreachable", px=round(float(measured) * 2.0, 1), cell=brush))
            return None
        self._dyn2_base_value = float(value)
        return float(value)

    def _dynamic_brush_v2_clearance_cells(self, mask_bool: np.ndarray) -> np.ndarray:
        """Distance (in cells) from each in-mask cell to the nearest outside cell.
        The mask is zero-padded first so the draw-region border counts as outside —
        a fat stamp must never poke past the region even when the color runs to its
        edge. The metric follows the calibrated stamp shape (Chebyshev for square)."""
        padded = np.pad(np.asarray(mask_bool, dtype=bool), 1).astype(np.uint8)
        if self._dynamic_brush_shape_kind() == "square":
            dist_type, mask_size = getattr(cv2, "DIST_C", cv2.DIST_L1), 3
        else:
            dist_type, mask_size = cv2.DIST_L2, 5
        try:
            dist = cv2.distanceTransform(padded, dist_type, mask_size)
        except Exception:
            dist = cv2.distanceTransform(padded, cv2.DIST_L2, 5)
        return dist[1:-1, 1:-1]

    def _dynamic_brush_v2_coarse_valid(self, clearance_cells: np.ndarray, r_req_cells: float, s_cells: int, *, anchor=None):
        """Step-over grid of permissible stamp CENTRES, ANCHORED so the deepest
        cell (argmax clearance by default) lies exactly on a grid node — with a
        free-floating grid a small deep core can fall BETWEEN nodes and the big
        brush would never be used there (the 'always picks the small brush' bug).
        valid[R, C] refers to base cell (rows[R], cols[C]); the erosion guarantee
        (clearance >= radius + margin) makes spill impossible from these centres.
        Returns (valid_bool, rows, cols)."""
        h, w = clearance_cells.shape
        s = max(1, int(s_cells))
        if anchor is None:
            flat = int(np.argmax(clearance_cells))
            anchor = (flat // w, flat % w)
        rows = np.arange(int(anchor[0]) % s, h, s)
        cols = np.arange(int(anchor[1]) % s, w, s)
        valid = clearance_cells[np.ix_(rows, cols)] >= float(r_req_cells)
        return valid, rows, cols

    def _dynamic_brush_v2_plan_grid(self, clearance_cells: np.ndarray, radius_px: float, requirement_scale: float = 1.0):
        """Plan one tier for a given REAL radius. Stroke grid first (margin
        r + s/2 + 1 covers the swept path between nodes); when not even one
        stroke node fits, fall back to ISOLATED STAMPS (margin r + 1 only — the
        pen travels lifted between nodes), so a big brush still paints a deep
        core where only a few stamps fit instead of degrading to a small brush.
        Returns {valid, rows, cols, s_cells, stamps_only, covered_cells} or None."""
        brush = max(1, int(self.brush_size))
        r_cells = float(radius_px) / brush
        s_cells = max(2, int(round(float(self._DYNAMIC_BRUSH_V2_STEP_OVER) * r_cells)))
        stroke_req = (r_cells + s_cells / 2.0 + 1.0) * float(requirement_scale)
        valid, rows, cols = self._dynamic_brush_v2_coarse_valid(clearance_cells, stroke_req, s_cells)
        stamps_only = False
        if not np.any(valid):
            stamp_req = (r_cells + 1.0) * float(requirement_scale)
            valid, rows, cols = self._dynamic_brush_v2_coarse_valid(clearance_cells, stamp_req, s_cells)
            stamps_only = True
        n_valid = int(np.count_nonzero(valid))
        if n_valid <= 0:
            return None
        per_node = math.pi * r_cells * r_cells if stamps_only else float(s_cells * s_cells)
        covered_cells = n_valid * per_node
        if covered_cells < self._DYNAMIC_BRUSH_V2_TIER_MIN_GAIN_CELLS:
            return None
        return {
            "valid": valid,
            "rows": rows,
            "cols": cols,
            "s_cells": s_cells,
            "stamps_only": stamps_only,
            "covered_cells": covered_cells,
        }

    def _dynamic_brush_v2_tier_candidates(self, r_cap_px: float, tier_min_px: float) -> list[float]:
        """Candidate tier radii, biggest first. Points mode: the measured preset
        radii themselves (sizes between presets don't exist); otherwise a x0.7
        ladder from the cap — finer than halving, so a big brush that ALMOST
        fits is not skipped straight down to a much smaller one."""
        if str(getattr(self, "dynamic_brush_control_mode", "text") or "text").strip().lower() == "points":
            table = self._dynamic_brush_v2_radius_table()
            if table is None:
                return []
            preset_radii = sorted({float(r) for r in table[0]}, reverse=True)
            return [r for r in preset_radii if tier_min_px <= r <= r_cap_px + 1e-6]
        candidates: list[float] = []
        radius = float(r_cap_px)
        while radius >= tier_min_px and len(candidates) < 9:
            candidates.append(radius)
            radius *= 0.7
        return candidates

    def _dynamic_brush_v2_color_has_tier_potential(self, undrawn_mask_u8) -> bool:
        """True when at least one tier could geometrically fit in this color —
        checked from the mask alone, BEFORE the size control is ever touched.
        Uses the STAMP-mode floor (clearance >= r + 1): a single big stamp in
        the deep core is already a valid tier."""
        try:
            mask_bool = np.asarray(undrawn_mask_u8) > 0
        except Exception:
            return False
        if not np.any(mask_bool):
            return False
        table = self._dynamic_brush_v2_radius_table()
        if table is None:
            return False
        radii, _values = table
        brush = max(1, int(self.brush_size))
        tier_min_px = max(self._DYNAMIC_BRUSH_V2_MIN_TIER_BRUSH_FACTOR * brush, float(radii[0]))
        clearance = self._dynamic_brush_v2_clearance_cells(mask_bool)
        r_cap = min(float(radii[-1]), (float(clearance.max()) - 2.0) * brush)
        return math.isfinite(r_cap) and r_cap >= tier_min_px

    def _dynamic_brush_v2_run_tiers(self, x0: int, y0: int, cluster_id: int) -> bool:
        """Paint the color's deep interior in up to _DYNAMIC_BRUSH_V2_MAX_TIERS
        coarse passes, BIGGEST usable brush first, ONE size change per tier.

        TRUSTS the calibration radius table (calibration already measured every
        size with computer vision): the tier is planned straight from the
        calibrated radius and the size is just APPLIED, then painted — no
        background test-stamping during the real drawing. A small extra erosion
        margin (req_scale) absorbs any residual calibration error, so the no-spill
        guarantee holds without re-measuring. Draw-time re-measurement is opt-in
        via `dynamic_brush_verify_at_draw` (off by default) for the rare case of a
        drifting/non-deterministic target."""
        if self.cluster_map is None or self.drawn_mask is None:
            return False
        table = self._dynamic_brush_v2_radius_table()
        if table is None:
            return False
        radii, _values = table
        brush = max(1, int(self.brush_size))
        r_min_cal = float(radii[0])
        r_max_cal = float(radii[-1])
        tier_min_px = max(self._DYNAMIC_BRUSH_V2_MIN_TIER_BRUSH_FACTOR * brush, r_min_cal)
        verify = bool(getattr(self, "dynamic_brush_verify_at_draw", False))
        # trust-the-calibration safety margin on the erosion requirement
        trust_scale = 1.0 + (r_max_cal - r_min_cal) / max(1.0, r_max_cal) * 0.15
        ran_any = False
        tiers_done = 0
        measures_left = 4
        pending: list[float] = []
        for _attempt in range(12):
            if tiers_done >= self._DYNAMIC_BRUSH_V2_MAX_TIERS:
                break
            if verify and measures_left <= 0:
                break
            if self._automation_cancelled() or self.stop_flag:
                break
            mask_bool = (self.cluster_map == cluster_id) & (~self.drawn_mask)
            if not np.any(mask_bool):
                break
            clearance = self._dynamic_brush_v2_clearance_cells(mask_bool)
            # stamp-mode floor: one big stamp only needs clearance >= r + 1 (+1 slack)
            r_cap = min(r_max_cal, (float(clearance.max()) - 2.0) * brush)
            if not math.isfinite(r_cap) or r_cap < tier_min_px:
                break
            if pending:
                pending = [r for r in pending if r <= r_cap + 1e-6]
            else:
                pending = self._dynamic_brush_v2_tier_candidates(r_cap, tier_min_px)
            if not pending:
                break
            radius_px = pending.pop(0)
            value = self._dynamic_brush_v2_value_for_radius_px(radius_px)
            if value is None:
                break
            calibrated_radius = self._dynamic_brush_v2_radius_px_for_value(value) or radius_px
            calibrated_radius = min(radius_px, calibrated_radius)
            if calibrated_radius < tier_min_px:
                continue
            # Does this size geometrically fit (plan exists and pays for the change)?
            if self._dynamic_brush_v2_plan_grid(clearance, calibrated_radius, trust_scale) is None:
                continue

            actual_radius = calibrated_radius
            requirement_scale = trust_scale
            if verify:
                # OPT-IN: stamp once in the scratch zone and plan from the measured
                # radius (for a drifting / non-deterministic target).
                applied, measured = self._dynamic_brush_v2_apply_and_measure(value)
                if not applied:
                    break
                measures_left -= 1
                if measured is not None and math.isfinite(measured) and measured > 0:
                    actual_radius = float(measured)
                    requirement_scale = 1.0
                else:
                    requirement_scale = 1.5
                if actual_radius < tier_min_px:
                    break
            else:
                # TRUST: just set the size (drag/click the control) and paint.
                if not self._apply_dynamic_brush_value(value, force=True):
                    break

            plan = self._dynamic_brush_v2_plan_grid(clearance, actual_radius, requirement_scale)
            if plan is None:
                continue
            ran_any = True
            tiers_done += 1
            if not self._dynamic_brush_v2_paint_tier(plan, actual_radius / brush, mask_bool, x0, y0):
                break
            pending = []  # the remaining interior changed; rebuild candidates
        return ran_any

    def _dynamic_brush_v2_paint_tier(
        self,
        plan: dict,
        r_cells: float,
        color_mask_bool: np.ndarray,
        x0: int,
        y0: int,
    ) -> bool:
        """One tier: order the coarse components (honours area_sequence), snake
        through each, draw the strokes/stamps and claim them in drawn_mask."""
        valid_coarse = plan["valid"]
        rows = plan["rows"]
        cols = plan["cols"]
        s_cells = int(plan["s_cells"])
        stamps_only = bool(plan.get("stamps_only"))
        coarse_u8 = np.asarray(valid_coarse, dtype=np.uint8) * 255
        try:
            num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(coarse_u8, connectivity=4)
        except Exception:
            return False
        if num_labels <= 1:
            return True
        anchor = getattr(self, "_last_pen_cell", None)
        if isinstance(anchor, (tuple, list)) and len(anchor) == 2:
            try:
                anchor = (
                    (float(anchor[0]) - float(cols[0])) / max(1, s_cells),
                    (float(anchor[1]) - float(rows[0])) / max(1, s_cells),
                )
            except Exception:
                anchor = None
        for label_idx in self._ordered_component_labels(num_labels, stats, centroids, anchor):
            if self._automation_cancelled() or self.stop_flag:
                return False
            component = labels == label_idx
            coords = np.argwhere(component)
            if coords.size == 0:
                continue
            order = self._dfs_greedy_snake_order(
                component.astype(np.uint8) * 255, int(coords[0][0]), int(coords[0][1])
            )
            if not order:
                continue
            if not self._dynamic_brush_v2_render_coarse_order(
                order, rows, cols, r_cells, color_mask_bool, x0, y0, stamps_only=stamps_only
            ):
                return False
            if centroids is not None:
                try:
                    self._last_pen_cell = (
                        float(cols[0]) + float(centroids[label_idx][0]) * max(1, s_cells),
                        float(rows[0]) + float(centroids[label_idx][1]) * max(1, s_cells),
                    )
                except Exception:
                    log.debug("ignored exception updating _last_pen_cell from coarse centroid", exc_info=True)
        return not self._automation_cancelled() and not self.stop_flag

    def _dynamic_brush_v2_render_coarse_order(
        self,
        order: list[tuple[int, int]],
        rows: np.ndarray,
        cols: np.ndarray,
        r_cells: float,
        color_mask_bool: np.ndarray,
        x0: int,
        y0: int,
        *,
        stamps_only: bool = False,
    ) -> bool:
        """Drive the pen along a coarse snake order. Adjacent coarse cells become a
        continuous stroke; gaps are pen-up teleports (a fat stamp dragged across the
        component would overdraw heavily). In stamps_only mode EVERY node is an
        isolated stamp (its clearance margin doesn't cover swept paths, so the pen
        always travels lifted). After each really-drawn segment its swept capsule
        (shrunk by _DYNAMIC_BRUSH_V2_MARK_RATIO so we never claim more than the
        paint guarantees) is marked in drawn_mask."""
        if not order:
            return True
        if self.drawn_mask is None:
            return False
        brush = max(1, int(self.brush_size))
        h, w = color_mask_bool.shape
        region_w, region_h = self._region_dimensions(fallback_cols=w, fallback_rows=h, brush=brush)
        r_mark = max(1, int(round(float(r_cells) * self._DYNAMIC_BRUSH_V2_MARK_RATIO)))
        thickness = 2 * r_mark + 1
        mark_buffer = np.zeros((h, w), dtype=np.uint8)

        def base_cell(coarse_rc: tuple[int, int]) -> tuple[int, int]:
            return (
                int(rows[min(int(coarse_rc[0]), len(rows) - 1)]),
                int(cols[min(int(coarse_rc[1]), len(cols) - 1)]),
            )

        def center_px(base_rc: tuple[int, int]) -> tuple[int, int]:
            return (
                self._cell_center_axis(x0, region_w, int(base_rc[1]), brush),
                self._cell_center_axis(y0, region_h, int(base_rc[0]), brush),
            )

        def mark_segment(base_a: tuple[int, int], base_b: tuple[int, int]) -> None:
            mark_buffer[:] = 0
            cv2.line(
                mark_buffer,
                (int(base_a[1]), int(base_a[0])),
                (int(base_b[1]), int(base_b[0])),
                255,
                thickness=thickness,
            )
            cv2.circle(mark_buffer, (int(base_a[1]), int(base_a[0])), r_mark, 255, -1)
            cv2.circle(mark_buffer, (int(base_b[1]), int(base_b[0])), r_mark, 255, -1)
            self._mark_dynamic_brush_coverage((mark_buffer > 0) & color_mask_bool)

        prev = order[0]
        base_prev = base_cell(prev)
        last_x, last_y = center_px(base_prev)
        mouse_is_down = False
        try:
            self._move_abs(last_x, last_y); time.sleep(self.pen_settle_delay)
            self._pen_down(); time.sleep(self.pen_settle_delay); mouse_is_down = True
        except Exception:
            self.stop_flag = True
            return False
        mark_segment(base_prev, base_prev)
        resumed_just_now = False
        for cur in order[1:]:
            if self._automation_cancelled():
                break
            if not self.drawing_enabled:
                if mouse_is_down:
                    self._mouse_up_with_settle()
                    mouse_is_down = False   # always sync to pen-up so resume re-presses
                while not self.drawing_enabled and not self.stop_flag:
                    time.sleep(0.01)
                if self.drawing_enabled and not self.stop_flag:
                    resumed_just_now = True
            if self._automation_cancelled() or self.stop_flag:
                break
            base_cur = base_cell(cur)
            next_x, next_y = center_px(base_cur)
            is_jump = stamps_only or max(abs(int(cur[0]) - int(prev[0])), abs(int(cur[1]) - int(prev[1]))) > 1
            if is_jump:
                if mouse_is_down:
                    self._mouse_up_with_settle()
                    mouse_is_down = False
                try:
                    self._move_abs(next_x, next_y); time.sleep(self.pen_settle_delay)
                    self._pen_down(); time.sleep(self.pen_settle_delay); mouse_is_down = True
                except Exception:
                    self.stop_flag = True
                    break
                resumed_just_now = False
                mark_segment(base_cur, base_cur)
                last_x, last_y = next_x, next_y
                prev = cur
                base_prev = base_cur
                continue
            if resumed_just_now or not mouse_is_down:
                try:
                    self._move_abs(last_x, last_y); time.sleep(self.pen_settle_delay)
                    self._pen_down(); time.sleep(self.pen_settle_delay); mouse_is_down = True
                except Exception:
                    self.stop_flag = True
                    break
                resumed_just_now = False
            self.draw_line(last_x, last_y, next_x, next_y)
            if self._automation_cancelled() or self.stop_flag:
                break
            mark_segment(base_prev, base_cur)
            last_x, last_y = next_x, next_y
            prev = cur
            base_prev = base_cur
        if mouse_is_down:
            self._mouse_up_with_settle()
        return not self._automation_cancelled() and not self.stop_flag

    # ---------- v3: pocket clearing with rest machining ----------

    _POCKET_ROW_SPACING = 1.6          # row spacing vs radius (< 2 keeps swept bands overlapping)
    _POCKET_LADDER = 0.6               # next radius vs previous
    _POCKET_MAX_SIZES = 6              # size changes per color in the coarse part
    _POCKET_MIN_GAIN_CELLS = 150       # newly covered cells that justify one size change

    @staticmethod
    def _pocket_disk(r_cells: float) -> np.ndarray:
        k = int(math.floor(r_cells))
        yy, xx = np.mgrid[-k:k + 1, -k:k + 1]
        return ((xx * xx + yy * yy) <= r_cells * r_cells + 1e-6).astype(np.uint8)

    _POCKET_PATTERN = "contour"      # "contour" (offset loops chained into spirals) or "rows"

    def _pocket_plan(self, full: np.ndarray, pending: np.ndarray, clearance: np.ndarray, r_cells: float):
        """Stroke plan for one radius. Every painted route lies where the whole
        stamp stays inside the color (clearance >= r + 0.5), so no stroke can
        touch another color; the claim is exactly the dilation of the routes.

        "contour": offset loops at clearance r+1, r+1+s, ... (s <= 1.6 r), like a
        CNC contour-parallel pocket; nested loops chain into a spiral with the pen
        down, and the outermost loop finishes the whole edge in one stroke. What
        the loops miss near the medial axis gets row runs. Returns
        (polylines, covered_mask, gain) or None."""
        disk = self._pocket_disk(r_cells)
        reach = cv2.dilate(pending.astype(np.uint8), disk) > 0
        centres_ok = clearance >= self._pocket_safe_level(r_cells)
        if not np.any(centres_ok & reach):
            return None
        path = np.zeros(full.shape, dtype=np.uint8)
        items: list[tuple[np.ndarray, bool]] = []
        if str(getattr(self, "_POCKET_PATTERN", "contour")) == "contour":
            spacing = max(1.0, self._POCKET_ROW_SPACING * r_cells)
            small = r_cells <= 0.75
            # A stamp narrower than ~1.5 cells rides the border cells themselves
            # (overlap <= 0.25 cell) and finishes the whole edge in one loop.
            level = 1.0 if small else r_cells + 1.0
            top = float(clearance.max())
            while level <= top + 1e-6:
                mask = (clearance >= level).astype(np.uint8)
                contours, _hier = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
                # NONE keeps per-pixel reach filtering exact; exact straight-run
                # compression happens after splitting (_pocket_compress).
                for contour in contours:
                    pts = contour[:, 0, ::-1].astype(np.int32)          # (row, col)
                    keep = reach[pts[:, 0], pts[:, 1]]
                    if not np.any(keep):
                        continue
                    for piece, closed in self._pocket_split_loop(pts, keep):
                        simple = self._pocket_compress(piece) if small else self._pocket_simplify(piece, closed)
                        items.append((simple, closed))
                        self._pocket_trace(path, simple, closed)
                level += spacing
        covered = cv2.dilate(path, disk) > 0
        rest = pending & ~covered
        if np.any(rest):
            rest_centres = centres_ok & (cv2.dilate(rest.astype(np.uint8), disk) > 0)
            for row, a0, b0 in self._pocket_rows(rest_centres, r_cells):
                items.append((np.array([[row, a0], [row, b0]], dtype=np.int32), False))
                path[row, a0:b0 + 1] = 1
        if not items:
            return None
        # Simplified chords may run up to half a cell off the rasterised path:
        # claim half a cell less so nothing unpainted is ever marked drawn.
        claim = disk if r_cells <= 0.75 else self._pocket_disk(max(0.5, r_cells - 0.5))
        covered = (cv2.dilate(path, claim) > 0) & full
        gain = int(np.count_nonzero(covered & pending))
        return self._pocket_order(items), covered, gain

    @staticmethod
    def _pocket_safe_level(r_cells: float) -> float:
        return 1.0 if r_cells <= 0.75 else r_cells + 0.5

    def _pocket_rows(self, centres: np.ndarray, r_cells: float):
        s = max(1, int(math.floor(self._POCKET_ROW_SPACING * r_cells)))
        ys = np.flatnonzero(centres.any(axis=1))
        if ys.size == 0:
            return []
        top, bottom = int(ys[0]), int(ys[-1])
        rows = list(range(top + min(s // 2, (bottom - top) // 2), bottom + 1, s)) or [top]
        if bottom - rows[-1] > s // 2:
            rows.append(bottom)
        runs = []
        for y in rows:
            edges = np.flatnonzero(np.diff(np.concatenate(([0], centres[y].astype(np.int8), [0]))))
            runs.extend((int(y), int(a), int(b) - 1) for a, b in zip(edges[::2], edges[1::2]))
        return runs

    @staticmethod
    def _pocket_split_loop(pts: np.ndarray, keep: np.ndarray):
        """A closed contour limited to the cells that still reach unpainted ones:
        the whole loop when all of it counts, otherwise its open useful arcs."""
        if bool(np.all(keep)):
            return [(pts, True)]
        start = int(np.flatnonzero(~keep)[0])
        pts = np.roll(pts, -start, axis=0)
        keep = np.roll(keep, -start)
        pieces = []
        edges = np.flatnonzero(np.diff(np.concatenate(([0], keep.astype(np.int8), [0]))))
        for a, b in zip(edges[::2], edges[1::2]):
            pieces.append((pts[a:b], False))
        return pieces

    @staticmethod
    def _pocket_compress(pts: np.ndarray) -> np.ndarray:
        """Drop interior vertices of straight 8-connected runs (lossless)."""
        if len(pts) <= 2:
            return pts
        step = np.diff(pts, axis=0)
        turn = np.any(step[1:] != step[:-1], axis=1)
        keep = np.concatenate(([True], turn, [True]))
        return pts[keep]

    @staticmethod
    def _pocket_simplify(pts: np.ndarray, closed: bool) -> np.ndarray:
        # 0.5-cell tolerance: chords stay inside the r+0.5 clearance zone because
        # the loops themselves run at clearance >= r+1.
        if len(pts) <= 2:
            return pts
        approx = cv2.approxPolyDP(pts[:, ::-1].reshape(-1, 1, 2).astype(np.int32), 0.5, closed)
        return approx[:, 0, ::-1].astype(np.int32)

    @staticmethod
    def _pocket_trace(path: np.ndarray, pts: np.ndarray, closed: bool) -> None:
        if len(pts) == 1:
            path[pts[0][0], pts[0][1]] = 1
            return
        cv2.polylines(path, [pts[:, ::-1].reshape(-1, 1, 2).astype(np.int32)], closed, 1, 1)

    def _pocket_order(self, items):
        """Greedy nearest order from the pen's last cell. Closed loops start at
        their vertex nearest to the pen, open strokes at their closer end, so
        nested loops chain into one spiral (the connector stays inside)."""
        if not items:
            return []
        anchor = getattr(self, "_last_pen_cell", None)
        pos = (np.array([float(anchor[1]), float(anchor[0])]) if isinstance(anchor, (tuple, list)) and len(anchor) == 2
               else items[0][0][0].astype(np.float64))
        left = list(range(len(items)))
        ordered = []
        while left:
            best = None
            for idx in left:
                pts, closed = items[idx]
                d = np.sum((pts - pos) ** 2, axis=1)
                if closed:
                    k = int(np.argmin(d))
                    cand = (float(d[k]), idx, k, False)
                else:
                    cand = (float(d[0]), idx, 0, False) if d[0] <= d[-1] else (float(d[-1]), idx, 0, True)
                if best is None or cand[0] < best[0]:
                    best = cand
            _dist, idx, k, reverse = best
            left.remove(idx)
            pts, closed = items[idx]
            if closed:
                pts = np.concatenate([np.roll(pts, -k, axis=0), np.roll(pts, -k, axis=0)[:1]])
            elif reverse:
                pts = pts[::-1]
            ordered.append([tuple(int(v) for v in p) for p in pts])
            pos = pts[-1].astype(np.float64)
        last = ordered[-1][-1]
        self._last_pen_cell = (float(last[1]), float(last[0]))
        return ordered

    def _pocket_paint(self, polylines, centres_ok: np.ndarray, x0: int, y0: int, full: np.ndarray | None = None) -> bool:
        """Draw planned polylines; consecutive ones are joined with the pen down
        only when the straight connector stays on valid centres."""
        brush = max(1, int(self.brush_size))
        h, w = centres_ok.shape
        region_w, region_h = self._region_dimensions(fallback_cols=w, fallback_rows=h, brush=brush)
        # Put the MEASURED stamp centre on the cell centre. Programs anchor stamps
        # differently (Paint: 2x2 with the cursor on its bottom-right pixel);
        # assuming a centred stamp left 1px seams or overlaps.
        off_x, off_y = self._dynamic_brush_stamp_offset(getattr(self, "_last_dynamic_brush_value", None))

        def axis(origin, length, index, offset):
            start = origin + index * brush
            end = min(start + brush, origin + max(1, int(length)))
            centre = start + (max(1, end - start) - 1) / 2.0
            return int(min(end - 1, max(start, math.floor(centre - offset + 0.5))))

        def px(cell):
            return (axis(x0, region_w, int(cell[1]), off_x), axis(y0, region_h, int(cell[0]), off_y))

        def screen_path(line):
            return [px(c) for c in line]

        def connector_ok(a, b):
            n = max(abs(a[0] - b[0]), abs(a[1] - b[1]), 1)
            for i in range(n + 1):
                r = round(a[0] + (b[0] - a[0]) * i / n)
                c = round(a[1] + (b[1] - a[1]) * i / n)
                if not (0 <= r < h and 0 <= c < w) or not centres_ok[r, c]:
                    return False
            return True

        down = False
        last = None
        try:
            for line in polylines:
                if self._automation_cancelled() or self.stop_flag:
                    break
                if not self.drawing_enabled:
                    if down:
                        self._mouse_up_with_settle()
                        down = False
                    while not self.drawing_enabled and not self.stop_flag:
                        time.sleep(0.01)
                    if self.stop_flag:
                        break
                start = line[0]
                if down and last is not None and connector_ok(last, start):
                    if last != start:
                        self.draw_line(*px(last), *px(start))
                else:
                    if down:
                        self._mouse_up_with_settle()
                    self._move_abs(*px(start)); time.sleep(self.pen_settle_delay)
                    self._pen_down(); time.sleep(self.pen_settle_delay)
                    down = True
                points = screen_path(line)
                for a, b in zip(points, points[1:]):
                    if self._automation_cancelled() or self.stop_flag:
                        break
                    self.draw_line(*a, *b)
                last = line[-1]
        except Exception:
            log.exception("pocket paint failed")
            self.stop_flag = True
        finally:
            if down:
                self._mouse_up_with_settle()
        return not self._automation_cancelled() and not self.stop_flag

    def _pocket_fill(self, cluster_id, x0: int, y0: int, radii_px: list[float]) -> bool:
        """Rest machining: for each radius (biggest first) paint only where the
        stamp fits inside the color and still reaches unpainted cells; each size
        is applied only when its plan pays for the size change."""
        if self.cluster_map is None or self.drawn_mask is None:
            return False
        brush = max(1, int(self.brush_size))
        full = self.cluster_map == cluster_id
        clearance = self._dynamic_brush_v2_clearance_cells(full)
        ran = False
        for radius_px in radii_px:
            if self._automation_cancelled() or self.stop_flag:
                break
            pending = full & (~self.drawn_mask)
            if not np.any(pending):
                break
            value = self._dynamic_brush_v2_value_for_radius_px(radius_px)
            if value is None:
                continue
            # The quantized control value may paint WIDER than asked: plan with the real radius.
            real_px = float(self._dynamic_brush_v2_radius_px_for_value(value) or radius_px)
            r_cells = real_px / brush
            plan = self._pocket_plan(full, pending, clearance, r_cells)
            if plan is None:
                continue
            segments, covered, gain = plan
            if gain < self._POCKET_MIN_GAIN_CELLS and radius_px != radii_px[-1]:
                continue
            if not self._apply_dynamic_brush_value(value):
                break
            centres_ok = clearance >= self._pocket_safe_level(r_cells)
            if not self._pocket_paint(segments, centres_ok, x0, y0, full):
                break
            self._mark_dynamic_brush_coverage(covered)
            ran = True
        return ran

    def _pocket_coarse_radii(self, full: np.ndarray) -> list[float]:
        """Coarse ladder from MEASURED stamp sizes only, biggest that fits first.
        Interpolating between calibration samples is unsafe on non-linear
        controls (Paint's slider painted smaller than predicted and the claimed
        ring stayed white), so no size between measurements is ever planned."""
        table = self._dynamic_brush_v2_radius_table()
        if table is None:
            return []
        radii = table[0]
        brush = max(1, int(self.brush_size))
        r_min = float(radii[0])
        floor_px = max(1.6 * r_min, float(getattr(self, "_POCKET_FLOOR_CELLS", 2.0)) * brush)
        deepest_px = (float(self._dynamic_brush_v2_clearance_cells(full).max()) - 0.5) * brush
        measured = sorted({round(float(r), 3) for r in radii}, reverse=True)
        return [r for r in measured if floor_px <= r <= deepest_px][: self._POCKET_MAX_SIZES]

    def _draw_coarse_size_major(self, x0: int, y0: int) -> None:
        """Coarse pass ordered by SIZE, not by colour: each measured size is set
        once and used for every colour where it still fits (live Paint: a size
        change cost ~0.6 s and dominated the draw time). Coarse strokes never
        touch other colours, so the colour order inside a size is free — the
        currently picked colour goes first to save a pick."""
        if self.cluster_map is None or self.drawn_mask is None:
            return
        table = self._dynamic_brush_v2_radius_table()
        if table is None:
            return
        brush = max(1, int(self.brush_size))
        colours = []
        for hex_color, cluster_id, _count, _rgb in self.color_palette:
            if cluster_id is None:
                continue
            pending = self._brush_pass_pending_mask(cluster_id)
            if pending is None or not self._dynamic_brush_v2_color_has_tier_potential(pending.astype(np.uint8) * 255):
                continue
            full = self.cluster_map == cluster_id
            colours.append({"hex": hex_color, "id": cluster_id, "full": full,
                            "clearance": self._dynamic_brush_v2_clearance_cells(full)})
        if not colours:
            return
        ladder = sorted({r for c in colours for r in self._pocket_coarse_radii(c["full"])}, reverse=True)
        picked = getattr(self, "_current_pick_cluster", None)
        for radius_px in ladder[: self._POCKET_MAX_SIZES]:
            if self._automation_cancelled() or self.stop_flag:
                return
            value = self._dynamic_brush_v2_value_for_radius_px(radius_px)
            if value is None:
                continue
            r_cells = float(self._dynamic_brush_v2_radius_px_for_value(value) or radius_px) / brush
            jobs = []
            for colour in colours:
                if float(colour["clearance"].max()) < r_cells + 0.5:
                    continue
                pending = self._brush_pass_pending_mask(colour["id"])
                if pending is None or not np.any(pending):
                    continue
                plan = self._pocket_plan(colour["full"], pending, colour["clearance"], r_cells)
                if plan is None or plan[2] < self._POCKET_MIN_GAIN_CELLS:
                    continue
                jobs.append((colour, plan))
            if not jobs:
                continue
            jobs.sort(key=lambda job: job[0]["id"] != picked)
            if not self._apply_dynamic_brush_value(value):
                return
            for colour, plan in jobs:
                if self._automation_cancelled() or self.stop_flag:
                    return
                while not self.drawing_enabled and not self.stop_flag:
                    time.sleep(0.01)
                if colour["id"] != picked:
                    self._log(f"[SIZE {radius_px * 2:.0f}px] Color {colour['hex']} (ID:{colour['id']}, coarse)")
                    self.pick_color(colour["hex"], colour["id"])
                    picked = colour["id"]
                    self._current_pick_cluster = picked
                    if self._automation_cancelled() or self.stop_flag:
                        return
                polylines, covered, _gain = plan
                centres_ok = colour["clearance"] >= self._pocket_safe_level(r_cells)
                if not self._pocket_paint(polylines, centres_ok, x0, y0, colour["full"]):
                    return
                self._mark_dynamic_brush_coverage(covered)


    _TEXT_AUTO_VALUES = (1, 2, 3, 5, 8, 13, 21, 34, 55, 89, 144, 233)
    # Track positions growing from the small end (about x1.4 per step).
    _SLIDER_AUTO_POSITIONS = (0.0, 0.01, 0.02, 0.035, 0.055, 0.08, 0.12, 0.17, 0.24, 0.33, 0.45, 0.6, 0.8, 1.0)
    dynamic_brush_text_auto = True

    def _dynamic_brush_flip_reversed_slider(self, samples) -> bool:
        """Sliders differ in which end is "bigger". If the measured stamps shrink
        towards the end assumed to be the maximum, swap the track ends and mirror
        the sampled positions instead of rejecting the calibration."""
        rows = []
        for sample in samples or ():
            try:
                radius = float(sample["point_reach_cells"]) * float(sample.get("brush_px", self.brush_size) or 1)
                rows.append((float(sample["value"]), radius, sample))
            except Exception:
                continue
        if len(rows) < 2:
            return False
        rows.sort(key=lambda row: row[0])
        low, high = rows[0][1], rows[-1][1]
        if not (low > high * 1.25 and low - high >= 1.0):
            return False
        params = self.dynamic_brush_slider_params
        if not isinstance(params, (tuple, list)) or len(params) != 4:
            return False
        orientation, fixed, coord_max, coord_min = params
        self.dynamic_brush_slider_params = (orientation, fixed, coord_min, coord_max)
        span = float(self.dynamic_brush_min_value) + float(self.dynamic_brush_max_value)
        for _value, _radius, sample in rows:
            sample["value"] = self._quantize_dynamic_brush_value(span - float(sample["value"]))
        self._log("Dynamic brush: the slider grows the other way; track ends swapped automatically.")
        return True

    def _dynamic_brush_stamp_offset(self, value) -> tuple[float, float]:
        """Measured stamp-centre offset from the cursor for the calibrated size
        nearest to `value` (0, 0 for old calibrations without it)."""
        samples = self._dynamic_brush_calibration_samples() or []
        if value is None or not samples:
            return 0.0, 0.0
        try:
            row = min(samples, key=lambda r: abs(float(r.get("value", 0.0)) - float(value)))
            return float(row.get("offset_x_px", 0.0) or 0.0), float(row.get("offset_y_px", 0.0) or 0.0)
        except Exception:
            return 0.0, 0.0

    @staticmethod
    def slider_track_offset(strip: np.ndarray, *, min_share: float = 0.6) -> int | None:
        """Column of a slider track inside `strip` (rows = along the track,
        columns = across it): the track is the column that differs from its row's
        typical colour along most of the length. None when no such line exists."""
        img = np.asarray(strip, dtype=np.int16)
        if img.ndim != 3 or img.shape[0] < 8 or img.shape[1] < 3:
            return None
        row_median = np.median(img, axis=1, keepdims=True)
        differs = np.abs(img - row_median).max(axis=2) > 25
        share = differs.mean(axis=0)
        best = int(np.argmax(share))
        return best if share[best] >= min_share else None

    @staticmethod
    def slider_track_run(line: np.ndarray, *, max_gap: int = 3) -> tuple[int, int] | None:
        """Longest stretch of `line` (bool per pixel along the track) that is the
        track, bridging gaps up to `max_gap` px (the thumb's anti-aliased edge)."""
        idx = np.flatnonzero(np.asarray(line, dtype=bool))
        if idx.size == 0:
            return None
        best = (int(idx[0]), int(idx[0]))
        start = prev = int(idx[0])
        for i in idx[1:]:
            i = int(i)
            if i - prev > max_gap + 1:
                if prev - start > best[1] - best[0]:
                    best = (start, prev)
                start = i
            prev = i
        if prev - start > best[1] - best[0]:
            best = (start, prev)
        return best

    def refine_slider_params(self, params):
        """Aim at the REAL track: the line beside the user's frame (frames are
        often drawn a few px off a thin track) and its true ends (frames stop
        short or overshoot the end caps; a press past the cap is ignored and one
        short of it misses the smallest/largest size — live Paint). The search
        widens until the found line ends inside it."""
        try:
            orientation, fixed, coord_max, coord_min = params
            lo, hi = sorted((int(coord_min), int(coord_max)))
        except Exception:
            return params
        vertical = str(orientation) == "vertical"
        margin = 10

        def grab(reach):
            if vertical:
                box = (int(fixed) - margin, lo - reach, int(fixed) + margin + 1, hi + reach + 1)
                return np.asarray(capture_screen(bbox=box).convert("RGB"))
            box = (lo - reach, int(fixed) - margin, hi + reach + 1, int(fixed) + margin + 1)
            return np.asarray(capture_screen(bbox=box).convert("RGB")).transpose(1, 0, 2)

        reach = max(40, int((hi - lo) * 0.35))
        try:
            strip = grab(reach)
        except Exception:
            return params
        column = self.slider_track_offset(strip[reach:reach + (hi - lo) + 1])
        if column is None:
            return params
        new_fixed = float(int(fixed) - margin + column)
        run = None
        for _ in range(4):
            # Ends: compare with the PANEL background, not the row median — a wide
            # thumb becomes the row median and its dark centre split the track, so
            # the "end" landed mid-track and the smallest size was never reached.
            img = strip.astype(np.int16)
            background = np.median(img.reshape(-1, 3), axis=0)
            differs = np.abs(img - background).max(axis=2) > 15
            run = self.slider_track_run(differs[:, column], max_gap=6)
            if run is None or (run[0] > 0 and run[1] < len(differs) - 1) or reach >= 800:
                break
            reach *= 2
            try:
                strip = grab(reach)
            except Exception:
                break
        if run is None or run[1] - run[0] < (hi - lo) * 0.5:
            return (orientation, new_fixed, coord_max, coord_min)
        start, end = lo - reach + run[0], lo - reach + run[1]
        if float(coord_max) <= float(coord_min):
            return (orientation, new_fixed, float(start), float(end))
        return (orientation, new_fixed, float(end), float(start))

    def _dynamic_brush_drop_contradicting_extras(self, samples, required_values):
        """Extra refinement probes near a steep end of a slider can disagree by a
        pixel or two (one track pixel apart). The scheduled probes must still be
        consistent; an extra one that contradicts them is dropped, not fatal."""
        def is_required(sample):
            return any(math.isclose(float(sample["value"]), float(v), abs_tol=1e-6) for v in required_values)

        kept = [s for s in samples if is_required(s)]
        extras = sorted((s for s in samples if not is_required(s)), key=lambda s: float(s["value"]))
        dropped = 0
        for sample in extras:
            if self._dynamic_brush_samples_consistent(kept + [sample], required_values):
                kept.append(sample)
            else:
                dropped += 1
        if dropped:
            self._log(f"Dynamic brush: {dropped} refinement probe(s) contradicted the others and were dropped.")
        return kept
