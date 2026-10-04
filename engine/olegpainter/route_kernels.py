# -*- coding: utf-8 -*-
"""route_kernels.py — Numba-compiled route planners (phase 5 / WS7-2).

The same algorithms as the pure-Python builders in core.py — the greedy snake
(_dfs_greedy_snake_order) and the stack-DFS pop order (_dfs_traversal_order) —
compiled to machine code. The contract is BIT-IDENTICAL ORDERS: every
tie-break, direction priority, BFS order and fallback mirrors the Python
implementation exactly (pinned by tests/test_route_kernels.py on a random
battery), so the flag changes planning TIME only, never a single pixel.

Numba is optional: when it is missing or a kernel raises, the wrappers return
None and the engine silently keeps its Python path. The JIT cache lives in
%LOCALAPPDATA% so OneDrive never syncs (or locks) compiled artifacts.
"""
from __future__ import annotations

import logging
import os

import numpy as np

log = logging.getLogger("olegpainter.engine.route_kernels")

# Keep numba's on-disk cache out of the OneDrive-synced project tree.
try:
    _cache_root = os.environ.get("LOCALAPPDATA")
    if _cache_root:
        _cache_dir = os.path.join(_cache_root, "OlegPainter", "numba_cache")
        os.makedirs(_cache_dir, exist_ok=True)
        os.environ.setdefault("NUMBA_CACHE_DIR", _cache_dir)
except Exception:
    pass

try:
    from numba import njit

    NUMBA_OK = True
except Exception:  # pragma: no cover - numba is in requirements, but stay soft
    NUMBA_OK = False

    def njit(*args, **kwargs):  # type: ignore
        def deco(fn):
            return fn
        return deco if not (args and callable(args[0])) else args[0]


@njit(cache=True)
def _flood_kernel(passable, sr, sc):
    """LIFO flood of the connected component (push order U, D, L, R — exactly
    the Python flood). Returns (comp uint8, min_r, max_r, min_c, max_c, total)."""
    h, w = passable.shape
    comp = np.zeros((h, w), dtype=np.uint8)
    stack = np.empty((h * w, 2), dtype=np.int32)
    stack[0, 0] = sr
    stack[0, 1] = sc
    top = 1
    comp[sr, sc] = 1
    total = 0
    min_r, max_r, min_c, max_c = sr, sr, sc, sc
    while top > 0:
        top -= 1
        r = stack[top, 0]
        c = stack[top, 1]
        total += 1
        if r < min_r:
            min_r = r
        if r > max_r:
            max_r = r
        if c < min_c:
            min_c = c
        if c > max_c:
            max_c = c
        for k in range(4):
            if k == 0:
                nr, nc = r - 1, c
            elif k == 1:
                nr, nc = r + 1, c
            elif k == 2:
                nr, nc = r, c - 1
            else:
                nr, nc = r, c + 1
            if 0 <= nr < h and 0 <= nc < w and comp[nr, nc] == 0 and passable[nr, nc] != 0:
                comp[nr, nc] = 1
                stack[top, 0] = nr
                stack[top, 1] = nc
                top += 1
    return comp, min_r, max_r, min_c, max_c, total


