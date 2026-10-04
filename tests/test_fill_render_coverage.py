# -*- coding: utf-8 -*-
"""Render-level fill coverage: stub the pen primitives (shared SimPen helper),
run the REAL fill pipeline (dfs_4dir_fill_current_color) and assert every mask
cell receives ink under every flag combination. Regression net for the live bug
'ускоренная заливка не дорисовывает до конца' (the greedy snake leaked into
other same-color components, exhausting its cell budget and leaving holes) and
for the gilbert no-op gating."""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers.sim_pen import engine_for  # noqa: E402


def _masks():
    out = {}
    m = np.zeros((20, 30), np.uint8)
    m[2:18, 3:27] = 255
    out["solid_rect"] = m
    m = np.full((16, 33), 255, np.uint8)
    for c in range(33):
        if (c // 3) % 2 == 1:
            m[0:4, c] = 0
        if (c // 3) % 2 == 0:
            m[8:11, c] = 0
    out["comb"] = m
    rng = np.random.default_rng(5)
    out["random_blob"] = ((rng.random((24, 24)) > 0.3) * 255).astype(np.uint8)
    m = np.zeros((15, 15), np.uint8)
    m[7, :] = 255
    m[:, 7] = 255
    m[0, :] = 255
    m[:, 0] = 255
    out["thin_cross"] = m
    return out


COMBOS = {
    "legacy": dict(runlen=False, astar=False),
    "runlen": dict(runlen=True, astar=False),     # live bug combo: 'ускоренная заливка'
    "astar": dict(runlen=False, astar=True),
    "both": dict(runlen=True, astar=True),
    "runlen_euler_greedy": dict(runlen=True, astar=False, euler_greedy=True),
    "gilbert": dict(runlen=False, astar=False, traversal="gilbert"),
    "gilbert_runlen": dict(runlen=True, astar=False, traversal="gilbert"),
    "entry_exit": dict(runlen=False, astar=False, entry_exit=True),
    "entry_exit_runlen": dict(runlen=True, astar=False, entry_exit=True),
    "entry_exit_gilbert": dict(runlen=True, astar=False, traversal="gilbert", entry_exit=True),
}


def test_every_flag_combo_paints_every_cell():
    for mask_name, mask in _masks().items():
        for combo_name, kw in COMBOS.items():
            e, pen = engine_for(mask, **kw)
            e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
            assert pen.missing(mask) == 0, f"{mask_name}/{combo_name}: unpainted cells"
            marked_unpainted = int((e.drawn_mask & ~pen.canvas).sum())
            assert marked_unpainted == 0, f"{mask_name}/{combo_name}: marked-but-unpainted"


def test_snake_order_never_leaks_into_other_components():
    """The greedy snake's pen-lift target must stay inside the start component:
    leaking into another same-color region exhausts `total` early and leaves the
    current region with holes (the live 'не дорисовывает до конца' bug)."""
    rng = np.random.default_rng(5)
    mask = ((rng.random((24, 24)) > 0.3) * 255).astype(np.uint8)
    import cv2
    num, labels = cv2.connectedComponents(mask, connectivity=4)
    e, _pen = engine_for(mask, runlen=True, astar=False)
    for lbl in range(1, num):
        comp = {(int(r), int(c)) for r, c in np.argwhere(labels == lbl)}
        sr, sc = min(comp)
        order = e._dfs_greedy_snake_order(mask, sr, sc)
        assert set(order) == comp, f"component {lbl}: order != component cells"
        assert len(order) == len(comp), f"component {lbl}: revisits or misses"


def test_astar_bridge_premarked_seed_does_not_orphan_region():
    """Regression (found by tools/bench_routes.py): an A* bridge of an earlier
    region can pre-paint another region's FIRST cell; seeding dfs_4dir_draw with
    that drawn cell made it bail out and orphan the rest of the region. The seed
    must be picked among UNDRAWN cells."""
    rng = np.random.default_rng(7)
    mask = ((rng.random((64, 64)) > 0.35) * 255).astype(np.uint8)
    for combo in ("astar", "both"):
        e, pen = engine_for(mask, **COMBOS[combo])
        e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
        assert pen.missing(mask) == 0, f"{combo}: orphaned cells"


def test_gilbert_mode_is_actually_used_without_other_flags():
    """Regression: gilbert order was gated under run-length/A*, so with both off
    the checkbox was a silent no-op (live report: 'никакой разницы')."""
    mask = np.zeros((12, 16), np.uint8)
    mask[1:11, 2:14] = 255
    e, pen = engine_for(mask, runlen=False, astar=False, traversal="gilbert")
    calls = []
    orig = e._gilbert_cell_order
    e._gilbert_cell_order = lambda *a, **k: (calls.append(1), orig(*a, **k))[1]
    e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    assert calls, "gilbert order was never consulted with run-length/A* off"
    assert pen.missing(mask) == 0
