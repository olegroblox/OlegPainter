"""Pack the built program into the archive a release carries (UPDATE-001).

    python tools/package_release.py            # dist/OlegPainter → dist/OlegPainter-<version>-win64.zip

The program finds an update by this name on the latest GitHub release, so keep it:
the archive holds one folder «OlegPainter» with the exe, `_internal` and the licences.
Personal folders a test run left in dist (configs, logs, models) never go in.
"""
from __future__ import annotations

import hashlib
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from application.support import APP_VERSION  # noqa: E402
from application.updates import EXE, USER_DATA  # noqa: E402

EXTRA = ("README.md", "LICENSE", "THIRD_PARTY_NOTICES.md")


def archive_name(version: str = APP_VERSION) -> str:
    return f"OlegPainter-{version}-win64.zip"


def package(build: Path, output: Path) -> Path:
    if not (build / EXE).is_file() or not (build / "_internal").is_dir():
        raise SystemExit(f"Нет собранной программы в {build}: сначала dev.ps1 build.")
    files = [path for path in sorted(build.rglob("*"))
             if path.is_file() and path.relative_to(build).parts[0] not in USER_DATA]
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_name(output.name + ".part")
    with zipfile.ZipFile(partial, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as bundle:
        for path in files:
            bundle.write(path, Path("OlegPainter") / path.relative_to(build))
        for name in EXTRA:
            if (ROOT / name).is_file():
                bundle.write(ROOT / name, Path("OlegPainter") / name)
    partial.replace(output)
    return output


def main() -> int:
    output = package(ROOT / "dist" / "OlegPainter", ROOT / "dist" / archive_name())
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    print(f"{output}  {output.stat().st_size // 2**20} МБ\nSHA-256 {digest}")
    print(f"Релиз на GitHub: тег v{APP_VERSION}, приложите этот архив, описание — «что нового».")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
