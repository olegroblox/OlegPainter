"""Route engine v2 tests:
generalized-Hilbert traversal, greedy eulerization beyond the DP limit, and the
2-opt pass over the nearest-neighbour region order. All new behaviour is
flag-gated; these tests also pin the flags' legacy (off) behaviour."""
from __future__ import annotations

import math
import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.core import OlegPainter
from engine.olegpainter.space_filling import gilbert2d, gilbert_order_for_cells


# --- gilbert2d: structural properties of the curve ---

def test_gilbert2d_covers_every_cell_exactly_once():
    for w, h in [(1, 1), (1, 7), (5, 1), (2, 2), (8, 8), (16, 8), (13, 7), (10, 6), (3, 11), (12, 5)]:
        pts = list(gilbert2d(w, h))
        assert len(pts) == w * h, (w, h)
        assert len(set(pts)) == w * h, (w, h)
        assert all(0 <= x < w and 0 <= y < h for x, y in pts), (w, h)


def test_gilbert2d_steps_are_neighbour_moves():
    for w, h in [(8, 8), (16, 8), (13, 7), (10, 6), (7, 13), (9, 9)]:
        pts = list(gilbert2d(w, h))
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            assert max(abs(x1 - x0), abs(y1 - y0)) == 1, (w, h, (x0, y0), (x1, y1))


def test_gilbert2d_even_sizes_use_orthogonal_steps_only():
    for w, h in [(8, 8), (16, 8), (10, 6), (6, 10)]:
        pts = list(gilbert2d(w, h))
        for (x0, y0), (x1, y1) in zip(pts, pts[1:]):
            assert abs(x1 - x0) + abs(y1 - y0) == 1, (w, h)


def test_gilbert_order_for_cells_is_permutation():
    cells = [(r, c) for r in range(3, 9) for c in range(10, 17) if (r + c) % 5 != 0]
    ordered = gilbert_order_for_cells(cells)
    assert sorted(ordered) == sorted(cells)
    assert gilbert_order_for_cells([]) == []


# --- engine: gilbert cell order over a component ---

def _engine():
    engine = OlegPainter()
    engine.status_callback = lambda *_a, **_k: None
    return engine


def test_gilbert_cell_order_visits_undrawn_component_once():
    engine = _engine()
    mask = np.zeros((12, 12), dtype=np.uint8)
    mask[2:10, 3:11] = 255
    engine.drawn_mask = np.zeros((12, 12), dtype=bool)
    engine.drawn_mask[2, 3] = True  # painted cell is a wall
    order = engine._gilbert_cell_order(mask, 5, 5)
    expected = {(r, c) for r in range(2, 10) for c in range(3, 11)} - {(2, 3)}
    assert set(order) == expected
    assert len(order) == len(expected)


def test_gilbert_cell_order_rejects_bad_start():
    engine = _engine()
    mask = np.zeros((6, 6), dtype=np.uint8)
    mask[1:4, 1:4] = 255
    engine.drawn_mask = np.zeros((6, 6), dtype=bool)
    assert engine._gilbert_cell_order(mask, 0, 0) == []   # off-color start
    assert engine._gilbert_cell_order(mask, 99, 0) == []  # out of bounds


def test_fill_traversal_mode_normalizer_and_config_roundtrip():
    engine = _engine()
    assert engine.fill_traversal_mode == "auto"
    assert engine._normalize_fill_traversal_mode("GILBERT") == "gilbert"
    assert engine._normalize_fill_traversal_mode("nonsense") == "auto"

    engine.fill_traversal_mode = "gilbert"
    engine.euler_greedy_pairing_enabled = True
    engine.area_order_2opt_enabled = False
    cfg = engine.get_config()
    assert cfg["fill_traversal_mode"] == "gilbert"
    assert cfg["euler_greedy_pairing_enabled"] is True
    assert cfg["area_order_2opt_enabled"] is False

    fresh = _engine()
    fresh.load_config(cfg)
    assert fresh.fill_traversal_mode == "gilbert"
    assert fresh.euler_greedy_pairing_enabled is True
    assert fresh.area_order_2opt_enabled is False


# --- greedy eulerization beyond the DP odd-vertex limit ---

def _comb_cells(teeth: int = 10):
    """A comb: a top bar with a tooth under every even column. Interior bar cells
    above teeth have degree 3 and every tooth has degree 1 => far more than 12
    odd vertices, which defeats the exact-DP pairing."""
    width = teeth * 2 + 1
    cells = [(0, c) for c in range(width)]
    cells += [(1, c) for c in range(0, width, 2)]
    return cells


