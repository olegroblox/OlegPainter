"""Read-only development checks; never capture input or start drawing."""
from __future__ import annotations

import importlib
import importlib.metadata
import os
from pathlib import Path
import platform
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    failures = []

    def report(ok: bool, label: str, detail: str = "") -> None:
        print(f"{'OK' if ok else 'FAIL'} {label}" + (f": {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    print(f"Project: {ROOT}\nPython: {sys.executable}")
    report(os.name == "nt", "Windows", platform.system())
    report(sys.version_info[:2] == (3, 13) and sys.maxsize > 2**32,
           "Python 3.13 x64", platform.python_version())
    report(sys.prefix != sys.base_prefix, "Isolated environment")

    for line in (ROOT / "requirements-dev.lock.txt").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, expected = line.split("==", 1)
        try:
            actual = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            actual = "missing"
        report(actual == expected, name, actual)

    for name in ("PySide6.QtWidgets", "PySide6.QtWebEngineWidgets", "numpy", "PIL.Image",
                 "cv2", "sklearn", "keyboard", "pynput", "interception", "win32api",
                 "onnxruntime", "numba", "windows_capture"):
        try:
            importlib.import_module(name)
            report(True, f"import {name}")
        except Exception as exc:
            report(False, f"import {name}", str(exc))

    for relative in ("assets", "ui/quick/qml/Main.qml", "ui/quick/i18n/en.json",
                     "models/manifest.json", "ui/OlegPainter Pro v1.3.ico"):
        report((ROOT / relative).exists(), f"resource {relative}")
    try:
        result = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=ROOT,
                                text=True, encoding="utf-8", capture_output=True, timeout=10)
        report(result.returncode == 0, "Git", (result.stdout or result.stderr).strip())
    except (OSError, subprocess.TimeoutExpired) as exc:
        report(False, "Git", str(exc))

    try:
        import onnxruntime
        providers = onnxruntime.get_available_providers()
        report(True, "onnxruntime " + onnxruntime.__version__, ", ".join(providers))
    except Exception as exc:
        report(False, "onnxruntime", str(exc))
    print("AI is opt-in on the AI page (AI-001); models are installed by the user, not shipped.")
    print("Interception driver and real drawing require a separate manual check.")
    print(f"Result: {len(failures)} failure(s).")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