@njit(cache=True)
def _snake_walk_kernel(passable, comp, sr, sc, horizontal_first, total):
    """The greedy snake walk: prefer continuing straight, dead-end -> BFS lift to
    the nearest run-start INSIDE the component. Mirrors the Python loop in
    core._dfs_greedy_snake_order line by line (incl. the comp-leak fix)."""
    h, w = passable.shape
    # base direction priority (dr, dc)
    base_dr = np.empty(4, dtype=np.int32)
    base_dc = np.empty(4, dtype=np.int32)
    if horizontal_first:
        base_dr[0], base_dc[0] = 0, 1
        base_dr[1], base_dc[1] = 0, -1
        base_dr[2], base_dc[2] = 1, 0
        base_dr[3], base_dc[3] = -1, 0
    else:
        base_dr[0], base_dc[0] = 1, 0
        base_dr[1], base_dc[1] = -1, 0
        base_dr[2], base_dc[2] = 0, 1
        base_dr[3], base_dc[3] = 0, -1

    dirs8_r = np.array([-1, 1, 0, 0, -1, -1, 1, 1], dtype=np.int32)
    dirs8_c = np.array([0, 0, -1, 1, -1, 1, -1, 1], dtype=np.int32)

    visited = np.zeros((h, w), dtype=np.uint8)
    order = np.empty((total, 2), dtype=np.int32)
    visited[sr, sc] = 1
    order[0, 0] = sr
    order[0, 1] = sc
    count = 1
    cur_r, cur_c = sr, sc
    has_last = False
    last_dr = 0
    last_dc = 0

    prio_dr = np.empty(4, dtype=np.int32)
    prio_dc = np.empty(4, dtype=np.int32)
    queue = np.empty((h * w, 2), dtype=np.int32)

    while count < total:
        # --- dir_priority(last) ---
        if not has_last:
            for k in range(4):
                prio_dr[k] = base_dr[k]
                prio_dc[k] = base_dc[k]
        else:
            prio_dr[0] = last_dr
            prio_dc[0] = last_dc
            idx = 1
            for k in range(4):
                dr_k = base_dr[k]
                dc_k = base_dc[k]
                if (dr_k == last_dr and dc_k == last_dc) or (dr_k == -last_dr and dc_k == -last_dc):
                    continue
                prio_dr[idx] = dr_k
                prio_dc[idx] = dc_k
                idx += 1
            prio_dr[3] = -last_dr
            prio_dc[3] = -last_dc

        moved = False
        for k in range(4):
            nr = cur_r + prio_dr[k]
            nc = cur_c + prio_dc[k]
            if 0 <= nr < h and 0 <= nc < w and passable[nr, nc] != 0 and visited[nr, nc] == 0:
                visited[nr, nc] = 1
                order[count, 0] = nr
                order[count, 1] = nc
                count += 1
                last_dr = prio_dr[k]
                last_dc = prio_dc[k]
                has_last = True
                cur_r, cur_c = nr, nc
                moved = True
                break
        if moved:
            continue

        # --- nearest_run_start: FIFO BFS through ANYTHING, candidates gated on comp ---
        seen = np.zeros((h, w), dtype=np.uint8)
        head = 0
        tail = 0
        queue[tail, 0] = cur_r
        queue[tail, 1] = cur_c
        tail += 1
        seen[cur_r, cur_c] = 1
        found = False
        found_r = -1
        found_c = -1
        fb_r = -1
        fb_c = -1
        has_fb = False
        while head < tail and not found:
            r = queue[head, 0]
            c = queue[head, 1]
            head += 1
            for k in range(8):
                nr = r + dirs8_r[k]
                nc = c + dirs8_c[k]
                if not (0 <= nr < h and 0 <= nc < w) or seen[nr, nc] != 0:
                    continue
                seen[nr, nc] = 1
                if comp[nr, nc] != 0 and passable[nr, nc] != 0 and visited[nr, nc] == 0:
                    # run-start = has an unvisited passable 4-neighbour
                    is_run_start = False
                    for a in range(4):
                        if a == 0:
                            pr, pc = nr - 1, nc
                        elif a == 1:
                            pr, pc = nr + 1, nc
                        elif a == 2:
                            pr, pc = nr, nc - 1
                        else:
                            pr, pc = nr, nc + 1
                        if 0 <= pr < h and 0 <= pc < w and passable[pr, pc] != 0 and visited[pr, pc] == 0:
                            is_run_start = True
                            break
                    if is_run_start:
                        found = True
                        found_r = nr
                        found_c = nc
                        break
                    if not has_fb:
                        fb_r = nr
                        fb_c = nc
                        has_fb = True
                else:
                    queue[tail, 0] = nr
                    queue[tail, 1] = nc
                    tail += 1
        if not found:
            if not has_fb:
                break  # nothing left in this component
            found_r = fb_r
            found_c = fb_c
        visited[found_r, found_c] = 1
        order[count, 0] = found_r
        order[count, 1] = found_c
        count += 1
        cur_r, cur_c = found_r, found_c
        has_last = False
    return order[:count]


