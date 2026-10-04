# -*- coding: utf-8 -*-
"""Phase-2: contour-parallel (fermat) fill order — peel decomposition, ring
walk, the never-worse-than-snake selector and full-pipeline coverage."""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.fermat_spiral import fermat_cell_order, peel_layers  # noqa: E402
from engine.olegpainter.telemetry import route_metrics  # noqa: E402
from helpers.sim_pen import engine_for  # noqa: E402


def _disk(n=31, r=13):
    yy, xx = np.mgrid[0:n, 0:n]
    return ((xx - n // 2) ** 2 + (yy - n // 2) ** 2 <= r * r)


def test_peel_layers_rect():
    mask = np.zeros((9, 12), bool)
    mask[1:8, 1:11] = True
    layers = peel_layers(mask)
    assert layers[0, 0] == -1
    assert layers[1, 1] == 0          # border ring
    assert layers[2, 2] == 1
    assert layers[4, 5] == int(layers.max())  # innermost


def test_fermat_order_covers_rect_with_zero_lifts():
    mask = np.zeros((20, 26), bool)
    mask[2:18, 3:23] = True
    order = fermat_cell_order(mask, (2, 3))
    assert len(order) == int(mask.sum())
    assert len(set(order)) == len(order)            # exactly once, no overdraw
    metrics = route_metrics(order)
    assert metrics["lifts"] == 0, metrics            # one continuous spiral


def test_fermat_order_covers_disk_with_zero_lifts():
    mask = _disk()
    start = tuple(map(int, np.argwhere(mask)[0]))
    order = fermat_cell_order(mask, start)
    assert len(order) == int(mask.sum())
    assert len(set(order)) == len(order)
    assert route_metrics(order)["lifts"] == 0


def test_fermat_order_rejects_bad_input():
    mask = np.zeros((6, 6), bool)
    assert fermat_cell_order(mask, (0, 0)) == []
    mask[2, 2] = True
    assert fermat_cell_order(mask, (0, 0)) == []     # start off-mask
    assert len(fermat_cell_order(mask, (2, 2))) == 1


def test_engine_selector_never_worse_than_snake():
    masks = []
    m = np.full((16, 33), 255, np.uint8)
    for c in range(33):
        if (c // 3) % 2 == 1:
            m[0:4, c] = 0
        if (c // 3) % 2 == 0:
            m[8:11, c] = 0
    masks.append(m)
    rng = np.random.default_rng(5)
    masks.append(((rng.random((24, 24)) > 0.3) * 255).astype(np.uint8))
    masks.append((_disk().astype(np.uint8)) * 255)
    for mask in masks:
        e, _pen = engine_for(mask, runlen=True, astar=False, traversal="fermat")
        sr, sc = map(int, np.argwhere(mask == 255)[0])
        order = e._fermat_cell_order(mask, sr, sc)
        snake = e._dfs_greedy_snake_order(mask, sr, sc)
        assert order, "selector returned empty on a valid component"
        assert set(order) == set(snake)              # same component, full coverage
        assert route_metrics(order)["lifts"] <= route_metrics(snake)["lifts"]


def test_fermat_full_pipeline_coverage():
    rng = np.random.default_rng(5)
    mask = ((rng.random((24, 24)) > 0.3) * 255).astype(np.uint8)
    for runlen in (False, True):
        e, pen = engine_for(mask, runlen=runlen, astar=False, traversal="fermat")
        e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
        assert pen.missing(mask) == 0
        assert int((e.drawn_mask & ~pen.canvas).sum()) == 0


def test_fermat_config_roundtrip_and_normalizer():
    from engine.olegpainter.core import OlegPainter
    e = OlegPainter()
    e.status_callback = lambda *_a, **_k: None
    assert e._normalize_fill_traversal_mode("FERMAT") == "fermat"
    e.fill_traversal_mode = "fermat"
    cfg = e.get_config()
    assert cfg["fill_traversal_mode"] == "fermat"
    fresh = OlegPainter()
    fresh.status_callback = lambda *_a, **_k: None
    fresh.load_config(cfg)
    assert fresh.fill_traversal_mode == "fermat"
