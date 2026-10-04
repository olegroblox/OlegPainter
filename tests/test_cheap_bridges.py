from __future__ import annotations

import os

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.core import OlegPainter
from engine.olegpainter.path_planning import astar_bridge


def _u_shape():
    # A U of one colour: (0,0) and (0,4) are joined only through the bottom row.
    grid = np.zeros((4, 5), dtype=bool)
    grid[:, 0] = grid[:, 4] = grid[3, :] = True
    return grid


def test_four_connected_bridge_never_steps_diagonally():
    grid = np.ones((3, 3), dtype=bool)
    grid[1, 0] = grid[0, 1] = False  # a diagonal-only link would clip foreign corners
    assert astar_bridge((0, 0), (1, 1), grid, diagonal=False) is None
    path = astar_bridge((0, 0), (0, 4), _u_shape(), diagonal=False)
    chain = [(0, 0), *path, (0, 4)]
    assert all(abs(a[0] - b[0]) + abs(a[1] - b[1]) == 1 for a, b in zip(chain, chain[1:]))


def _engine(settle, dwell):
    engine = OlegPainter(status_callback=lambda _m: None)
    engine.pen_settle_delay, engine.mouse_release_settle, engine.pen_button_delay = settle, settle, 0.0
    engine.draw_delay = dwell
    return engine


def test_bridge_is_taken_only_when_cheaper_than_a_lift():
    grid = _u_shape()
    cheap = _engine(0.002, 0.001)  # lift 6 ms, the U detour is 3 strokes = 3 ms
    assert cheap._cheap_bridge((0, 0), (0, 4), grid, cheap._pen_lift_cost(), 0.001)
    dear = _engine(0.001, 0.002)   # lift 3 ms, detour 6 ms
    assert dear._cheap_bridge((0, 0), (0, 4), grid, dear._pen_lift_cost(), 0.002) is None


def test_far_or_disconnected_goals_lift_the_pen():
    engine = _engine(0.01, 0.001)
    grid = np.zeros((1, 30), dtype=bool)
    grid[0, :] = True
    assert engine._cheap_bridge((0, 0), (0, 29), grid, engine._pen_lift_cost(), 0.001) is None
    grid[0, 3] = False
    assert engine._cheap_bridge((0, 0), (0, 5), grid, engine._pen_lift_cost(), 0.001) is None


def test_setting_round_trips():
    engine = _engine(0.002, 0.001)
    assert engine.get_config()["cheap_bridges_enabled"] is True
    restored = OlegPainter(status_callback=lambda _m: None)
    restored.load_config({**engine.get_config(), "cheap_bridges_enabled": False})
    assert restored.cheap_bridges_enabled is False
