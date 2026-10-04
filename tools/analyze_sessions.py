# -*- coding: utf-8 -*-
"""analyze_sessions.py — сводка и A/B-сравнение сессий рисования по телеметрии.

Читает JSONL-журналы из %LOCALAPPDATA%\\OlegPainter\\telemetry (или --dir) и
печатает по каждой сессии: длительность, закрашенную долю, регионы, перелёты,
телепорты/мосты и активные флаги. Режим A/B — юзер делает два прогона одной
картинки с разными чекбоксами, скрипт сам говорит, какой быстрее:

    python tools/analyze_sessions.py                 # таблица последних сессий
    python tools/analyze_sessions.py --ab-last 2     # сравнить две последние
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.olegpainter.telemetry import default_telemetry_dir  # noqa: E402

FLAG_KEYS = (
    "run_length_merge_enabled", "astar_bridge_enabled", "fill_traversal_mode",
    "fill_route_polish_enabled", "snake_turn_minimize_enabled",
    "area_order_2opt_enabled", "area_order_or_opt_enabled",
    "area_entry_exit_routing_enabled", "euler_greedy_pairing_enabled",
)


def load_session(path: Path) -> dict | None:
    start = end = None
    components = 0
    colors = 0
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                ev = json.loads(line)
            except Exception:
                continue
            kind = ev.get("event")
            if kind == "session_start":
                start = ev
            elif kind == "session_end":
                end = ev
            elif kind == "component":
                components += 1
            elif kind == "color_start":
                colors += 1
    except Exception:
        return None
    if start is None:
        return None
    out = {
        "file": path.name,
        "flags": start.get("flags") or {},
        "grid": start.get("grid") or {},
        "colors": colors,
        "components": components,
        "duration_s": None,
        "drawn_share": None,
        "counters": {},
        "finished": end is not None,
    }
    if end is not None:
        out["duration_s"] = round(float(end.get("ts", 0)) - float(start.get("ts", 0)), 1)
        out["drawn_share"] = end.get("drawn_share")
        out["counters"] = end.get("counters") or {}
    return out


def fmt_flags(flags: dict) -> str:
    on = []
    for key in FLAG_KEYS:
        val = flags.get(key)
        if val in (True,):
            on.append(key.replace("_enabled", ""))
        elif isinstance(val, str) and val not in ("auto", "", None):
            on.append(f"{key.split('_')[0]}={val}")
    return ",".join(on) or "-"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=None, help="каталог телеметрии (по умолчанию %%LOCALAPPDATA%%)")
    ap.add_argument("--last", type=int, default=10, help="сколько последних сессий показать")
    ap.add_argument("--ab-last", type=int, default=0, metavar="N",
                    help="сравнить N последних завершённых сессий между собой (обычно 2)")
    args = ap.parse_args()

    base = Path(args.dir) if args.dir else default_telemetry_dir()
    files = sorted(base.glob("session_*.jsonl")) if base.exists() else []
    sessions = [s for s in (load_session(p) for p in files) if s]
    if not sessions:
        print(f"Нет сессий в {base}")
        return

    show = sessions[-max(1, args.last):]
    print(f"{'файл':<32} {'сек':>7} {'доля':>5} {'цв':>3} {'рег':>5} {'перелёт px':>10} {'телепорт':>8} {'флаги'}")
    for s in show:
        c = s["counters"]
        print(f"{s['file']:<32} {s['duration_s'] if s['duration_s'] is not None else '—':>7} "
              f"{s['drawn_share'] if s['drawn_share'] is not None else '—':>5} "
              f"{s['colors']:>3} {s['components']:>5} "
              f"{c.get('region_travel_px', '—'):>10} {c.get('teleports', '—'):>8} {fmt_flags(s['flags'])}")

    if args.ab_last:
        done = [s for s in sessions if s["finished"] and s["duration_s"]]
        pick = done[-args.ab_last:]
        if len(pick) < 2:
            print("\nДля A/B нужно >=2 завершённых сессий.")
            return
        print("\n=== A/B: последние завершённые ===")
        base_s = pick[0]
        for s in pick[1:]:
            dd = s["duration_s"] - base_s["duration_s"]
            pct = 100.0 * dd / base_s["duration_s"] if base_s["duration_s"] else 0.0
            verdict = "БЫСТРЕЕ" if dd < 0 else "медленнее"
            print(f"{s['file']} vs {base_s['file']}: {s['duration_s']}s vs {base_s['duration_s']}s "
                  f"({dd:+.1f}s, {pct:+.1f}%) — {verdict}")
            t1 = (s["counters"] or {}).get("region_travel_px")
            t0 = (base_s["counters"] or {}).get("region_travel_px")
            if t1 is not None and t0:
                print(f"  перелёты между областями: {t1} vs {t0} px ({100.0 * (t1 - t0) / t0:+.1f}%)")
            print(f"  флаги A: {fmt_flags(base_s['flags'])}")
            print(f"  флаги B: {fmt_flags(s['flags'])}")


if __name__ == "__main__":
    main()