def test_comb_has_more_odd_vertices_than_dp_limit():
    engine = _engine()
    adjacency = engine._build_component_adjacency(_comb_cells(), engine.dfs_4dir_neighbor_offsets)
    odd = [v for v, nb in adjacency.items() if len(nb) % 2 == 1]
    assert len(odd) > engine.eulerization_max_odd_vertices


def test_euler_route_legacy_gives_up_beyond_limit():
    engine = _engine()
    engine.euler_greedy_pairing_enabled = False
    adjacency = engine._build_component_adjacency(_comb_cells(), engine.dfs_4dir_neighbor_offsets)
    assert engine._build_euler_route(adjacency) is None


def test_euler_route_greedy_pairing_covers_all_cells():
    engine = _engine()
    engine.euler_greedy_pairing_enabled = True
    cells = _comb_cells()
    adjacency = engine._build_component_adjacency(cells, engine.dfs_4dir_neighbor_offsets)
    route = engine._build_euler_route(adjacency)
    assert route is not None
    assert set(route) == set(cells)  # every cell visited, nothing foreign
    for a, b in zip(route, route[1:]):
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1, (a, b)


def test_euler_route_greedy_pairing_random_blob():
    rng = np.random.default_rng(3)
    mask = np.zeros((16, 16), dtype=bool)
    mask[1:15, 1:15] = rng.random((14, 14)) > 0.35
    import cv2
    num, labels = cv2.connectedComponents(mask.astype(np.uint8) * 255, connectivity=4)
    sizes = [(labels == i).sum() for i in range(1, num)]
    biggest = 1 + int(np.argmax(sizes))
    cells = [tuple(map(int, rc)) for rc in np.argwhere(labels == biggest)]

    engine = _engine()
    engine.euler_greedy_pairing_enabled = True
    adjacency = engine._build_component_adjacency(cells, engine.dfs_4dir_neighbor_offsets)
    route = engine._build_euler_route(adjacency)
    if route is None:  # tiny odd-count combos may legitimately use the DP path
        odd = [v for v, nb in adjacency.items() if len(nb) % 2 == 1]
        raise AssertionError(f"route None with {len(odd)} odd vertices")
    assert set(route) == set(cells)
    for a, b in zip(route, route[1:]):
        assert abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1, (a, b)


# --- 2-opt over the nearest-neighbour region order ---

def _path_len(pos, order):
    pts = pos[np.asarray(order)]
    return float(np.sum(np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))))


def test_two_opt_uncrosses_a_known_crossing():
    engine = _engine()
    pos = np.array([(0.0, 0.0), (10.0, 10.0), (10.0, 0.0), (0.0, 10.0)])
    improved = engine._two_opt_improve_open_path(pos, [0, 1, 2, 3])
    assert improved[0] == 0  # anchored start never moves
    assert sorted(improved) == [0, 1, 2, 3]
    assert _path_len(pos, improved) < _path_len(pos, [0, 1, 2, 3]) - 1e-6
    assert math.isclose(_path_len(pos, improved), 30.0, rel_tol=1e-9)


def test_nearest_neighbor_labels_2opt_never_longer_and_flag_reverts():
    import cv2
    rng = np.random.default_rng(11)
    n = 40
    pts = rng.uniform(0, 500, size=(n, 2))
    labels = list(range(1, n + 1))
    stats = np.zeros((n + 1, 5), dtype=np.int64)
    stats[:, cv2.CC_STAT_AREA] = 1
    centroids = np.zeros((n + 1, 2), dtype=np.float64)
    centroids[1:] = pts

    engine = _engine()
    engine.area_order_or_opt_enabled = False  # isolate the 2-opt comparison
    engine.area_order_2opt_enabled = False
    legacy = engine._nearest_neighbor_labels(labels, stats, centroids, start_anchor=(0.0, 0.0))
    engine.area_order_2opt_enabled = True
    tuned = engine._nearest_neighbor_labels(labels, stats, centroids, start_anchor=(0.0, 0.0))

    assert sorted(legacy) == labels
    assert sorted(tuned) == labels
    assert tuned[0] == legacy[0]  # same anchored start region

    pos_by_label = {lbl: pts[lbl - 1] for lbl in labels}

    def order_len(order):
        seq = np.array([pos_by_label[lbl] for lbl in order])
        return float(np.sum(np.hypot(np.diff(seq[:, 0]), np.diff(seq[:, 1]))))

    assert order_len(tuned) <= order_len(legacy) + 1e-9
