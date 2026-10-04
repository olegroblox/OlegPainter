"""Generate a current source inventory or locate symbols without importing the app."""
from __future__ import annotations

import ast
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def source_files() -> list[Path]:
    files = list(ROOT.glob("*.py"))
    for directory in ("engine", "ui", "tests", "tools"):
        files.extend((ROOT / directory).rglob("*.py"))
    return sorted(files)


def main() -> int:
    query = " ".join(sys.argv[1:]).casefold()
    sizes = []
    matches = []
    for path in source_files():
        relative = path.relative_to(ROOT).as_posix()
        source = path.read_text(encoding="utf-8-sig")
        tree = ast.parse(source, filename=relative)
        sizes.append((len(source.splitlines()), relative))
        if query:
            for node in ast.walk(tree):
                if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                    if query in node.name.casefold() or query in relative.casefold():
                        matches.append(f"{relative}:{node.lineno} {node.name}")
    if query:
        print("\n".join(matches) if matches else f"No source symbols match: {query}")
        return 0 if matches else 1

    print("LIVE SOURCE CONTEXT (generated now; no archived plans)")
    print(f"Root: {ROOT}")
    print(f"Python sources: {len(sizes)}")
    print(f"Test modules: {len(list((ROOT / 'tests').glob('test_*.py')))}")
    print("Largest modules:")
    for lines, relative in sorted(sizes, reverse=True)[:12]:
        print(f"  {lines:5} lines  {relative}")
    print("Entry point: main.py (Qt Quick window; quick_main.py is the same window)")
    print("Guidance: README.md -> docs/ARCHITECTURE.md -> docs/DEVELOPMENT.md")
    print("Symbols: dev.ps1 context PainterService")
    try:
        result = subprocess.run(["git", "status", "--short", "--branch"], cwd=ROOT,
                                capture_output=True, text=True, encoding="utf-8", timeout=10)
        print("Git:\n" + (result.stdout or result.stderr).strip())
        return result.returncode
    except (OSError, subprocess.TimeoutExpired) as exc:
        print(f"Git unavailable: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
