# -*- coding: utf-8 -*-
"""bench_routes.py — baseline of the route engine (phase 0 of the breakthrough plan).

For a corpus of masks × traversal combos, runs the REAL fill pipeline through the
shared SimPen and reports the canonical metrics (telemetry.route_metrics is the
single source of truth) plus planning time. Acceptance criteria of phases 1-2
("lifts(fermat) <= lifts(snake)", "-20% lifts after polish", ...) are measured
against THIS report.

Usage:
    python tools/bench_routes.py            # markdown table to stdout
    python tools/bench_routes.py --save     # also writes docs/plans/BASELINE_routes.md
"""
from __future__ import annotations

import io
import os
import sys
import time

import numpy as np

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.telemetry import route_metrics  # noqa: E402
from helpers.sim_pen import engine_for  # noqa: E402


def corpus():
    masks = {}
    m = np.zeros((20, 30), np.uint8); m[2:18, 3:27] = 255
    masks["solid_rect"] = m
    m = np.full((16, 33), 255, np.uint8)
    for c in range(33):
        if (c // 3) % 2 == 1: m[0:4, c] = 0
        if (c // 3) % 2 == 0: m[8:11, c] = 0
    masks["comb"] = m
    rng = np.random.default_rng(5)
    masks["random_blob_24"] = ((rng.random((24, 24)) > 0.3) * 255).astype(np.uint8)
    rng = np.random.default_rng(7)
    masks["noisy_blob_64"] = ((rng.random((64, 64)) > 0.35) * 255).astype(np.uint8)
    yy, xx = np.mgrid[0:40, 0:40]
    disk = ((xx - 20) ** 2 + (yy - 20) ** 2 <= 18 ** 2)
    masks["disk_40"] = (disk * 255).astype(np.uint8)
    ring = disk & ~(((xx - 20) ** 2 + (yy - 20) ** 2) <= 10 ** 2)
    masks["ring_40"] = (ring * 255).astype(np.uint8)
    return masks


COMBOS = {
    "legacy": dict(runlen=False, astar=False),
    "runlen(snake)": dict(runlen=True, astar=False),
    "astar(dfs)": dict(runlen=False, astar=True),
    "both": dict(runlen=True, astar=True),
    "gilbert": dict(runlen=True, astar=False, traversal="gilbert"),
    "euler_greedy": dict(runlen=True, astar=False, euler_greedy=True),
    "fermat": dict(runlen=True, astar=False, traversal="fermat"),
}


def order_for(e, mask, combo_kw):
    """The visit order the engine would use for the first component (for
    route_metrics); the SimPen run below exercises the FULL pipeline."""
    sr, sc = map(int, np.argwhere(mask == 255)[0])
    if combo_kw.get("traversal") == "gilbert":
        return e._gilbert_cell_order(mask, sr, sc)
    if combo_kw.get("traversal") == "fermat":
        return e._fermat_cell_order(mask, sr, sc)
    if combo_kw.get("astar"):
        return e._dfs_traversal_order(mask, sr, sc)
    return e._dfs_greedy_snake_order(mask, sr, sc)


def run():
    rows = []
    for mask_name, mask in corpus().items():
        for combo_name, kw in COMBOS.items():
            e, pen = engine_for(mask, **kw)
            t0 = time.perf_counter()
            order = order_for(e, mask, kw)
            plan_ms = (time.perf_counter() - t0) * 1000
            metrics = route_metrics(order)
            t0 = time.perf_counter()
            e.dfs_4dir_fill_current_color(mask.copy(), 0, 0, 1)
            fill_ms = (time.perf_counter() - t0) * 1000
            rows.append({
                "mask": mask_name, "combo": combo_name,
                "missing": pen.missing(mask), "stray": pen.stray(mask),
                "downs": pen.downs, "lines": pen.lines,
                "order_lifts": metrics["lifts"], "order_turns": metrics["turns"],
                "order_revisits": metrics["revisits"],
                "plan_ms": round(plan_ms, 1), "fill_ms": round(fill_ms, 1),
            })
    return rows


def render(rows) -> str:
    out = io.StringIO()
    out.write("# Baseline маршрутного движка (фаза 0)\n\n")
    out.write(f"Снято: {time.strftime('%Y-%m-%d %H:%M')}. Метрики — `telemetry.route_metrics`; "
              "downs/lines — фактические нажатия/штрихи пера через SimPen-конвейер.\n\n")
    out.write("| mask | combo | missing | stray | downs | lines | lifts | turns | revisits | plan ms | fill ms |\n")
    out.write("|---|---|---|---|---|---|---|---|---|---|---|\n")
    for r in rows:
        out.write("| {mask} | {combo} | {missing} | {stray} | {downs} | {lines} | "
                  "{order_lifts} | {order_turns} | {order_revisits} | {plan_ms} | {fill_ms} |\n".format(**r))
    bad = [r for r in rows if r["missing"]]
    out.write(f"\nКомбинаций с пропусками закраски: {len(bad)} (должно быть 0).\n")
    return out.getvalue()


if __name__ == "__main__":
    report = render(run())
    print(report)
    if "--save" in sys.argv:
        path = os.path.join(ROOT, "docs", "plans", "BASELINE_routes.md")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(report)
        print(f"saved: {path}")
