"""Tests for the new (OFF-by-default) drawing-path helpers in path_planning.py."""
import numpy as np

from engine.olegpainter.path_planning import (
    astar_bridge,
    cells_adjacent,
    collinear_vertices_mask,
    merge_collinear_runs,
)


def _bresenham(a, b):
    """Cells covered by a straight line a->b (8-connected supercover-ish)."""
    (r0, c0), (r1, c1) = a, b
    cells = []
    dr = abs(r1 - r0); dc = abs(c1 - c0)
    sr = 1 if r0 < r1 else -1
    sc = 1 if c0 < c1 else -1
    err = dr - dc
    r, c = r0, c0
    while True:
        cells.append((r, c))
        if (r, c) == (r1, c1):
            break
        e2 = 2 * err
        if e2 > -dc:
            err -= dc; r += sr
        if e2 < dr:
            err += dr; c += sc
    return cells


def test_adjacent():
    assert cells_adjacent((2, 2), (2, 3))
    assert cells_adjacent((2, 2), (3, 3))   # diagonal counts (8-conn)
    assert cells_adjacent((2, 2), (2, 2))
    assert not cells_adjacent((2, 2), (2, 4))
    assert not cells_adjacent((0, 0), (3, 0))


def test_astar_straight_excludes_endpoints():
    passable = np.ones((5, 5), dtype=bool)
    mid = astar_bridge((0, 0), (0, 4), passable)
    assert (0, 0) not in mid and (0, 4) not in mid       # endpoints excluded
    # contiguous path start..goal
    chain = [(0, 0)] + mid + [(0, 4)]
    for p, q in zip(chain, chain[1:]):
        assert cells_adjacent(p, q)
    # all on passable cells
    assert all(passable[r, c] for r, c in mid)


def test_astar_detours_around_wall():
    passable = np.ones((5, 5), dtype=bool)
    passable[0:4, 2] = False          # vertical wall, gap at row 4
    mid = astar_bridge((0, 0), (0, 4), passable)
    assert mid, "expected a detour path"
    assert all(passable[r, c] for r, c in mid)   # never steps on the wall
    chain = [(0, 0)] + mid + [(0, 4)]
    for p, q in zip(chain, chain[1:]):
        assert cells_adjacent(p, q)


def test_astar_no_path_or_blocked_goal():
    passable = np.ones((5, 5), dtype=bool)
    passable[:, 2] = False            # full wall, no gap -> unreachable
    assert astar_bridge((0, 0), (0, 4), passable) is None   # failure -> None (so caller teleports)
    passable2 = np.ones((5, 5), dtype=bool)
    passable2[0, 4] = False           # goal not passable
    assert astar_bridge((0, 0), (0, 4), passable2) is None


def test_astar_budget_bounded():
    passable = np.ones((200, 200), dtype=bool)
    # tiny budget -> should give up and return None (failure) rather than hang
    assert astar_bridge((0, 0), (199, 199), passable, max_expansions=5) is None


def test_merge_straight_run():
    run = [(0, 0), (0, 1), (0, 2), (0, 3)]
    assert merge_collinear_runs(run) == [(0, 0), (0, 3)]


def test_merge_L_shape_keeps_corner():
    chain = [(0, 0), (0, 1), (0, 2), (1, 2), (2, 2)]
    assert merge_collinear_runs(chain) == [(0, 0), (0, 2), (2, 2)]


def test_merge_keeps_jump_endpoints():
    # a jump (non-adjacent) between (0,1) and (0,5) forces vertices on both sides
    chain = [(0, 0), (0, 1), (0, 5), (0, 6)]
    keep = collinear_vertices_mask(chain)
    assert keep[0] and keep[-1]
    assert keep[1] and keep[2]    # both sides of the jump are vertices


def test_merge_preserves_pixels_for_cardinal_and_diagonal():
    # vertices drawn as straight lines must cover every original cell
    for chain in (
        [(0, 0), (0, 1), (0, 2), (0, 3), (0, 4)],                 # horizontal
        [(0, 0), (1, 0), (2, 0), (3, 0)],                         # vertical
        [(0, 0), (1, 1), (2, 2), (3, 3)],                         # 45 diagonal
        [(0, 0), (0, 1), (0, 2), (1, 2), (2, 2), (2, 3)],         # zigzag
    ):
        verts = merge_collinear_runs(chain)
        covered = set()
        for a, b in zip(verts, verts[1:]):
            covered.update(_bresenham(a, b))
        assert set(chain).issubset(covered), (chain, verts, covered)


def test_merge_short_chains():
    assert merge_collinear_runs([]) == []
    assert merge_collinear_runs([(1, 1)]) == [(1, 1)]
    assert merge_collinear_runs([(1, 1), (1, 2)]) == [(1, 1), (1, 2)]
