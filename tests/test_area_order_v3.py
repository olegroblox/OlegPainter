# -*- coding: utf-8 -*-
"""Phase-1 region order v3 (docs/plans/WS2_region_order.md): Or-opt segment
relocation, asymmetric entry/exit routing, inter-color anchor and the
_reset_draw_progress anchor-leak fix."""
from __future__ import annotations

import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers.sim_pen import engine_for  # noqa: E402


def _bare_engine():
    from engine.olegpainter.core import OlegPainter
    e = OlegPainter()
    e.status_callback = lambda *_a, **_k: None
    return e


def _path_len(pos, order):
    pts = pos[np.asarray(order)]
    return float(np.sum(np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))))


# --- Or-opt ---

def test_or_opt_relocates_a_misplaced_point():
    e = _bare_engine()
    pos = np.array([(0.0, 0.0), (10.0, 0.0), (20.0, 0.0), (30.0, 0.0), (40.0, 0.0)])
    bad = [0, 2, 3, 4, 1]  # point '1' stranded at the end: length 70
    improved = e._or_opt_improve_open_path(pos, bad)
    assert improved[0] == 0
    assert sorted(improved) == [0, 1, 2, 3, 4]
    assert _path_len(pos, improved) < _path_len(pos, bad) - 1e-6
    assert math.isclose(_path_len(pos, improved), 40.0, rel_tol=1e-9)


def test_or_opt_never_worse_on_random_instances():
    e = _bare_engine()
    rng = np.random.default_rng(42)
    for _ in range(100):
        n = int(rng.integers(4, 40))
        pos = rng.uniform(0, 300, size=(n, 2))
        order = list(rng.permutation(n))
        order.remove(0)
        order = [0] + order  # fixed start
        before = _path_len(pos, order)
        after_order = e._or_opt_improve_open_path(pos, order)
        assert after_order[0] == 0
        assert sorted(after_order) == list(range(n))
        assert _path_len(pos, after_order) <= before + 1e-9


def test_or_opt_flag_off_keeps_two_opt_only_order():
    import cv2
    rng = np.random.default_rng(9)
    n = 30
    pts = rng.uniform(0, 400, size=(n, 2))
    labels = list(range(1, n + 1))
    stats = np.zeros((n + 1, 5), dtype=np.int64)
    stats[:, cv2.CC_STAT_AREA] = 1
    centroids = np.zeros((n + 1, 2), dtype=np.float64)
    centroids[1:] = pts

    e = _bare_engine()
    e.area_order_2opt_enabled = True
    e.area_order_or_opt_enabled = False
    only_2opt = e._nearest_neighbor_labels(labels, stats, centroids, start_anchor=(0.0, 0.0))
    e.area_order_or_opt_enabled = True
    with_oropt = e._nearest_neighbor_labels(labels, stats, centroids, start_anchor=(0.0, 0.0))

    pos_by_label = {lbl: pts[lbl - 1] for lbl in labels}

    def order_len(order):
        seq = np.array([pos_by_label[lbl] for lbl in order])
        return float(np.sum(np.hypot(np.diff(seq[:, 0]), np.diff(seq[:, 1]))))

    assert sorted(only_2opt) == labels and sorted(with_oropt) == labels
    assert order_len(with_oropt) <= order_len(only_2opt) + 1e-9


# --- entry/exit routing ---

def _two_blocks_mask():
    """Two blocks, both with >12 odd grid vertices => both go through the SNAKE
    (the Euler route would otherwise swallow simple regions and hide entries)."""
    mask = np.zeros((14, 14), np.uint8)
    mask[1:11, 2:5] = 255          # region A: rows 1..10, cols 2..4
    mask[1:11, 7:11] = 255         # region B: rows 1..10, cols 7..10
    return mask


def test_entry_exit_enters_next_region_at_nearest_cell():
    mask = _two_blocks_mask()
    e, pen = engine_for(mask, runlen=True, astar=False, entry_exit=True)
    e.area_sequence = "nearest"
    e._last_pen_cell = (0.0, 0.0)   # deterministic: region A (left) goes first
    starts = []
    orig = e._dfs_greedy_snake_order

    def spy(m, sr, sc):
        starts.append(((sr, sc), e._last_pen_cell))
        return orig(m, sr, sc)

    e._dfs_greedy_snake_order = spy
    e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    assert pen.missing(mask) == 0
    assert len(starts) == 2
    (start_a, anchor_a), (start_b, anchor_b) = starts
    assert start_a == (1, 2)        # nearest A-cell to the (0,0) anchor
    assert anchor_b is not None     # anchor = region A's REAL exit, in (x, y)
    ax, ay = anchor_b
    assert mask[int(ay), int(ax)] == 255 and int(ax) <= 4  # a real cell OF A
    # region B must be entered at its CORNER-most cell nearest to that anchor
    # (corners only — a mid-edge entry would split B's snake and add lifts)
    b_cells = np.argwhere((mask == 255) & (np.arange(mask.shape[1])[None, :] >= 7))
    rr, cc = b_cells[:, 0].astype(float), b_cells[:, 1].astype(float)
    corners = b_cells[sorted({int(np.argmin(rr + cc)), int(np.argmin(rr - cc)),
                              int(np.argmax(rr + cc)), int(np.argmax(rr - cc))})]
    d = (corners[:, 1] - ax) ** 2 + (corners[:, 0] - ay) ** 2
    expected = tuple(corners[int(np.argmin(d))])
    assert start_b == (int(expected[0]), int(expected[1]))


