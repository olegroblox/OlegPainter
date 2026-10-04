# -*- coding: utf-8 -*-
"""«Золотой» пин-тест слоя мыши (фаза 0, REVIEW §5.2).

Records the EXACT pen-event sequence (move/down/up/line with coordinates) the
engine produces on reference masks with ALL experimental flags off, and compares
it bit-for-bit against a committed fixture. Any change to this sequence on the
default path is a regression of the pixel-perfect mouse contract («формула C +
NOCOALESCE») — or a conscious behaviour change, in which case regenerate:

    python tests/test_golden_pin_moves.py --regen
"""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

FIXTURE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures", "golden_moves.json")


class EventPen:
    """Records every pen primitive call instead of painting."""

    def __init__(self, engine):
        self.events: list[list] = []
        engine._move_abs = lambda x, y: self.events.append(["move", int(x), int(y)])
        engine._pen_down = lambda: self.events.append(["down"])
        engine._mouse_up_with_settle = lambda *a, **k: self.events.append(["up"])
        engine.draw_line = lambda x0, y0, x1, y1, **_kw: self.events.append(
            ["line", int(x0), int(y0), int(x1), int(y1)])
        engine._automation_cancelled = lambda: False
        engine.pixel_update_callback = None
        engine.pen_settle_delay = 0.0
        engine.drawing_enabled = True
        engine.stop_flag = False


def _legacy_engine(mask):
    """Engine with the DEFAULT-PATH semantics pinned: every experimental flag off."""
    from engine.olegpainter.core import OlegPainter

    h, w = mask.shape
    e = OlegPainter()
    e.status_callback = lambda *_a, **_k: None
    e.brush_size = 1
    e.draw_region = (0, 0, w, h)
    e.run_length_merge_enabled = False
    e.astar_bridge_enabled = False
    e.cheap_bridges_enabled = False
    e.euler_greedy_pairing_enabled = False
    e.fill_traversal_mode = "auto"
    e.area_order_2opt_enabled = False
    e.telemetry_enabled = False
    e.cluster_map = np.where(mask == 255, 1, 0)
    e.drawn_mask = np.zeros((h, w), bool)
    return e


def _reference_masks():
    masks = {}
    m = np.full((16, 33), 255, np.uint8)
    for c in range(33):
        if (c // 3) % 2 == 1:
            m[0:4, c] = 0
        if (c // 3) % 2 == 0:
            m[8:11, c] = 0
    masks["comb"] = m
    rng = np.random.default_rng(5)
    masks["random_blob"] = ((rng.random((24, 24)) > 0.3) * 255).astype(np.uint8)
    return masks


def _record_all():
    out = {}
    for name, mask in _reference_masks().items():
        engine = _legacy_engine(mask)
        pen = EventPen(engine)
        engine.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
        out[name] = pen.events
    return out


def test_default_path_pen_events_are_pinned():
    assert os.path.exists(FIXTURE), "fixture missing — run with --regen once"
    golden = json.load(open(FIXTURE, encoding="utf-8"))
    current = _record_all()
    assert set(current) == set(golden)
    for name in golden:
        assert current[name] == golden[name], (
            f"{name}: default-path pen event sequence changed "
            f"({len(golden[name])} -> {len(current[name])} events). If this is an "
            f"INTENDED default behaviour change, regenerate the fixture: "
            f"python tests/test_golden_pin_moves.py --regen"
        )


if __name__ == "__main__":
    if "--regen" in sys.argv:
        os.makedirs(os.path.dirname(FIXTURE), exist_ok=True)
        data = _record_all()
        json.dump(data, open(FIXTURE, "w", encoding="utf-8"))
        total = sum(len(v) for v in data.values())
        print(f"fixture written: {FIXTURE} ({total} events)")
    else:
        print("run via pytest, or with --regen to rewrite the fixture")
