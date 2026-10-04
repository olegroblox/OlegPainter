# -*- coding: utf-8 -*-
"""Greedy contiguous 'snake' fill order (A*-off path): lays long strokes like the old
DFS but WITHOUT the scattered teleport 'dot-spam' AND without overdraw — every cell is
visited exactly once; the pen lifts only a handful of times to a stroke-start. Engine:
_dfs_greedy_snake_order, used by dfs_4dir_draw when astar_bridge_enabled is False."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from ui.services.painter_service import PainterService  # noqa: E402

_app = QApplication.instance() or QApplication([])


def _cheb(a, b):
    return max(abs(a[0] - b[0]), abs(a[1] - b[1]))


def _isolated_dots(order):
    """Single-cell runs = cells whose incoming AND outgoing steps are both non-adjacent
    (what _draw_cell_sequence would teleport-press as a lone dot)."""
    n = len(order); d = 0
    for i in range(n):
        p = i > 0 and _cheb(order[i - 1], order[i]) <= 1
        nx = i < n - 1 and _cheb(order[i], order[i + 1]) <= 1
        if not p and not nx:
            d += 1
    return d


def _component(mask, sr, sc):
    from collections import deque
    h, w = mask.shape
    seen = {(sr, sc)}
    dq = deque([(sr, sc)])
    while dq:
        r, c = dq.popleft()
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            nr, nc = r + dr, c + dc
            if 0 <= nr < h and 0 <= nc < w and mask[nr, nc] == 255 and (nr, nc) not in seen:
                seen.add((nr, nc)); dq.append((nr, nc))
    return seen


def _multi_band_comb(W=33, H=16):
    m = np.full((H, W), 255, np.uint8)
    for c in range(W):
        if (c // 3) % 2 == 1:
            m[0:4, c] = 0
        if (c // 3) % 2 == 0:
            m[8:11, c] = 0
    return m


class GreedySnakeFillTests(unittest.TestCase):
    def setUp(self):
        self.svc = PainterService()
        self.e = self.svc.engine

    def tearDown(self):
        self.svc.shutdown(); self.svc.deleteLater(); _app.processEvents()

    def test_no_overdraw_and_full_coverage(self):
        mask = _multi_band_comb()
        self.e.drawn_mask = np.zeros(mask.shape, bool)
        sr, sc = map(int, np.argwhere(mask == 255)[0])
        order = self.e._dfs_greedy_snake_order(mask, sr, sc)
        comp = _component(mask, sr, sc)
        self.assertEqual(len(order), len(comp), "every cell visited exactly once (no overdraw)")
        self.assertEqual(set(order), comp, "full coverage, no holes")

    def test_kills_dot_spam(self):
        mask = _multi_band_comb()
        self.e.drawn_mask = np.zeros(mask.shape, bool)
        sr, sc = map(int, np.argwhere(mask == 255)[0])
        dfs = self.e._dfs_traversal_order(mask, sr, sc)
        snake = self.e._dfs_greedy_snake_order(mask, sr, sc)
        dfs_dots = _isolated_dots(dfs)
        snake_dots = _isolated_dots(snake)
        self.assertGreater(dfs_dots, 5, "comb must dot-spam under raw DFS")
        self.assertLessEqual(snake_dots, 1, "snake reduces to the topological floor (<=1)")
        self.assertLess(snake_dots, dfs_dots)

    def test_solid_rect_is_one_clean_pass(self):
        H, W = 10, 30
        mask = np.full((H, W), 255, np.uint8)
        self.e.drawn_mask = np.zeros((H, W), bool)
        order = self.e._dfs_greedy_snake_order(mask, 0, 0)
        self.assertEqual(len(order), H * W)            # no overdraw
        self.assertEqual(set(order), _component(mask, 0, 0))
        self.assertEqual(_isolated_dots(order), 0)     # zero dots, one continuous snake

    def test_honors_drawn_mask_wall(self):
        H, W = 6, 8
        mask = np.full((H, W), 255, np.uint8)
        self.e.drawn_mask = np.zeros((H, W), bool)
        self.e.drawn_mask[2, 4] = True                 # already painted -> wall
        order = self.e._dfs_greedy_snake_order(mask, 0, 0)
        self.assertNotIn((2, 4), order)
        self.assertEqual(len(order), H * W - 1)


if __name__ == "__main__":
    unittest.main()
