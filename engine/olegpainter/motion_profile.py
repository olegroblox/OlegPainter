# -*- coding: utf-8 -*-
"""motion_profile.py — plotter-style speed profiles for stroke pacing
(docs/plans/WS3_motion_profiles.md, simplified v1).

Applies ONLY when the user paces fills («Задержка заливки», area_fill_delay>0):
instead of a CONSTANT per-pixel dwell along every stroke, the dwell follows a
trapezoid — accelerate out of a corner, cruise FASTER than the configured base
speed on the straight, decelerate into the next corner (GRBL junction idea).
Net effect: same pixels in the same order (the sub-step coordinates are
untouched — the sacred _move_abs formula never changes), shorter wall time,
and the slow-downs sit exactly where a plotter would put them.

Pure math, no engine imports; the engine passes (entry_factor, exit_factor)
per stroke and the configured base dwell.
"""
from __future__ import annotations

import math

import numpy as np


def junction_speed_factor(dir_in, dir_out, corner_speed: float = 1.0,
                          vmax_factor: float = 1.6) -> float:
    """Entry SPEED (in multiples of the configured base speed) for a stroke
    given the previous stroke's direction. The key calibration: the legacy
    constant pace drives every corner at 1.0x and that is PROVEN safe in
    production — so 1.0 is the floor, never a slow-down. Straight-through
    junctions may enter at cruise speed; corners/reversals/fresh pen-downs
    enter at corner_speed (default 1.0 = exactly legacy)."""
    corner = max(0.25, float(corner_speed))
    v_max = max(corner, float(vmax_factor))
    if dir_in is None or dir_out is None:
        return corner
    ax, ay = float(dir_in[0]), float(dir_in[1])
    bx, by = float(dir_out[0]), float(dir_out[1])
    na = math.hypot(ax, ay)
    nb = math.hypot(bx, by)
    if na <= 0 or nb <= 0:
        return corner
    cos = (ax * bx + ay * by) / (na * nb)
    if cos <= 0.0:
        return corner
    return corner + (v_max - corner) * cos       # straight => cruise entry


def trapezoid_dwells(steps: int, base_delay: float, entry_speed: float, exit_speed: float,
                     *, vmax_factor: float = 1.6, accel_px: int = 6) -> np.ndarray:
    """Per-sub-step dwell times for a stroke of `steps` pixels paced at
    base_delay s/px: speed ramps entry->cruise over accel_px, cruises at
    vmax_factor x base speed, ramps down to exit_speed. All speeds are >= the
    corner floor, so every dwell <= base_delay — with default settings the
    profile is mathematically NEVER slower than the legacy constant pace."""
    n = max(1, int(steps) - 1)  # dwells sit between sub-steps
    base = max(1e-6, float(base_delay))
    v_max = max(1.0, float(vmax_factor))
    v_entry = max(0.25, min(v_max, float(entry_speed)))
    v_exit = max(0.25, min(v_max, float(exit_speed)))
    accel = max(1, int(accel_px))
    v = np.full(n, v_max, dtype=np.float64)
    ramp_in = min(accel, n)
    v[:ramp_in] = np.minimum(v[:ramp_in], np.linspace(v_entry, v_max, ramp_in))
    ramp_out = min(accel, n)
    v[n - ramp_out:] = np.minimum(v[n - ramp_out:], np.linspace(v_max, v_exit, ramp_out))
    return base / v


def stroke_time(steps: int, base_delay: float, entry_factor: float, exit_factor: float,
                *, vmax_factor: float = 1.6, accel_px: int = 6) -> float:
    """Simulated wall time of one paced stroke — the ETA building block."""
    if steps <= 1:
        return 0.0
    return float(trapezoid_dwells(steps, base_delay, entry_factor, exit_factor,
                                  vmax_factor=vmax_factor, accel_px=accel_px).sum())


__all__ = ["junction_speed_factor", "trapezoid_dwells", "stroke_time"]
