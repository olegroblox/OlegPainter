# -*- coding: utf-8 -*-
"""route_polish.py — LS-MCPP-lite post-pass over an already built cell visit
order (docs/plans/WS1_fermat_routes.md этап «дожиматель»).

The order is split into RUNS (maximal chains of 8-adjacent steps — exactly the
pieces the pen draws without lifting), and the runs are re-chained greedily by
nearest endpoint with free reversal. Re-chaining can only move WHERE the pen
lifts, never WHAT it paints: the multiset of cells (and the very first cell —
the caller already positioned the pen there) is preserved. A final guard keeps
the original order unless the polish strictly improves (lifts, lift travel,
draw length) — so the flag can never make a fill worse, only cheaper.
"""
from __future__ import annotations

import time

import numpy as np

from .telemetry import route_metrics


def split_runs(order):
    """Maximal chains of Chebyshev<=1 steps — the pen-down strokes."""
    runs = []
    cur = [order[0]]
    for a, b in zip(order, order[1:]):
        if max(abs(a[0] - b[0]), abs(a[1] - b[1])) <= 1:
            cur.append(b)
        else:
            runs.append(cur)
            cur = [b]
    runs.append(cur)
    return runs


def polish_cell_order(order, *, time_budget_s: float = 0.1, max_runs: int = 4000):
    """Re-chain the lift-separated runs of `order` (nearest endpoint, free
    reversal) and return the better of the two orders. Never worse, same cells,
    same first cell."""
    n = len(order)
    if n < 3:
        return order
    t0 = time.perf_counter()
    runs = split_runs(order)
    if len(runs) <= 2 or len(runs) > max_runs:
        return order

    heads = np.array([r[0] for r in runs], dtype=np.float64)    # (R, 2)
    tails = np.array([r[-1] for r in runs], dtype=np.float64)

    used = np.zeros(len(runs), dtype=bool)
    used[0] = True
    chain = [(0, False)]
    cur = np.asarray(runs[0][-1], dtype=np.float64)
    for _ in range(len(runs) - 1):
        if (time.perf_counter() - t0) > time_budget_s:
            return order  # over budget: keep the proven original
        d_head = ((heads - cur) ** 2).sum(axis=1)
        d_tail = ((tails - cur) ** 2).sum(axis=1)
        d_head[used] = np.inf
        d_tail[used] = np.inf
        i_head = int(np.argmin(d_head))
        i_tail = int(np.argmin(d_tail))
        if d_head[i_head] <= d_tail[i_tail]:
            idx, reverse = i_head, False
            cur = tails[idx]
        else:
            idx, reverse = i_tail, True
            cur = heads[idx]
        used[idx] = True
        chain.append((idx, reverse))

    polished = []
    for idx, reverse in chain:
        polished.extend(reversed(runs[idx]) if reverse else runs[idx])

    before = route_metrics(order)
    after = route_metrics(polished)
    key_before = (before["lifts"], before["lift_travel"], before["draw_len"])
    key_after = (after["lifts"], after["lift_travel"], after["draw_len"])
    return polished if key_after < key_before else order


__all__ = ["polish_cell_order", "split_runs"]
