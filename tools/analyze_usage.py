# -*- coding: utf-8 -*-
"""Usage analyzer for OlegPainter — turns the verbose interaction logs into a UX report.

Parses ``logs/session_*.log`` (the per-session logs written by
``ui/helpers/interaction_logger.py``) and produces, per session and aggregated:

  * Action frequencies (which buttons / combos / sliders / hotkeys / nav / dialogs).
  * Time-to-first-draw funnel (app open -> image -> area -> first start -> completed)
    with the wall-clock gap of each milestone, so we can see where setup time goes.
  * Friction signals: errors hit, dialogs opened-then-closed, repeated calibrations,
    long idle gaps before an action (hunting for a control).
  * A compressed timeline of milestone events.

No app changes required — it reads logs that already exist. Run:

    python tools/analyze_usage.py                 # all logs/session_*.log
    python tools/analyze_usage.py logs/session_X.log [more ...]
    python tools/analyze_usage.py --json report.json
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
from collections import Counter, defaultdict

LINE_RE = re.compile(r"^(\d{2}):(\d{2}):(\d{2})\s+\[(\w+)\]\s+([\w.]+):\s+(.*)$")

# Milestone matchers (substring / regex against the message). Order = funnel order.
MILESTONES = [
    ("image_loaded",   re.compile(r"Изображение успешно установлено|SIGNAL configLoaded.*IMAGE_PATH': '[^']")),
    ("area_selected",  re.compile(r"SIGNAL area: |Area selected|Область выбрана")),
    ("hex_capture",    re.compile(r"capture.*hex|Капля|HEX|start_hex", re.IGNORECASE)),
    ("calibration",    re.compile(r"WINDOW shown:.*(Calibr|калибр|selectROI|Select area)", re.IGNORECASE)),
    ("stencil_shown",  re.compile(r"WINDOW shown:.*Kalka|toggle_stencil")),
    ("draw_started",   re.compile(r"SIGNAL drawingState: started|Рисование запущено")),
    ("draw_completed", re.compile(r"SIGNAL drawingState: completed|drawing_complete")),
]

ACTION_PREFIXES = ("BUTTON ", "COMBO ", "SLIDER ", "NUMBER ", "SPIN ", "SEGMENT ",
                   "INPUT ", "GROUP ", "NAV ", "KEY ", "ACTION ", "THEME ", "LANGUAGE ")


def _secs(h, m, s):
    return int(h) * 3600 + int(m) * 60 + int(s)


def _norm_action(kind: str, body: str) -> str:
    """Stable id for an action: strip volatile bits (checked=, values, indices)."""
    body = body.split(" -> ")[0].strip()
    body = re.sub(r"\s+", " ", body)
    return f"{kind.strip()}::{body}"[:90]


def parse_log(path: str) -> dict:
    actions = Counter()
    by_kind = Counter()
    windows_open = Counter()
    errors = []
    nav_visits = Counter()
    keys = Counter()
    events = []           # (secs, kind, body)
    milestones = {}       # name -> secs (first occurrence)
    open_close = defaultdict(list)
    t0 = None
    prev_secs = None
    idle_gaps = []        # (gap_secs, kind, body) — long pauses before an action

    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()
    except Exception as exc:
        return {"path": path, "error": str(exc)}

    for ln in lines:
        m = LINE_RE.match(ln.rstrip("\n"))
        if not m:
            continue
        h, mi, s, level, logger, msg = m.groups()
        t = _secs(h, mi, s)
        if t0 is None:
            t0 = t
        # account for midnight wrap in a single session
        if prev_secs is not None and t < prev_secs - 3600:
            t += 86400
        prev_secs = t

        # milestones
        for name, rx in MILESTONES:
            if name not in milestones and rx.search(msg):
                milestones[name] = t

        # errors / friction
        if "error:" in msg.lower() and logger.endswith("signals"):
            errors.append((t - t0, msg[:120]))

        # actions
        for pref in ACTION_PREFIXES:
            if msg.startswith(pref):
                kind = pref.strip()
                body = msg[len(pref):]
                aid = _norm_action(kind, body)
                actions[aid] += 1
                by_kind[kind] += 1
                if kind == "NAV":
                    nav_visits[body.strip()] += 1
                if kind == "KEY":
                    keys[body.split(" on ")[0].strip()] += 1
                events.append((t, kind, body[:70]))
                break
        else:
            if msg.startswith("WINDOW shown: "):
                w = msg[len("WINDOW shown: "):].strip()
                windows_open[w] += 1
                open_close[w].append(("open", t))
                events.append((t, "WINDOW_OPEN", w[:70]))
            elif msg.startswith("WINDOW closed: "):
                w = msg[len("WINDOW closed: "):].strip()
                open_close[w].append(("close", t))
                events.append((t, "WINDOW_CLOSE", w[:70]))

    # idle gaps between consecutive user actions (> 4s = likely hunting/thinking)
    user_events = [(t, k, b) for (t, k, b) in events if k in
                   ("BUTTON", "COMBO", "SLIDER", "NUMBER", "SPIN", "SEGMENT", "KEY", "NAV", "WINDOW_OPEN")]
    for i in range(1, len(user_events)):
        gap = user_events[i][0] - user_events[i - 1][0]
        if gap >= 4:
            idle_gaps.append((gap, user_events[i][1], user_events[i][2]))

    # dialogs opened then closed quickly (< 3s) with nothing applied = friction
    quick_dismiss = []
    for w, evs in open_close.items():
        evs.sort(key=lambda x: x[1])
        for i in range(len(evs) - 1):
            if evs[i][0] == "open" and evs[i + 1][0] == "close":
                d = evs[i + 1][1] - evs[i][1]
                if d <= 3:
                    quick_dismiss.append((w, d))

    duration = (prev_secs - t0) if (prev_secs is not None and t0 is not None) else 0
    funnel = {name: (milestones[name] - t0) for name in milestones} if t0 is not None else {}

    return {
        "path": os.path.basename(path),
        "duration_s": duration,
        "n_user_actions": sum(by_kind.values()),
        "by_kind": dict(by_kind),
        "top_actions": actions.most_common(25),
        "nav_visits": dict(nav_visits),
        "top_keys": keys.most_common(15),
        "windows": dict(windows_open),
        "funnel_s": funnel,
        "errors": errors,
        "idle_gaps": sorted(idle_gaps, reverse=True)[:15],
        "quick_dismiss": quick_dismiss,
    }


def _fmt_t(s):
    if s is None:
        return "—"
    m, sec = divmod(int(s), 60)
    return f"{m}:{sec:02d}"


def print_report(reports: list) -> None:
    agg_actions = Counter()
    agg_kind = Counter()
    agg_keys = Counter()
    agg_windows = Counter()
    agg_nav = Counter()
    total_errors = 0

    for r in reports:
        if r.get("error"):
            print(f"  ! {r['path']}: {r['error']}")
            continue
        print("=" * 78)
        print(f"SESSION {r['path']}   duration {_fmt_t(r['duration_s'])}   "
              f"user-actions {r['n_user_actions']}")
        f = r["funnel_s"]
        print("  FUNNEL (time from app open):")
        for name, _rx in MILESTONES:
            print(f"     {name:<16} {_fmt_t(f.get(name))}")
        if r["errors"]:
            print(f"  ERRORS hit: {len(r['errors'])}")
            for dt, msg in r["errors"][:6]:
                print(f"     +{_fmt_t(dt)}  {msg}")
        if r["idle_gaps"]:
            print("  LONG PAUSES before an action (hunting?):")
            for gap, kind, body in r["idle_gaps"][:8]:
                print(f"     {gap:>4}s  before {kind} {body}")
        if r["quick_dismiss"]:
            print("  DIALOGS opened then closed <3s (friction):")
            for w, d in r["quick_dismiss"][:8]:
                print(f"     {d}s  {w}")
        print("  TOP ACTIONS:")
        for aid, n in r["top_actions"][:15]:
            print(f"     {n:>4}x  {aid}")
        for k, v in r["by_kind"].items():
            agg_kind[k] += v
        for aid, n in r["top_actions"]:
            agg_actions[aid] += n
        for k, n in r["top_keys"]:
            agg_keys[k] += n
        for w, n in r["windows"].items():
            agg_windows[w] += n
        for nv, n in r["nav_visits"].items():
            agg_nav[nv] += n
        total_errors += len(r["errors"])

    print("\n" + "#" * 78)
    print(f"AGGREGATE over {len([r for r in reports if not r.get('error')])} session(s)")
    print(f"  total errors hit: {total_errors}")
    print("  actions by kind:", dict(agg_kind))
    print("  TOP 20 actions overall (candidates for quick access):")
    for aid, n in agg_actions.most_common(20):
        print(f"     {n:>4}x  {aid}")
    print("  TOP keys:", agg_keys.most_common(10))
    print("  page visits (NAV):", dict(agg_nav))
    print("  dialogs opened:", dict(agg_windows))


def main(argv):
    args = [a for a in argv if not a.startswith("--")]
    json_out = None
    if "--json" in argv:
        i = argv.index("--json")
        if i + 1 < len(argv):
            json_out = argv[i + 1]
            args = [a for a in args if a != json_out]
    here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    paths = args or sorted(glob.glob(os.path.join(here, "logs", "session_*.log")))
    if not paths:
        print("No logs found. Pass log paths or put session_*.log under logs/.")
        return 1
    reports = [parse_log(p) for p in paths]
    print_report(reports)
    if json_out:
        with open(json_out, "w", encoding="utf-8") as f:
            json.dump(reports, f, ensure_ascii=False, indent=2)
        print(f"\nJSON written: {json_out}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
