# -*- coding: utf-8 -*-
"""Phase-1 tail: cKDTree palette assignment (bit-identical to sklearn predict),
throttled pixel progress notifications (drawn_mask still exact), and the
configurable pen_repress_settle."""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.prep_pipeline import _assign_nearest_center  # noqa: E402
from helpers.sim_pen import engine_for  # noqa: E402


def test_kdtree_assignment_matches_brute_force():
    rng = np.random.default_rng(3)
    flat = rng.random((50_000, 3))
    centers = rng.random((25, 3))
    fast = _assign_nearest_center(flat, centers)
    brute = np.argmin(((flat[:, None, :] - centers[None, :, :]) ** 2).sum(axis=2), axis=1)
    assert (fast == brute).all()


def test_pixel_progress_throttled_but_mask_exact(monkeypatch):
    mask = np.zeros((30, 40), np.uint8)
    mask[2:28, 2:38] = 255
    e, pen = engine_for(mask, runlen=True, astar=False)
    calls = []
    e.pixel_update_callback = lambda: calls.append(1)
    e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    assert pen.missing(mask) == 0
    cells = int((mask == 255).sum())
    # previously: one Qt event per cell (936 here); now: bounded by 20 Hz + flushes
    assert 1 <= len(calls) < cells / 4, (len(calls), cells)
    # the mask itself is still exact per cell
    assert int(e.drawn_mask.sum()) == cells


def test_pen_repress_settle_config_roundtrip():
    from engine.olegpainter.core import OlegPainter
    e = OlegPainter()
    e.status_callback = lambda *_a, **_k: None
    assert e.pen_repress_settle == 0.05
    e.pen_repress_settle = 0.02
    cfg = e.get_config()
    assert cfg["pen_repress_settle"] == 0.02
    fresh = OlegPainter()
    fresh.status_callback = lambda *_a, **_k: None
    fresh.load_config(cfg)
    assert fresh.pen_repress_settle == 0.02
    cfg["pen_repress_settle"] = 99  # clamped on load
    fresh.load_config(cfg)
    assert fresh.pen_repress_settle == 0.5
