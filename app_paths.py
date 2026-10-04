from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


def _is_writable_dir(path: Path) -> bool:
    """True only if we can actually create *and write into* ``path``."""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".op_write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return True
    except Exception:
        return False


def _writable_user_dir(app_root: Path, name: str) -> Path:
    """``app_root/name`` when writable, otherwise a per-user folder (read-only
    Program Files install). Downloaded AI models live here too (AI-001)."""
    primary = (app_root / name).resolve()
    if _is_writable_dir(primary):
        return primary
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    fallback = ((Path(base) if base else Path.home()) / "OlegPainter" / name).resolve()
    if _is_writable_dir(fallback):
        return fallback
    return primary


def _writable_configs_root(app_root: Path) -> Path:
    """Pick a writable configs dir.

    Normal case (app extracted to a writable folder — Desktop/Documents/etc.):
    keep configs next to the exe so a config is portable with the app. If the
    install dir is READ-ONLY (e.g. Program Files), fall back to a per-user
    location so the app still starts and can save — otherwise ConfigStore.mkdir
    would raise and crash MainWindow.__init__ on launch.
    """
    primary = (app_root / "configs").resolve()
    if _is_writable_dir(primary):
        return primary
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA")
    fallback_base = Path(base) if base else Path.home()
    fallback = (fallback_base / "OlegPainter" / "configs").resolve()
    if _is_writable_dir(fallback):
        return fallback
    return primary  # nothing writable — let the caller surface the error


@dataclass(frozen=True)
class AppPaths:
    app_root: Path
    resource_root: Path
    configs_root: Path
    assets_root: Path
    models_root: Path
    is_frozen: bool

    def resource_candidates(self, relative: str | Path) -> tuple[Path, ...]:
        candidate = Path(relative)
        roots = []
        for root in (self.resource_root, self.app_root):
            resolved = (root / candidate).resolve()
            if resolved not in roots:
                roots.append(resolved)
        return tuple(roots)


@lru_cache(maxsize=1)
def get_app_paths() -> AppPaths:
    if getattr(sys, "frozen", False):
        app_root = Path(sys.executable).resolve().parent
        resource_root = Path(getattr(sys, "_MEIPASS", app_root)).resolve()
        is_frozen = True
    else:
        app_root = Path(__file__).resolve().parent
        resource_root = app_root
        is_frozen = False

    # When frozen, the install dir may be read-only (Program Files); pick a
    # writable configs dir so saving a config never crashes. In dev the project
    # tree is always writable, so keep the exact legacy path (tests pin this).
    configs_root = _writable_configs_root(app_root) if is_frozen else (app_root / "configs").resolve()

    return AppPaths(
        app_root=app_root,
        resource_root=resource_root,
        configs_root=configs_root,
        assets_root=(resource_root / "assets").resolve(),
        models_root=_writable_user_dir(app_root, "models") if is_frozen else (app_root / "models").resolve(),
        is_frozen=is_frozen,
    )
