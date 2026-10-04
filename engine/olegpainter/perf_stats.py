"""perf_stats.py — featherweight phase timing for a drawing session.

A PerfStats instance accumulates wall-clock spans per named phase. It exists so
optimisation work is driven by measured numbers and so the telemetry session_end can report where the time actually went.
Overhead: two perf_counter() calls per span — safe inside any loop coarser than
a per-cell step. Never raises.
"""
from __future__ import annotations

import time
from contextlib import contextmanager


class PerfStats:
    def __init__(self):
        self._totals: dict[str, float] = {}
        self._counts: dict[str, int] = {}

    @contextmanager
    def span(self, name: str):
        start = time.perf_counter()
        try:
            yield
        finally:
            try:
                elapsed = time.perf_counter() - start
                self._totals[name] = self._totals.get(name, 0.0) + elapsed
                self._counts[name] = self._counts.get(name, 0) + 1
            except Exception:
                pass

    def add(self, name: str, seconds: float) -> None:
        try:
            self._totals[name] = self._totals.get(name, 0.0) + float(seconds)
            self._counts[name] = self._counts.get(name, 0) + 1
        except Exception:
            pass

    def snapshot(self) -> dict:
        return {
            name: {"total_s": round(total, 4), "count": self._counts.get(name, 0)}
            for name, total in sorted(self._totals.items())
        }


__all__ = ["PerfStats"]
