# -*- coding: utf-8 -*-
"""Phase-2.2: route polish (run re-chaining, never worse), snake axis by strip
count, the single-cell stray-ink fix, and the A/B session analyzer."""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.route_polish import polish_cell_order, split_runs  # noqa: E402
from engine.olegpainter.telemetry import route_metrics  # noqa: E402
from helpers.sim_pen import engine_for  # noqa: E402


# --- polish_cell_order ---

def test_polish_rechains_interleaved_runs():
    # runs laid A1, B1, A2, B2 (far hops between every run); re-chaining to
    # A1, A2, B1, B2 must shrink the lift travel
    a1 = [(0, c) for c in range(5)]
    b1 = [(20, c) for c in range(5)]
    a2 = [(2, c) for c in range(5)]
    b2 = [(22, c) for c in range(5)]
    bad = a1 + b1 + a2 + b2
    out = polish_cell_order(bad)
    assert sorted(out) == sorted(bad)
    assert out[0] == bad[0]
    before, after = route_metrics(bad), route_metrics(out)
    assert (after["lifts"], after["lift_travel"]) <= (before["lifts"], before["lift_travel"])
    assert after["lift_travel"] < before["lift_travel"]


def test_polish_never_worse_on_random_orders():
    rng = np.random.default_rng(17)
    for _ in range(50):
        n = int(rng.integers(10, 120))
        cells = {(int(rng.integers(0, 30)), int(rng.integers(0, 30))) for _ in range(n)}
        order = list(cells)
        rng.shuffle(order)
        out = polish_cell_order(order)
        assert sorted(out) == sorted(order)
        assert out[0] == order[0]
        b, a = route_metrics(order), route_metrics(out)
        assert (a["lifts"], a["lift_travel"], a["draw_len"]) <= (b["lifts"], b["lift_travel"], b["draw_len"])


def test_split_runs_basic():
    order = [(0, 0), (0, 1), (5, 5), (5, 6), (5, 7)]
    runs = split_runs(order)
    assert [len(r) for r in runs] == [2, 3]


def test_polish_full_pipeline_coverage():
    rng = np.random.default_rng(5)
    mask = ((rng.random((24, 24)) > 0.3) * 255).astype(np.uint8)
    e, pen = engine_for(mask, runlen=True, astar=False)
    e.fill_route_polish_enabled = True
    e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    assert pen.missing(mask) == 0
    assert int((e.drawn_mask & ~pen.canvas).sum()) == 0


# --- snake axis by strips ---

def _vertical_bars_mask():
    """Wide bbox (legacy rule says horizontal) but the shape is vertical bars
    joined at the bottom — vertical snaking gives far fewer turns."""
    mask = np.zeros((20, 31), np.uint8)
    for c in range(2, 30, 4):
        mask[2:18, c:c + 2] = 255   # vertical bars 16 tall, 2 wide
    mask[17, 2:30] = 255            # connector along the bottom
    return mask


def test_snake_axis_by_strips_reduces_turns():
    mask = _vertical_bars_mask()
    sr, sc = map(int, np.argwhere(mask == 255)[0])
    e, _ = engine_for(mask, runlen=True, astar=False)
    legacy = e._dfs_greedy_snake_order(mask, sr, sc)
    e2, _ = engine_for(mask, runlen=True, astar=False)
    e2.snake_turn_minimize_enabled = True
    tuned = e2._dfs_greedy_snake_order(mask, sr, sc)
    assert set(tuned) == set(legacy)            # same coverage, exactly once
    assert len(tuned) == len(legacy)
    m_legacy, m_tuned = route_metrics(legacy), route_metrics(tuned)
    assert m_tuned["turns"] < m_legacy["turns"], (m_tuned, m_legacy)


def test_snake_axis_coverage_in_pipeline():
    mask = _vertical_bars_mask()
    e, pen = engine_for(mask, runlen=True, astar=False)
    e.snake_turn_minimize_enabled = True
    e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    assert pen.missing(mask) == 0


# --- single-cell stray-ink fix ---

def test_runlen_no_stray_ink_on_noisy_mask():
    rng = np.random.default_rng(7)
    mask = ((rng.random((64, 64)) > 0.35) * 255).astype(np.uint8)
    e, pen = engine_for(mask, runlen=True, astar=False)
    e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    assert pen.missing(mask) == 0
    assert pen.stray(mask) == 0, "single-cell span sprayed ink onto foreign cells"


def test_isolated_single_cell_still_painted():
    mask = np.zeros((8, 8), np.uint8)
    mask[3, 3] = 255
    e, pen = engine_for(mask, runlen=True, astar=False)
    e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
    assert pen.missing(mask) == 0
    assert pen.stray(mask) == 0


# --- config + analyzer smoke ---

def test_new_flags_config_roundtrip():
    from engine.olegpainter.core import OlegPainter
    e = OlegPainter()
    e.status_callback = lambda *_a, **_k: None
    assert e.fill_route_polish_enabled is False
    assert e.snake_turn_minimize_enabled is False
    e.fill_route_polish_enabled = True
    e.snake_turn_minimize_enabled = True
    cfg = e.get_config()
    fresh = OlegPainter()
    fresh.status_callback = lambda *_a, **_k: None
    fresh.load_config(cfg)
    assert fresh.fill_route_polish_enabled is True
    assert fresh.snake_turn_minimize_enabled is True


def test_analyze_sessions_smoke(tmp_path, capsys):
    lines = [
        {"ts": 100.0, "event": "session_start",
         "flags": {"run_length_merge_enabled": True, "fill_traversal_mode": "fermat"}, "grid": {}},
        {"ts": 101.0, "event": "color_start", "color": "#fff"},
        {"ts": 102.0, "event": "component", "label": 1},
        {"ts": 160.0, "event": "session_end", "stop_flag": False, "drawn_share": 1.0,
         "counters": {"regions": 1, "region_travel_px": 42.0}},
    ]
    f = tmp_path / "session_20260101_000000.jsonl"
    f.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")
    f2 = tmp_path / "session_20260101_000100.jsonl"
    lines[3] = dict(lines[3], ts=130.0)
    f2.write_text("\n".join(json.dumps(line) for line in lines), encoding="utf-8")

    sys.argv = ["analyze_sessions.py", "--dir", str(tmp_path), "--ab-last", "2"]
    import importlib
    mod = importlib.import_module("tools.analyze_sessions") if False else None
    # import by path (tools/ is not a package)
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "analyze_sessions",
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools", "analyze_sessions.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.main()
    out = capsys.readouterr().out
    assert "session_20260101_000000.jsonl" in out
    assert "A/B" in out and "БЫСТРЕЕ" in out
    assert "fermat" in out
