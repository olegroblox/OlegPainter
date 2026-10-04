# -*- coding: utf-8 -*-
"""Phase-3: plotter-style speed profile for paced fills + ETA interval +
monotonic percent. The sacred invariant: with the profile ON the pen visits
EXACTLY the same coordinates (pixels bit-identical) — only sleep durations
between sub-steps change, and the total paced time gets SHORTER."""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.motion_profile import (  # noqa: E402
    junction_speed_factor, stroke_time, trapezoid_dwells,
)


# --- module math ---

def test_junction_factor_geometry():
    # corners = the proven legacy speed (1.0); straight-through = cruise entry
    assert junction_speed_factor((0, 1), (0, 1), 1.0, 1.6) == 1.6        # straight
    assert junction_speed_factor((0, 1), (1, 0), 1.0, 1.6) == 1.0        # right angle
    assert junction_speed_factor((0, 1), (0, -1), 1.0, 1.6) == 1.0       # reversal
    assert junction_speed_factor(None, (0, 1), 1.0, 1.6) == 1.0          # fresh pen-down


def test_trapezoid_never_slower_than_legacy_constant_pace():
    base = 0.005
    for steps in (3, 8, 50, 200):
        dwells = trapezoid_dwells(steps, base, 1.0, 1.0, vmax_factor=1.6, accel_px=6)
        assert len(dwells) == steps - 1
        assert dwells.max() <= base + 1e-12          # corners at legacy speed, never slower
        assert dwells.sum() <= base * (steps - 1) + 1e-12
    long = trapezoid_dwells(200, base, 1.0, 1.0, vmax_factor=1.6, accel_px=6)
    assert long.sum() < base * 199 * 0.75            # long straights: >=25% faster
    assert long[0] > long[len(long) // 2]            # ramp out of the corner
    assert long[-1] > long[len(long) // 2]           # ramp into the next corner


def test_stroke_time_short_segments_degenerate():
    assert stroke_time(1, 0.005, 1.0, 1.0) == 0.0
    assert stroke_time(2, 0.005, 1.0, 1.0) > 0.0


# --- engine: pixels identical, paced time shorter ---

class VirtualPacedPen:
    """Stubs movement primitives but keeps the REAL draw_line so the paced
    sub-step path runs; accumulates virtual sleep instead of real time."""

    def __init__(self, engine):
        self.moves: list[tuple[int, int]] = []
        self.vtime = 0.0
        engine._move_abs = lambda x, y: self.moves.append((int(x), int(y)))
        engine._pen_down = lambda: None
        engine._mouse_up_with_settle = lambda *a, **k: None

        def fake_sleep(seconds, step=None, **_k):
            self.vtime += float(seconds)
            return False  # not aborted

        engine._sleep_with_abort = fake_sleep
        engine._automation_cancelled = lambda: False
        engine.pixel_update_callback = None
        engine.pen_settle_delay = 0.0
        engine.drawing_enabled = True
        engine.stop_flag = False


def _paced_engine(mask, motion_on):
    from engine.olegpainter.core import OlegPainter
    h, w = mask.shape
    e = OlegPainter()
    e.status_callback = lambda *_a, **_k: None
    e.brush_size = 1
    e.draw_region = (0, 0, w, h)
    e.run_length_merge_enabled = True
    e.astar_bridge_enabled = False
    e.telemetry_enabled = False
    e.area_fill_delay = 0.004           # the paced-fill regime the profile targets
    e.draw_delay = 0.0
    e.motion_profile_enabled = motion_on
    e.cluster_map = np.where(mask == 255, 1, -1)
    e.drawn_mask = np.zeros((h, w), bool)
    return e, VirtualPacedPen(e)


def test_profile_pixels_identical_and_time_shorter():
    mask = np.zeros((24, 40), np.uint8)
    mask[2:22, 2:38] = 255              # big solid rect: long merged runs
    results = {}
    for flag in (False, True):
        e, pen = _paced_engine(mask, flag)
        e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
        results[flag] = (list(pen.moves), pen.vtime)
    moves_off, t_off = results[False]
    moves_on, t_on = results[True]
    assert moves_on == moves_off, "speed profile must never change coordinates"
    assert t_on <= t_off + 1e-9, "profile must NEVER be slower than legacy"
    assert t_on < t_off * 0.9, (t_on, t_off)    # >=10% faster on this geometry


def test_profile_flag_off_is_legacy_constant_pace():
    mask = np.zeros((10, 30), np.uint8)
    mask[2:8, 2:28] = 255
    e, pen = _paced_engine(mask, False)
    e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    # constant pace: virtual time == fill_delay * substeps (plus nothing else)
    assert pen.vtime > 0
    per_step = 0.004
    assert abs(pen.vtime / per_step - round(pen.vtime / per_step)) < 1e-6


# --- service: ETA interval formatting + monotonic percent ---

def test_format_eta_interval_and_point():
    from PySide6.QtWidgets import QApplication
    from ui.services.painter_service import PainterService
    app = QApplication.instance() or QApplication([])
    svc = PainterService()
    point = svc._format_eta(125, 1, 3)
    assert "02:05" in point
    ranged = svc._format_eta(300, 1, 3, eta_range=(250, 380))
    assert "04:10" in ranged and "06:20" in ranged and "–" in ranged
    # too-narrow or invalid ranges collapse to the point estimate
    narrow = svc._format_eta(300, 1, 3, eta_range=(299, 301))
    assert "05:00" in narrow


def test_percent_is_monotonic_while_drawing():
    from PySide6.QtWidgets import QApplication
    from ui.services.painter_service import PainterService
    app = QApplication.instance() or QApplication([])
    svc = PainterService()
    svc._is_drawing = True
    svc._update_progress_metrics(force=True, percent_override=50)
    assert svc._progress_snapshot.get("percent") == 50
    svc._update_progress_metrics(force=True, percent_override=30)   # jitter back
    assert svc._progress_snapshot.get("percent") == 50              # held
    svc._update_progress_metrics(force=True, percent_override=62)
    assert svc._progress_snapshot.get("percent") == 62
    svc._is_drawing = False
    svc._update_progress_metrics(force=True, percent_override=0)
    assert svc._progress_snapshot.get("percent") == 0               # reset when idle


def test_motion_config_roundtrip():
    from engine.olegpainter.core import OlegPainter
    e = OlegPainter()
    e.status_callback = lambda *_a, **_k: None
    assert e.motion_profile_enabled is False
    e.motion_profile_enabled = True
    e.motion_vmax_factor = 2.0
    cfg = e.get_config()
    fresh = OlegPainter()
    fresh.status_callback = lambda *_a, **_k: None
    fresh.load_config(cfg)
    assert fresh.motion_profile_enabled is True
    assert fresh.motion_vmax_factor == 2.0
    cfg["motion_vmax_factor"] = 99
    fresh.load_config(cfg)
    assert fresh.motion_vmax_factor == 4.0  # clamped