def test_entry_exit_off_keeps_argwhere_first_entry():
    mask = _two_blocks_mask()
    e, pen = engine_for(mask, runlen=True, astar=False, entry_exit=False)
    e.area_sequence = "nearest"
    e._last_pen_cell = (0.0, 0.0)   # deterministic: region A (left) goes first
    starts = []
    orig = e._dfs_greedy_snake_order
    e._dfs_greedy_snake_order = lambda m, sr, sc: (starts.append((sr, sc)), orig(m, sr, sc))[1]
    e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    assert pen.missing(mask) == 0
    assert starts == [(1, 2), (1, 7)]  # legacy coords[0] pin


def test_entry_exit_anchor_is_real_exit_not_centroid():
    mask = _two_blocks_mask()
    e, pen = engine_for(mask, runlen=True, astar=False, entry_exit=True)
    e.area_sequence = "nearest"
    e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    ax, ay = e._last_pen_cell
    # anchor = real exit cell of the LAST region in (x, y): a painted mask cell,
    # not the region centroid (which has fractional coords here)
    assert mask[int(ay), int(ax)] == 255
    assert pen.canvas[int(ay), int(ax)]
    assert float(ax) == int(ax) and float(ay) == int(ay)  # a cell, not a centroid


def test_entry_exit_shortens_hop_between_far_regions():
    # A is TALL and exits far from its top-left; B sits to the right, vertically
    # offset DOWN, so B's nearest cell to A's exit differs a lot from coords[0].
    mask = np.zeros((46, 60), np.uint8)
    mask[2:38, 2:15] = 255     # big region A (13 cols => snake exits bottom)
    mask[8:44, 40:58] = 255    # big region B, shifted down
    travel = {}
    downs = {}
    for flag in (False, True):
        e, pen = engine_for(mask, runlen=True, astar=False, entry_exit=flag)
        e.area_sequence = "nearest"
        e._last_pen_cell = (0.0, 0.0)   # deterministic: region A goes first
        e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
        assert pen.missing(mask) == 0
        travel[flag] = pen.travel_up
        downs[flag] = pen.downs
    # acceptance: the far-regions hop is strictly shorter with the flag, and
    # the corner-entry rule must NOT add pen lifts inside the regions
    assert travel[True] < travel[False] - 2.0, travel
    assert downs[True] <= downs[False], downs


def test_exit_cell_survives_interruption():
    mask = np.zeros((20, 20), np.uint8)
    mask[2:18, 2:18] = 255
    e, pen = engine_for(mask, runlen=True, astar=False, entry_exit=True)
    calls = []
    orig_line = pen.draw_line
    def stopping_line(x0, y0, x1, y1):
        orig_line(x0, y0, x1, y1)
        calls.append(1)
        if len(calls) == 5:
            e.stop_flag = True
    e.draw_line = stopping_line
    e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    exit_info = e._route_exit_cell
    assert exit_info is not None
    (er, ec), shape = exit_info
    assert shape == mask.shape
    assert pen.canvas[er, ec]  # the recorded exit was REALLY painted


def test_inter_color_anchor_survives_color_change():
    # two colors; every region >12 odd vertices so the snake path is exercised
    h, w = 34, 44
    cluster = np.zeros((h, w), np.int64)
    cluster[20:31, 2:7] = 1                  # color 1 region (bottom-left)
    cluster[20:31, 11:16] = 2                # color 2 region NEAR color 1 exit
    cluster[2:13, 32:37] = 2                 # color 2 region FAR
    mask1 = ((cluster == 1) * 255).astype(np.uint8)
    mask2 = ((cluster == 2) * 255).astype(np.uint8)

    e, pen = engine_for(mask1, runlen=True, astar=False, entry_exit=True)
    e.cluster_map = cluster
    e.area_sequence = "nearest"
    starts = []
    orig = e._dfs_greedy_snake_order
    e._dfs_greedy_snake_order = lambda m, sr, sc: (starts.append((sr, sc)), orig(m, sr, sc))[1]

    e.dfs_4dir_fill_current_color(mask1, 0, 0, 1)
    anchor_after_c1 = e._last_pen_cell
    assert anchor_after_c1 is not None
    e.dfs_4dir_fill_current_color(mask2, 0, 0, 2)
    # the first region of color 2 must be the NEAR one (cols 11..15)
    assert len(starts) >= 2, starts
    first_c2_start = starts[1]
    assert 11 <= first_c2_start[1] <= 15, starts


def test_reset_draw_progress_clears_anchor():
    mask = np.zeros((10, 10), np.uint8)
    mask[2:8, 2:8] = 255
    e, _pen = engine_for(mask, runlen=True, astar=False)
    e._last_pen_cell = (4.0, 4.0)
    e._route_exit_cell = ((4, 4), mask.shape)
    e._reset_draw_progress(notify=False)
    assert e._last_pen_cell is None
    assert e._route_exit_cell is None


def test_config_roundtrip_new_flags():
    e = _bare_engine()
    assert e.area_order_or_opt_enabled is True
    assert e.area_entry_exit_routing_enabled is False
    e.area_order_or_opt_enabled = False
    e.area_entry_exit_routing_enabled = True
    cfg = e.get_config()
    assert cfg["area_order_or_opt_enabled"] is False
    assert cfg["area_entry_exit_routing_enabled"] is True
    fresh = _bare_engine()
    fresh.load_config(cfg)
    assert fresh.area_order_or_opt_enabled is False
    assert fresh.area_entry_exit_routing_enabled is True
