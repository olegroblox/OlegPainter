"""Atomic JSON documents with a validated previous version and explicit recovery.

Locks serialize writers in this process. Multiple application instances must use
different data directories; this module is not an inter-process database.
"""
from __future__ import annotations

import json
import logging
import math
import os
import tempfile
import threading
from pathlib import Path
from uuid import uuid4

log = logging.getLogger(__name__)
_lock = threading.RLock()


def backup_path(path: Path) -> Path:
    return path.with_name(path.name + ".last-good")


def atomic_bytes(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _invalid_constant(value):
    raise ValueError(f"Non-finite JSON number: {value}")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        _invalid_constant(value)
    return result


def decode_document(data: bytes) -> dict:
    result = json.loads(data.decode("utf-8"), parse_constant=_invalid_constant, parse_float=_finite_float)
    if not isinstance(result, dict):
        raise ValueError("Document must be a JSON object")
    return result


def read_document(path: Path) -> dict:
    path = Path(path)
    with _lock:
        try:
            return decode_document(path.read_bytes())
        except (FileNotFoundError, ValueError, UnicodeError, RecursionError) as original:
            try:
                saved = backup_path(path).read_bytes()
                result = decode_document(saved)
            except (OSError, ValueError, UnicodeError, RecursionError):
                raise original
            damaged = None
            if path.exists():
                damaged = path.with_name(path.name + ".corrupt-" + uuid4().hex)
                atomic_bytes(damaged, path.read_bytes())
            atomic_bytes(path, saved)
            log.warning("Recovered document %s from %s; damaged copy: %s", path, backup_path(path), damaged)
            return result


def write_document(path: Path, document: dict) -> None:
    if not isinstance(document, dict):
        raise ValueError("Document must be a JSON object")
    encoded = json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    path = Path(path)
    with _lock:
        if path.exists():
            # Recover (or fail visibly) before replacing a damaged document.
            read_document(path)
            atomic_bytes(backup_path(path), path.read_bytes())
        atomic_bytes(path, encoded)


def delete_document(path: Path) -> None:
    with _lock:
        # Remove backup first so a deliberately deleted document cannot reappear.
        backup_path(path).unlink(missing_ok=True)
        path.unlink(missing_ok=True)
