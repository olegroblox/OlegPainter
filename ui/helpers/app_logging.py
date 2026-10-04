from __future__ import annotations

import logging
import os
import sys
from datetime import datetime
from pathlib import Path

_CONFIGURED = False

# Keep at most this many per-session log files; older ones are pruned on start.
_MAX_SESSION_LOGS = 30

# Third-party loggers that are noisy at DEBUG and drown out our own logs.
_NOISY_LIBRARIES = (
    "PIL",
    "matplotlib",
    "urllib3",
    "onnxruntime",
    "numba",
    "h5py",
    "asyncio",
    "fontTools",
    "huggingface_hub",
    "filelock",
)


def _resolve_log_dir() -> Path | None:
    try:
        from app_paths import get_app_paths

        paths = get_app_paths()
        log_dir = paths.app_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        return log_dir
    except Exception:
        return None


def _session_log_path(log_dir: Path) -> Path:
    """A fresh, uniquely-named log file for this app launch."""
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    candidate = log_dir / f"session_{stamp}.log"
    suffix = 1
    while candidate.exists():
        candidate = log_dir / f"session_{stamp}_{suffix}.log"
        suffix += 1
    return candidate


def _prune_old_session_logs(log_dir: Path, keep: int = _MAX_SESSION_LOGS) -> None:
    try:
        sessions = sorted(
            log_dir.glob("session_*.log"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        for stale in sessions[keep:]:
            try:
                stale.unlink()
            except Exception:
                pass
    except Exception:
        pass


def configure_logging(level: int | None = None) -> logging.Logger:
    """Configure root logger once. Subsequent calls are no-ops."""
    global _CONFIGURED
    root = logging.getLogger()
    if _CONFIGURED:
        return root

    if level is None:
        level_name = os.environ.get("OLEGPAINTER_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, level_name, logging.INFO)
    root.setLevel(level)

    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    if not any(isinstance(h, logging.StreamHandler) for h in root.handlers):
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(fmt)
        root.addHandler(stream)

    headless = os.environ.get("QT_QPA_PLATFORM", "").strip().lower() in {"offscreen", "minimal"}
    log_dir = _resolve_log_dir()
    if log_dir is not None and not headless and not any(
        isinstance(h, logging.FileHandler) for h in root.handlers
    ):
        # One file per app launch so each session is self-contained and easy to
        # share. Old session files are pruned to keep the folder bounded.
        _prune_old_session_logs(log_dir)
        session_path = _session_log_path(log_dir)
        file_handler = logging.FileHandler(session_path, encoding="utf-8")
        file_handler.setFormatter(fmt)
        root.addHandler(file_handler)
        root.info("=== session log started: %s ===", session_path.name)
        global _SESSION_PATH
        _SESSION_PATH = session_path

    # Keep third-party chatter out of the way of our own logs.
    for noisy in _NOISY_LIBRARIES:
        logging.getLogger(noisy).setLevel(logging.WARNING)

    _CONFIGURED = True
    return root


_SESSION_PATH: Path | None = None


def session_log_path() -> Path | None:
    """The file of this launch, to name it in error messages for support."""
    return _SESSION_PATH


def get_logger(name: str) -> logging.Logger:
    if not _CONFIGURED:
        configure_logging()
    return logging.getLogger(name)