@njit(cache=True)
def _pop_order_kernel(passable, sr, sc, off_r, off_c):
    """Stack-DFS pop order — the exact _dfs_traversal_order walk (visited at
    push, appended at pop, neighbour push order = the engine offsets)."""
    h, w = passable.shape
    visited = np.zeros((h, w), dtype=np.uint8)
    stack = np.empty((h * w, 2), dtype=np.int32)
    order = np.empty((h * w, 2), dtype=np.int32)
    stack[0, 0] = sr
    stack[0, 1] = sc
    top = 1
    visited[sr, sc] = 1
    n = 0
    n_off = off_r.shape[0]
    while top > 0:
        top -= 1
        r = stack[top, 0]
        c = stack[top, 1]
        order[n, 0] = r
        order[n, 1] = c
        n += 1
        for k in range(n_off):
            nr = r + off_r[k]
            nc = c + off_c[k]
            if 0 <= nr < h and 0 <= nc < w and passable[nr, nc] != 0 and visited[nr, nc] == 0:
                visited[nr, nc] = 1
                stack[top, 0] = nr
                stack[top, 1] = nc
                top += 1
    return order[:n]


def _passable_u8(mask_for_color, drawn) -> np.ndarray:
    passable = (np.asarray(mask_for_color) == 255)
    if drawn is not None:
        passable = passable & ~np.asarray(drawn, dtype=bool)
    return passable.astype(np.uint8)


def snake_order_fast(mask_for_color, drawn, start_row, start_col, *, strip_axis: bool,
                     turn_minimize: bool = False):
    """Compiled twin of _dfs_greedy_snake_order. Returns the visit order as a
    list of (r, c) tuples, or None when numba is unavailable (caller falls back
    to the Python path). `strip_axis`/`turn_minimize`: pick the snake axis by
    strip count (the snake_turn_minimize_enabled flag) instead of bbox aspect."""
    if not NUMBA_OK:
        return None
    passable = _passable_u8(mask_for_color, drawn)
    sr, sc = int(start_row), int(start_col)
    comp, min_r, max_r, min_c, max_c, total = _flood_kernel(passable, sr, sc)
    if total <= 0:
        return []
    horizontal_first = (max_c - min_c) >= (max_r - min_r)
    if strip_axis or turn_minimize:
        comp_bool = comp.astype(bool)
        h_strips = int((comp_bool[:, 1:] & ~comp_bool[:, :-1]).sum() + comp_bool[:, 0].sum())
        v_strips = int((comp_bool[1:, :] & ~comp_bool[:-1, :]).sum() + comp_bool[0, :].sum())
        horizontal_first = h_strips <= v_strips
    order = _snake_walk_kernel(passable, comp, sr, sc, horizontal_first, total)
    return [(int(order[i, 0]), int(order[i, 1])) for i in range(order.shape[0])]


def pop_order_fast(mask_for_color, drawn, start_row, start_col, offsets):
    """Compiled twin of _dfs_traversal_order. None => caller's Python path."""
    if not NUMBA_OK:
        return None
    mask = np.asarray(mask_for_color)
    if drawn is not None and np.asarray(drawn).shape != mask.shape:
        return None  # foreign grid: let the Python path keep its own semantics
    passable = _passable_u8(mask, drawn)
    off = list(offsets)
    off_r = np.array([int(d[0]) for d in off], dtype=np.int32)
    off_c = np.array([int(d[1]) for d in off], dtype=np.int32)
    order = _pop_order_kernel(passable, int(start_row), int(start_col), off_r, off_c)
    return [(int(order[i, 0]), int(order[i, 1])) for i in range(order.shape[0])]


__all__ = ["snake_order_fast", "pop_order_fast", "NUMBA_OK"]
