# -*- coding: utf-8 -*-
"""Smart ETA (measured two-term model) + total-time-on-completion.

Live report being fixed: with draw_delay=0.0001 the config-derived region
weight lies (OS sleep granularity dwarfs the configured per-pixel delay), so
the ETA said '5 sec left' while hundreds of slow tiny regions remained. The
smart model regresses MEASURED elapsed time on (pixels_done, regions_done)
and prices the remaining work at the observed rates."""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from ui.services.painter_service import solve_two_term_eta  # noqa: E402
from helpers.sim_pen import engine_for  # noqa: E402


def _samples(a, b, steps):
    """Cumulative (elapsed, px_done, regions_done) for true rates a, b."""
    out = []
    px = rg = 0.0
    for dpx, drg in steps:
        px += dpx
        rg += drg
        out.append((a * px + b * rg, px, rg))
    return out


def test_recovers_true_rates_and_prices_remaining_regions():
    a, b = 0.001, 0.08  # 1 ms per pixel, 80 ms per region (his delay profile)
    # big blobs first (many px, few regions), then tiny dots (few px, many regions)
    steps = [(500, 1), (400, 1), (300, 2), (50, 10), (30, 12), (20, 15)]
    samples = _samples(a, b, steps)
    # remaining: 200 px spread over 150 tiny regions
    eta = solve_two_term_eta(samples, px_remaining=200, regions_remaining=150)
    true_eta = a * 200 + b * 150  # = 12.2 s
    assert eta is not None
    assert abs(eta - true_eta) < 0.5, (eta, true_eta)
    # the pixel-ratio model would claim ~1.6 s here — the lie the user saw
    t_last, px_last, _ = samples[-1]
    pixel_ratio_eta = t_last / px_last * 200
    assert eta > pixel_ratio_eta * 3


def test_falls_back_to_single_rate_when_collinear():
    # regions grow in lockstep with pixels -> two-term split is meaningless
    samples = _samples(0.002, 0.0, [(100, 1)] * 8)
    eta = solve_two_term_eta(samples, px_remaining=300, regions_remaining=3)
    assert eta is not None
    assert abs(eta - 0.002 * 300 - 0.0) < 0.2 * (0.002 * 300) + 0.05


def test_not_trustworthy_with_too_few_samples():
    assert solve_two_term_eta([(1.0, 100, 1)], 50, 1) is None
    assert solve_two_term_eta([], 50, 1) is None


def test_engine_eta_inputs_count_regions_consistently():
    mask = np.zeros((20, 30), np.uint8)
    mask[2:8, 2:8] = 255       # region 1
    mask[2:8, 12:18] = 255     # region 2
    mask[12:18, 2:8] = 255     # region 3
    e, pen = engine_for(mask, runlen=True, astar=False)
    e._eta_total_regions = e._count_regions_per_color_total()
    assert e._eta_total_regions == 3
    e._telemetry_begin_session()  # resets the regions counter
    e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    done_px, total_px, regions_done, regions_total = e.get_eta_inputs()
    assert regions_done == 3 and regions_total == 3
    assert done_px == total_px == int((mask == 255).sum())


def test_count_regions_per_color_total_two_colors():
    from engine.olegpainter.core import OlegPainter
    e = OlegPainter()
    e.status_callback = lambda *_a, **_k: None
    cluster = np.full((10, 20), -1, np.int64)
    cluster[1:4, 1:5] = 0       # color 0: one region
    cluster[1:4, 8:12] = 1      # color 1: region a
    cluster[6:9, 8:12] = 1      # color 1: region b
    e.cluster_map = cluster
    assert e._count_regions_per_color_total() == 3


def test_record_drawing_duration_snapshot():
    import time as _time
    from engine.olegpainter.core import OlegPainter
    e = OlegPainter()
    e.status_callback = lambda *_a, **_k: None
    e.drawing_start_time = _time.time() - 65.0
    e.paused_time_accumulated = 5.0
    e.drawing_enabled = True
    elapsed = e._record_drawing_duration()
    assert elapsed is not None
    assert 59.0 <= elapsed <= 61.5
    assert e.last_drawing_duration == elapsed


def test_smart_eta_config_roundtrip():
    from engine.olegpainter.core import OlegPainter
    e = OlegPainter()
    e.status_callback = lambda *_a, **_k: None
    assert e.smart_eta_enabled is True
    e.smart_eta_enabled = False
    cfg = e.get_config()
    assert cfg["smart_eta_enabled"] is False
    fresh = OlegPainter()
    fresh.status_callback = lambda *_a, **_k: None
    fresh.load_config(cfg)
    assert fresh.smart_eta_enabled is False
