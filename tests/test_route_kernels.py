# -*- coding: utf-8 -*-
"""Phase-5 / WS7-2: Numba route kernels. The contract is BIT-IDENTICAL visit
orders vs the pure-Python planners — every tie-break, BFS order and fallback —
across a random battery of masks, drawn-mask walls and axis modes. The flag
therefore changes planning TIME only, never a pixel."""
from __future__ import annotations

import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter import route_kernels  # noqa: E402
from helpers.sim_pen import engine_for  # noqa: E402

pytestmark = pytest.mark.skipif(not route_kernels.NUMBA_OK, reason="numba unavailable")


def _random_cases(n_cases=40, seed=123):
    rng = np.random.default_rng(seed)
    for i in range(n_cases):
        h = int(rng.integers(8, 40))
        w = int(rng.integers(8, 40))
        density = float(rng.uniform(0.45, 0.95))
        mask = ((rng.random((h, w)) < density) * 255).astype(np.uint8)
        cells = np.argwhere(mask == 255)
        if cells.shape[0] < 4:
            continue
        sr, sc = map(int, cells[rng.integers(0, cells.shape[0])])
        drawn = None
        if i % 3 == 1:  # every third case: random already-painted walls
            drawn = rng.random((h, w)) < 0.15
            drawn[sr, sc] = False
        yield i, mask, drawn, sr, sc


def _engines(mask, drawn, *, strip_axis=False):
    fast_e, _ = engine_for(mask, runlen=True, astar=False)
    slow_e, _ = engine_for(mask, runlen=True, astar=False)
    fast_e.route_kernels_numba_enabled = True
    slow_e.route_kernels_numba_enabled = False
    for e in (fast_e, slow_e):
        e.snake_turn_minimize_enabled = strip_axis
        if drawn is not None:
            e.drawn_mask = drawn.copy()
    return fast_e, slow_e


def test_snake_kernel_bit_identical_on_battery():
    checked = 0
    for i, mask, drawn, sr, sc in _random_cases():
        strip_axis = (i % 2 == 0)
        fast_e, slow_e = _engines(mask, drawn, strip_axis=strip_axis)
        fast = fast_e._dfs_greedy_snake_order(mask, sr, sc)
        slow = slow_e._dfs_greedy_snake_order(mask, sr, sc)
        assert fast == slow, f"case {i}: snake orders diverge"
        checked += 1
    assert checked >= 30


def test_pop_order_kernel_bit_identical_on_battery():
    checked = 0
    for i, mask, drawn, sr, sc in _random_cases(seed=321):
        fast_e, slow_e = _engines(mask, drawn)
        fast = fast_e._dfs_traversal_order(mask, sr, sc)
        slow = slow_e._dfs_traversal_order(mask, sr, sc)
        assert fast == slow, f"case {i}: pop orders diverge"
        checked += 1
    assert checked >= 30


def test_kernels_handle_degenerate_inputs():
    mask = np.zeros((6, 6), np.uint8)
    mask[2, 2] = 255
    e, _ = engine_for(mask, runlen=True, astar=False)
    assert e._dfs_greedy_snake_order(mask, 2, 2) == [(2, 2)]
    assert e._dfs_traversal_order(mask, 2, 2) == [(2, 2)]
    assert e._dfs_greedy_snake_order(mask, 0, 0) == []   # off-mask start


def test_full_pipeline_coverage_with_kernels():
    rng = np.random.default_rng(5)
    mask = ((rng.random((32, 32)) > 0.3) * 255).astype(np.uint8)
    e, pen = engine_for(mask, runlen=True, astar=False)
    e.route_kernels_numba_enabled = True
    e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    assert pen.missing(mask) == 0
    assert pen.stray(mask) == 0


def test_euler_parity_precheck_skips_doomed_builds():
    """Components whose Euler route is guaranteed to fail (odd vertices over the
    DP limit, greedy pairing off) must go straight to the fallback WITHOUT the
    expensive adjacency build — and the drawing outcome stays identical."""
    # the comb has ~18 odd vertices (> 12)
    mask = np.zeros((4, 21), np.uint8)
    mask[0, :] = 255
    mask[1, 0:21:2] = 255
    e, pen = engine_for(mask, runlen=True, astar=False)
    e.euler_greedy_pairing_enabled = False
    calls = []
    orig = e._build_euler_route
    e._build_euler_route = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
    e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    assert pen.missing(mask) == 0
    assert calls == [], "doomed euler build was not skipped"

    # greedy pairing ON => the route is viable, the build must still happen
    e2, pen2 = engine_for(mask, runlen=True, astar=False)
    e2.euler_greedy_pairing_enabled = True
    calls2 = []
    orig2 = e2._build_euler_route
    e2._build_euler_route = lambda *a, **k: (calls2.append(1), orig2(*a, **k))[1]
    e2.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    assert pen2.missing(mask) == 0
    assert len(calls2) >= 1


def test_kernel_flag_config_roundtrip():
    from engine.olegpainter.core import OlegPainter
    e = OlegPainter(status_callback=lambda *_a, **_k: None)
    assert e.route_kernels_numba_enabled is True
    e.route_kernels_numba_enabled = False
    cfg = e.get_config()
    fresh = OlegPainter(status_callback=lambda *_a, **_k: None)
    fresh.load_config(cfg)
    assert fresh.route_kernels_numba_enabled is False
