"""telemetry.py — session metrics: the single source of truth.

Two responsibilities, both with a hard rule — telemetry may NEVER break or slow
drawing (every public method swallows its own exceptions; events are buffered):

* route_metrics(cells): canonical definitions of "draw length", "lift", "turn"
  and "revisit" over an ordered cell route. Every workstream (acceptance
  criteria, benches, logs, ETA) imports THIS function instead of redefining the
  metrics — see docs/plans/REVIEW_architect.md §3.7.

* TelemetrySession: buffered JSONL writer for one drawing session. Files live
  outside the (OneDrive-synced) project tree by default — %LOCALAPPDATA% — and
  are pruned to a budget so a long-lived install never grows unbounded.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path

import numpy as np

# Rotation budget for the telemetry directory.
MAX_FILES = 100
MAX_TOTAL_BYTES = 50 * 1024 * 1024
_FLUSH_EVERY = 16  # events buffered between disk writes


def default_telemetry_dir() -> Path:
    override = os.environ.get("OLEGPAINTER_TELEMETRY_DIR")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") or str(Path.home())
    return Path(base) / "OlegPainter" / "telemetry"


def route_metrics(cells) -> dict:
    """Canonical metrics of an ordered (row, col) cell route.

    draw_len     — euclidean length of pen-down motion (adjacent steps only)
    lifts        — number of non-adjacent consecutive pairs (Chebyshev > 1):
                   the pen must teleport/bridge there
    lift_travel  — euclidean length flown across those lifts
    turns        — direction changes between consecutive ADJACENT steps
    revisits     — cells visited more than once (overdraw)
    """
    n = len(cells)
    empty = {"cells": 0, "unique_cells": 0, "revisits": 0, "draw_len": 0.0,
             "lifts": 0, "lift_travel": 0.0, "turns": 0}
    if n == 0:
        return empty
    a = np.asarray(cells, dtype=np.int64).reshape(n, 2)
    unique = int(np.unique(a, axis=0).shape[0])
    if n == 1:
        return {**empty, "cells": 1, "unique_cells": 1}
    d = np.diff(a, axis=0)
    cheb = np.maximum(np.abs(d[:, 0]), np.abs(d[:, 1]))
    adjacent = cheb <= 1
    eucl = np.hypot(d[:, 0], d[:, 1])
    sign = np.sign(d)
    adj_pair = adjacent[:-1] & adjacent[1:]
    dir_change = (sign[:-1] != sign[1:]).any(axis=1)
    return {
        "cells": int(n),
        "unique_cells": unique,
        "revisits": int(n - unique),
        "draw_len": float(eucl[adjacent].sum()),
        "lifts": int((~adjacent).sum()),
        "lift_travel": float(eucl[~adjacent].sum()),
        "turns": int((adj_pair & dir_change).sum()),
    }


def _json_default(value):
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        return float(value)
    if isinstance(value, np.ndarray):
        return value.tolist()
    return str(value)


class TelemetrySession:
    """Buffered JSONL event log for one drawing session.

    Usage: emit("session_start", ...); emit("component", ...); close(...).
    Disabled or broken telemetry degrades to no-ops — drawing never notices.
    """

    def __init__(self, enabled: bool = True, base_dir: Path | None = None):
        self._lock = threading.Lock()
        self._buffer: list[str] = []
        self._path: Path | None = None
        self._closed = False
        self.enabled = bool(enabled)
        if not self.enabled:
            return
        try:
            base = Path(base_dir) if base_dir is not None else default_telemetry_dir()
            base.mkdir(parents=True, exist_ok=True)
            self._prune(base)
            stamp = time.strftime("%Y%m%d_%H%M%S")
            path = base / f"session_{stamp}.jsonl"
            # Two sessions within one second: suffix instead of clobbering.
            counter = 1
            while path.exists():
                counter += 1
                path = base / f"session_{stamp}_{counter}.jsonl"
            self._path = path
        except Exception:
            self.enabled = False

    @staticmethod
    def _prune(base: Path) -> None:
        try:
            files = sorted(base.glob("session_*.jsonl"), key=lambda p: p.stat().st_mtime)
            total = sum(p.stat().st_size for p in files)
            while files and (len(files) >= MAX_FILES or total > MAX_TOTAL_BYTES):
                victim = files.pop(0)
                total -= victim.stat().st_size
                victim.unlink()
        except Exception:
            pass

    @property
    def path(self) -> Path | None:
        return self._path

    def emit(self, event: str, **fields) -> None:
        if not self.enabled or self._closed:
            return
        try:
            record = {"ts": round(time.time(), 3), "event": str(event)}
            record.update(fields)
            line = json.dumps(record, ensure_ascii=False, default=_json_default)
            with self._lock:
                self._buffer.append(line)
                if len(self._buffer) >= _FLUSH_EVERY:
                    self._flush_locked()
        except Exception:
            pass

    def _flush_locked(self) -> None:
        if not self._buffer or self._path is None:
            self._buffer.clear()
            return
        chunk = "\n".join(self._buffer) + "\n"
        self._buffer.clear()
        with open(self._path, "a", encoding="utf-8") as fh:
            fh.write(chunk)

    def flush(self) -> None:
        if not self.enabled:
            return
        try:
            with self._lock:
                self._flush_locked()
        except Exception:
            pass

    def close(self, **fields) -> None:
        if not self.enabled or self._closed:
            return
        try:
            if fields:
                self.emit("session_end", **fields)
            self.flush()
        except Exception:
            pass
        self._closed = True


__all__ = ["route_metrics", "TelemetrySession", "default_telemetry_dir"]
