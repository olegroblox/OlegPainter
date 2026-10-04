# -*- coding: utf-8 -*-
"""SimPen — the shared render-level simulation pen.

Stubs the engine's pen primitives onto a virtual canvas so tests and benches can
run the REAL fill pipeline (dfs_4dir_fill_current_color and friends) and assert
what actually got painted. This is the acceptance instrument for every route
change: full coverage, no stray ink, lift counts.

Import in tests as `from helpers.sim_pen import SimPen, engine_for` (the tests/
directory is on sys.path under pytest's prepend import mode and when running a
test file directly).
"""
from __future__ import annotations

import numpy as np


class SimPen:
    """Virtual canvas: paints what the engine's pen primitives would paint.

    Counters:
      lines — draw_line calls; downs — pen_down presses (≈ lifts + 1 per
      component, the canonical "how often did the pen detach" signal).
    """

    def __init__(self, engine, h, w):
        self.canvas = np.zeros((h, w), bool)
        self.pos = (0, 0)
        self.down = False
        self.lines = 0
        self.downs = 0
        self.travel_up = 0.0  # pen-up flight distance (hops between strokes/regions)
        engine._move_abs = self.move_abs
        engine._pen_down = self.pen_down
        engine._mouse_up_with_settle = self.pen_up
        engine.draw_line = self.draw_line
        engine._automation_cancelled = lambda: False
        engine.pixel_update_callback = None
        engine.pen_settle_delay = 0.0
        engine.drawing_enabled = True
        engine.stop_flag = False

    def _paint(self, x, y):
        if 0 <= y < self.canvas.shape[0] and 0 <= x < self.canvas.shape[1]:
            self.canvas[y, x] = True

    def move_abs(self, x, y):
        if self.down:
            self._stroke(self.pos, (x, y))
        else:
            self.travel_up += float(np.hypot(x - self.pos[0], y - self.pos[1]))
        self.pos = (x, y)

    def pen_down(self):
        self.down = True
        self.downs += 1
        self._paint(*self.pos)

    def pen_up(self, *_args, **_kwargs):
        self.down = False
        return True

    def _stroke(self, a, b):
        x0, y0 = a
        x1, y1 = b
        n = max(abs(x1 - x0), abs(y1 - y0), 1)
        for i in range(n + 1):
            self._paint(round(x0 + (x1 - x0) * i / n), round(y0 + (y1 - y0) * i / n))

    def draw_line(self, x0, y0, x1, y1, **_kwargs):
        self.lines += 1
        self._stroke((x0, y0), (x1, y1))
        self.pos = (x1, y1)

    # --- canonical assertions ---

    def missing(self, mask: np.ndarray) -> int:
        """Target cells (mask==255) that never received ink."""
        return int(((mask == 255) & ~self.canvas).sum())

    def stray(self, mask: np.ndarray) -> int:
        """Ink outside the target mask (stray strokes across other colors)."""
        return int((self.canvas & ~(mask == 255)).sum())


def engine_for(mask, *, runlen, astar, euler_greedy=False, traversal="auto",
               telemetry=False, entry_exit=False):
    """A headless engine wired to a fresh SimPen over `mask` (brush=1 grid)."""
    from engine.olegpainter.core import OlegPainter

    h, w = mask.shape
    e = OlegPainter()
    e.status_callback = lambda *_a, **_k: None
    e.brush_size = 1
    e.draw_region = (0, 0, w, h)
    e.run_length_merge_enabled = runlen
    e.astar_bridge_enabled = astar
    e.euler_greedy_pairing_enabled = euler_greedy
    e.fill_traversal_mode = traversal
    e.telemetry_enabled = telemetry
    e.area_entry_exit_routing_enabled = entry_exit
    e.cluster_map = np.where(mask == 255, 1, -1)  # -1 = background, as in the app
    e.drawn_mask = np.zeros((h, w), bool)
    return e, SimPen(e, h, w)


__all__ = ["SimPen", "engine_for"]
