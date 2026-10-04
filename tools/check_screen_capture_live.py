"""Opt-in native Windows capture check; creates only its own temporary windows.

Run with --live after notifying the user. No mouse/keyboard hooks or drawing.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import threading
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, default=Path("test-results/screen-capture-live"))
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    os.environ["OLEGPAINTER_CONFIG_DIR"] = str((args.output / "configs").resolve())

    from PIL import ImageGrab
    from PySide6.QtCore import QPoint, QTimer, Qt
    from PySide6.QtWidgets import QApplication, QWidget
    from ui.overlays.capture_guide import CaptureGuideOverlay
    from infrastructure.screen_capture import screen_capture
    from engine.olegpainter.core import OlegPainter

    app = QApplication([])
    if os.name != "nt" or app.platformName() != "windows":
        raise RuntimeError("This check requires an unlocked native Windows desktop")
    background = QWidget(None, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
    background.setWindowTitle("OlegPainter capture verification")
    background.setStyleSheet("background: #000000")
    origin = app.primaryScreen().availableGeometry().topLeft() + QPoint(160, 160)
    background.setGeometry(origin.x(), origin.y(), 400, 300)
    background.show()
    guide = CaptureGuideOverlay()
    guide.set_state("Screen capture verification", 0)
    engine = OlegPainter(status_callback=lambda *_: None)
    point = origin + QPoint(80, 80)
    results = []
    worker = None
    stages = iter(("passive", "interactive", "hidden", "shown again"))

    def measure(label, x, y):
        bbox = (x, y, x + 1, y + 1)
        try:
            before = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB").getpixel((0, 0))
            start = time.perf_counter()
            clean = engine._sample_screen_rgb(x, y)
            elapsed = 1000 * (time.perf_counter() - start)
            after = ImageGrab.grab(bbox=bbox, all_screens=True).convert("RGB").getpixel((0, 0))
            results.append(dict(label=label, before=before, clean=clean, after=after, capture_ms=elapsed))
        except Exception as error:
            results.append(dict(label=label, error=str(error)))

    def poll():
        if worker.is_alive():
            QTimer.singleShot(20, poll)
        else:
            advance()

    def start_measure(label):
        nonlocal worker
        x, y = guide.coordinates.to_physical(point.x(), point.y())
        worker = threading.Thread(target=measure, args=(label, x, y))
        worker.start()
        QTimer.singleShot(20, poll)

    def advance():
        label = next(stages, None)
        if label is None:
            app.quit()
            return
        if label == "hidden":
            guide.hide()
        else:
            if label == "interactive":
                guide.set_passthrough(False)
            guide.show()
            guide._anim.stop()
            guide._cursor = QPoint(point)
            guide.update()
        QTimer.singleShot(500, lambda: start_measure(label))

    QTimer.singleShot(0, advance)
    QTimer.singleShot(15000, app.quit)
    try:
        app.exec()
    finally:
        if worker is not None:
            worker.join()
        guide.close()
        background.close()
        (args.output / "report.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    assert len(results) == 4, results
    for result in results:
        assert result.get("clean") == (0, 0, 0), result
        assert result["after"] == result["before"], result
        if result["label"] != "hidden":
            assert result["before"] != result["clean"], result
    assert not screen_capture._windows, "Closed tools stayed registered"
    print(json.dumps(results, indent=2))


if __name__ == "__main__":
    main()
