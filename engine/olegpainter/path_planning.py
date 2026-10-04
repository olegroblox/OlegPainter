"""path_planning.py — optional, OFF-by-default drawing-path helpers.

These are NEW, self-contained algorithms. They are used by core.py only when the
corresponding engine flag is enabled; with the flags off, the existing
DFS / Eulerian traversal is byte-for-byte untouched.

- astar_bridge(): route the pen between two NON-adjacent cells through ONLY
  drawable cells (8-directional A*), so a backtrack/jump never draws a straight
  line across off-color / background pixels. Port of LxPainter's `new_dfs_module`
  A* bridge, reworked to run on a numpy boolean mask with a hard expansion budget.

- merge_collinear_runs() / collinear_vertices_mask(): collapse a chain of grid
  cells down to its direction-change vertices. Because each kept run is a
  straight grid segment, drawing a line between consecutive vertices reproduces
  EXACTLY the same pixels as stepping cell-by-cell — but with far fewer mouse
  moves, which is faster on big fills. (Marking of drawn cells must still be done
  per cell by the caller; only the pen MOVES are merged.)

All cells are (row, col) integer tuples. Pure CPU; no Qt / driver deps.
"""
from __future__ import annotations

import heapq
from typing import List, Optional, Sequence, Tuple

import numpy as np

Cell = Tuple[int, int]

# 8-connected neighbourhood for A* (cardinal + diagonal), uniform step cost.
_DIRS8: List[Cell] = [
    (-1, 0), (1, 0), (0, -1), (0, 1),
    (-1, -1), (-1, 1), (1, -1), (1, 1),
]


def cells_adjacent(a: Cell, b: Cell) -> bool:
    """True if a and b are the same or 8-neighbours (Chebyshev distance <= 1).
    A consecutive pair that is NOT adjacent is a 'jump' that astar_bridge fills."""
    return max(abs(int(a[0]) - int(b[0])), abs(int(a[1]) - int(b[1]))) <= 1


def astar_bridge(
    start: Cell,
    goal: Cell,
    passable: np.ndarray,
    *,
    max_expansions: int = 20000,
    diagonal: bool = True,
) -> Optional[List[Cell]]:
    """8-directional A* from `start` to `goal` over a boolean grid `passable`
    (shape HxW, True where the pen may travel — i.e. cells of the current color,
    drawn or not). Returns the INTERMEDIATE cells only (excluding both `start`
    and `goal`) as a list of (row, col); the caller is already at `start` and
    will draw to `goal` itself.

    Returns the intermediate cells (a list, empty only in the degenerate start==goal
    case) on SUCCESS, or None on FAILURE — no on-color path exists (unreachable, goal
    or start not passable, malformed mask) or the expansion budget is exceeded. The
    None-vs-list distinction lets the caller TELEPORT (pen up/move/pen down) on failure
    instead of cutting a stray straight line across off-color/background pixels."""
    s = (int(start[0]), int(start[1]))
    g = (int(goal[0]), int(goal[1]))
    if s == g:
        return []
    if passable.ndim != 2:
        return None
    h, w = passable.shape
    if not (0 <= s[0] < h and 0 <= s[1] < w and 0 <= g[0] < h and 0 <= g[1] < w):
        return None
    if not bool(passable[g[0], g[1]]) or not bool(passable[s[0], s[1]]):
        return None

    def heuristic(r: int, c: int) -> int:
        # Chebyshev for 8-connected moves, Manhattan for 4-connected: admissible & tight.
        if diagonal:
            return max(abs(r - g[0]), abs(c - g[1]))
        return abs(r - g[0]) + abs(c - g[1])

    open_heap: List[Tuple[int, int, int]] = [(heuristic(*s), s[0], s[1])]
    came: dict[Cell, Cell] = {}
    gscore: dict[Cell, int] = {s: 0}
    closed: set[Cell] = set()
    expansions = 0

    while open_heap and expansions < max_expansions:
        _, r, c = heapq.heappop(open_heap)
        node = (r, c)
        if node == g:
            path: List[Cell] = [g]
            cur = g
            while cur in came:
                cur = came[cur]
                path.append(cur)
            path.reverse()  # [start, ..., goal]
            return path[1:-1]  # intermediates only
        if node in closed:
            continue
        closed.add(node)
        expansions += 1
        base = gscore[node]
        for dr, dc in (_DIRS8 if diagonal else _DIRS8[:4]):
            nr, nc = r + dr, c + dc
            if not (0 <= nr < h and 0 <= nc < w):
                continue
            if not passable[nr, nc]:
                continue
            nxt = (nr, nc)
            if nxt in closed:
                continue
            tentative = base + 1
            if tentative < gscore.get(nxt, 1 << 30):
                came[nxt] = node
                gscore[nxt] = tentative
                heapq.heappush(open_heap, (tentative + heuristic(nr, nc), nr, nc))
    return None


def _unit_step(a: Cell, b: Cell) -> Cell:
    """Sign of the step a->b on each axis, e.g. (3,0)->(1,0), (-2,2)->(-1,1)."""
    dr = int(b[0]) - int(a[0])
    dc = int(b[1]) - int(a[1])
    return ((dr > 0) - (dr < 0), (dc > 0) - (dc < 0))


def collinear_vertices_mask(cells: Sequence[Cell]) -> List[bool]:
    """Return a per-index boolean list: True where a cell is a 'vertex' that must
    be a pen MOVE target — the first cell, the last cell, any direction change,
    and any non-adjacent jump (so callers that also bridge jumps stay correct).
    Intermediate cells of a straight, adjacent run are False (their pixels are
    covered by the straight line between the surrounding vertices)."""
    n = len(cells)
    if n == 0:
        return []
    if n <= 2:
        return [True] * n
    out = [False] * n
    out[0] = True
    out[-1] = True
    prev_dir = _unit_step(cells[0], cells[1])
    for i in range(1, n - 1):
        a, b = cells[i], cells[i + 1]
        # A run can only be merged across cells that are 8-adjacent (a real grid
        # segment). A jump forces a vertex on both sides.
        if not cells_adjacent(cells[i - 1], a):
            out[i] = True
            prev_dir = _unit_step(a, b)
            continue
        d = _unit_step(a, b)
        if d != prev_dir or not cells_adjacent(a, b):
            out[i] = True
            prev_dir = d
    return out


def merge_collinear_runs(cells: Sequence[Cell]) -> List[Cell]:
    """Convenience: the cells kept by collinear_vertices_mask (direction-change
    vertices + endpoints). Drawing straight lines between them reproduces the
    same pixels as the full chain."""
    keep = collinear_vertices_mask(cells)
    return [c for c, k in zip(cells, keep) if k]


__all__ = [
    "cells_adjacent",
    "astar_bridge",
    "collinear_vertices_mask",
    "merge_collinear_runs",
]
