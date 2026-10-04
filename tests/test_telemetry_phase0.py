# -*- coding: utf-8 -*-
"""Phase-0 telemetry: canonical route metrics + JSONL session writer + engine
hooks. Telemetry must never affect drawing — coverage assertions stay green
with the flag on, and the flag off produces no files."""
from __future__ import annotations

import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.telemetry import TelemetrySession, route_metrics  # noqa: E402
from helpers.sim_pen import engine_for  # noqa: E402


# --- route_metrics: the single source of truth ---

def test_route_metrics_straight_run():
    m = route_metrics([(0, c) for c in range(5)])
    assert m["cells"] == 5 and m["unique_cells"] == 5 and m["revisits"] == 0
    assert m["draw_len"] == 4.0 and m["lifts"] == 0 and m["turns"] == 0


def test_route_metrics_l_shape_counts_one_turn():
    m = route_metrics([(0, 0), (0, 1), (0, 2), (1, 2), (2, 2)])
    assert m["turns"] == 1 and m["lifts"] == 0


def test_route_metrics_jump_and_revisit():
    m = route_metrics([(0, 0), (0, 1), (5, 5), (5, 6), (0, 1)])
    assert m["lifts"] == 2  # (0,1)->(5,5) and (5,6)->(0,1)
    assert m["revisits"] == 1
    assert m["lift_travel"] > 6.0
    assert m["draw_len"] == 2.0


def test_route_metrics_degenerate():
    assert route_metrics([])["cells"] == 0
    one = route_metrics([(3, 3)])
    assert one["cells"] == 1 and one["lifts"] == 0 and one["draw_len"] == 0.0


# --- TelemetrySession writer ---

def test_session_writes_valid_jsonl(tmp_path):
    s = TelemetrySession(enabled=True, base_dir=tmp_path)
    s.emit("session_start", flags={"x": 1})
    for i in range(20):
        s.emit("component", label=i)
    s.close(stop_flag=False)
    files = list(tmp_path.glob("session_*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").strip().splitlines()
    events = [json.loads(line) for line in lines]
    assert events[0]["event"] == "session_start"
    assert events[-1]["event"] == "session_end"
    assert len(events) == 22


def test_session_disabled_writes_nothing(tmp_path):
    s = TelemetrySession(enabled=False, base_dir=tmp_path)
    s.emit("session_start")
    s.close()
    assert list(tmp_path.glob("*.jsonl")) == []


def test_session_handles_numpy_types(tmp_path):
    s = TelemetrySession(enabled=True, base_dir=tmp_path)
    s.emit("component", size=np.int64(42), travel=np.float64(3.5), arr=np.array([1, 2]))
    s.close(ok=True)
    line = list(tmp_path.glob("*.jsonl"))[0].read_text(encoding="utf-8").splitlines()[0]
    rec = json.loads(line)
    assert rec["size"] == 42 and rec["travel"] == 3.5 and rec["arr"] == [1, 2]


def test_rotation_prunes_old_sessions(tmp_path, monkeypatch):
    import engine.olegpainter.telemetry as tm
    monkeypatch.setattr(tm, "MAX_FILES", 3)
    for i in range(5):
        p = tmp_path / f"session_2020010{i}_000000.jsonl"
        p.write_text("{}\n", encoding="utf-8")
        os.utime(p, (1000 + i, 1000 + i))
    s = TelemetrySession(enabled=True, base_dir=tmp_path)
    s.emit("session_start")
    s.close()
    files = sorted(p.name for p in tmp_path.glob("session_*.jsonl"))
    assert len(files) <= 3
    assert "session_20200100_000000.jsonl" not in files  # oldest pruned


# --- engine hooks: events flow, drawing untouched ---

def _run_fill_with_telemetry(tmp_path, enabled):
    mask = np.zeros((14, 18), np.uint8)
    mask[1:6, 2:9] = 255
    mask[8:13, 10:17] = 255
    e, pen = engine_for(mask, runlen=True, astar=False, telemetry=enabled)
    os.environ["OLEGPAINTER_TELEMETRY_DIR"] = str(tmp_path)
    try:
        e._telemetry_begin_session()
        e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
        e._telemetry_end_session()
    finally:
        os.environ.pop("OLEGPAINTER_TELEMETRY_DIR", None)
    return mask, pen, list(tmp_path.glob("session_*.jsonl"))


def test_engine_hooks_emit_components_and_counters(tmp_path):
    mask, pen, files = _run_fill_with_telemetry(tmp_path, enabled=True)
    assert pen.missing(mask) == 0  # telemetry never changes what is painted
    assert len(files) == 1
    events = [json.loads(line) for line in files[0].read_text(encoding="utf-8").splitlines()]
    kinds = [ev["event"] for ev in events]
    assert kinds[0] == "session_start" and kinds[-1] == "session_end"
    comp_events = [ev for ev in events if ev["event"] == "component"]
    assert len(comp_events) == 2  # two regions in the mask
    end = events[-1]
    assert end["counters"].get("regions") == 2
    assert events[0]["flags"]["run_length_merge_enabled"] is True


def test_engine_hooks_silent_when_disabled(tmp_path):
    mask, pen, files = _run_fill_with_telemetry(tmp_path, enabled=False)
    assert pen.missing(mask) == 0
    assert files == []
