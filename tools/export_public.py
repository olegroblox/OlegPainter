"""Collect the public copy of OlegPainter: only the files .gitignore allows, checked
for personal data. Publish this copy, never the working folder: GitHub's web upload
ignores .gitignore, and the working folder holds settings, logs, models and internal notes.

    python tools/export_public.py <empty or new folder>
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PRIVATE_DIRS = ("configs/", "logs/", "models/", "internal/", "test-results/", "dist/", "build/", "drivers/")
PRIVATE_NAMES = ("AGENTS.md",)
PRIVATE_SUFFIXES = (".xlsx", ".xls", ".onnx", ".log", ".exe", ".dll", ".pyc")
ALLOWED = {"models/manifest.json"}
TEXT_SUFFIXES = (".py", ".md", ".qml", ".json", ".txt", ".ps1", ".toml", ".ini", ".yml", ".yaml", ".spec", ".cfg")
MAX_BYTES = 5 * 1024 * 1024


def public_files() -> list[str]:
    """Every working-tree file git would add to a brand-new repository."""
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ, GIT_INDEX_FILE=str(Path(tmp) / "index"))
        result = subprocess.run(["git", "-c", "core.quotePath=false", "add", "-A", "--dry-run"],
                                cwd=ROOT, env=env, capture_output=True, encoding="utf-8", check=True)
    return sorted(line[5:-1] for line in result.stdout.splitlines() if line.startswith("add '") and line.endswith("'"))


def problems(files: list[str]) -> list[str]:
    found = []
    user = os.environ.get("USERNAME", "")
    personal_path = re.compile(r"[A-Za-z]:[\\/]+Users[\\/]+" + re.escape(user) + r"\b", re.IGNORECASE) if user else None
    for name in files:
        if name in ALLOWED:
            continue
        lowered = name.lower()
        if lowered.startswith(PRIVATE_DIRS) or Path(name).name in PRIVATE_NAMES or lowered.endswith(PRIVATE_SUFFIXES):
            found.append(f"личный или служебный файл: {name}")
            continue
        path = ROOT / name
        if path.stat().st_size > MAX_BYTES:
            found.append(f"слишком большой файл ({path.stat().st_size // 1024} КБ): {name}")
        if personal_path and lowered.endswith(TEXT_SUFFIXES):
            if personal_path.search(path.read_text(encoding="utf-8", errors="ignore")):
                found.append(f"путь с именем пользователя Windows: {name}")
    return found


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__)
        return 2
    target = Path(sys.argv[1]).resolve()
    if target == ROOT or ROOT in target.parents:
        print("Папка выгрузки должна быть вне проекта.")
        return 2
    if target.exists() and any(target.iterdir()):
        print(f"Папка не пустая: {target}")
        return 2
    files = public_files()
    found = problems(files)
    if found:
        print("Выгрузка остановлена:\n  " + "\n  ".join(found))
        return 1
    for name in files:
        destination = target / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / name, destination)
    size = sum((target / name).stat().st_size for name in files)
    print(f"Скопировано {len(files)} файлов ({size / 1024 / 1024:.1f} МБ) в {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
