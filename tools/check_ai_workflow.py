"""End-to-end AI edits through the real PainterService (no input devices).

Usage: python tools/check_ai_workflow.py [image] [output-dir]
Uses a throwaway config folder; installed models are taken from models/.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
config_dir = tempfile.TemporaryDirectory(prefix="olegpainter-ai-check-")
os.environ["OLEGPAINTER_CONFIG_DIR"] = config_dir.name
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtWidgets import QApplication  # noqa: E402

app = QApplication.instance() or QApplication([])
from ui.services.painter_service import PainterService  # noqa: E402


def wait(service, predicate, seconds=60.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return False


def main() -> int:
    image = sys.argv[1] if len(sys.argv) > 1 else "tools/paint_bench/images/02_sponge.png"
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "test-results/ai-workflow")
    out.mkdir(parents=True, exist_ok=True)
    service = PainterService()
    ai = service.ai
    report = {"image": image, "steps": []}

    def step(name, kind=None, params=None, action=None):
        before = service._session_image_revision
        start = time.perf_counter()
        if action is not None:
            action()
        else:
            service.ai_run(kind, params or {})
        ok = wait(service, lambda: not ai.busy())
        app.processEvents()
        source = service.engine.source_pil_image
        entry = {"step": name, "seconds": round(time.perf_counter() - start, 2), "finished": ok,
                 "image_changed": service._session_image_revision != before,
                 "size": list(source.size) if source is not None else None,
                 "message": ai.snapshot()["message"], "error": ai.snapshot()["error"]}
        if source is not None:
            source.save(out / f"{len(report['steps']):02d}-{name}.png")
        report["steps"].append(entry)
        print(json.dumps(entry, ensure_ascii=False))

    assert service.open_image(image)
    ai.set_enabled(True)
    step("background", "background")
    step("restore", action=service.ai_restore_original)
    step("select", "select", {"x": 0.5, "y": 0.5, "positive": True})
    report["selection_pixels"] = int(ai.selection_mask.sum()) if ai.selection_mask is not None else 0
    preview = service.ai_selection_preview()
    if preview is not None:
        preview.save(str(out / "selection-preview.png"))
    step("keep_selection", "keep_selection")
    step("restore2", action=service.ai_restore_original)
    step("select2", "select", {"x": 0.5, "y": 0.5, "positive": True})
    step("erase_selection", "erase_selection")
    step("upscale", "upscale", {"scale": 2, "style": "anime"})
    step("lineart", "lineart")
    step("flatten", "flatten", {"strength": 2})
    step("restore3", action=service.ai_restore_original)
    ai.set_enabled(False)
    report["disabled_clears_sessions"] = not ai.pool._sessions
    report["prefs_file"] = str(ai.prefs_path)
    report["prefs_in_temp_dir"] = str(ai.prefs_path).startswith(config_dir.name)
    (out / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "steps"}, ensure_ascii=False))
    service.shutdown() if hasattr(service, "shutdown") else None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
