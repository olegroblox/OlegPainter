"""Headless session save timings with real PNG/atomic writes and isolated data."""
import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time

parser = argparse.ArgumentParser()
parser.add_argument("--output", default="test-results/session-save-async.json")
args = parser.parse_args()
os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image
import numpy as np
from PySide6.QtCore import QSettings, QTimer
from PySide6.QtWidgets import QApplication
from ui.services.painter_service import PainterService
from ui.helpers.config_store import ConfigStore
from application.controller import ApplicationController

root = tempfile.TemporaryDirectory(prefix="olegpainter-session-profile-")
os.environ["OLEGPAINTER_CONFIG_DIR"] = root.name
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, root.name)
app = QApplication([])
app.setQuitOnLastWindowClosed(False)
service = PainterService()
controller = ApplicationController(service, config_store=ConfigStore(Path(root.name)))
service.engine.source_pil_image = Image.fromarray(
    np.random.default_rng(421).integers(0, 256, (2048, 2048, 4), dtype=np.uint8))
service._set_session_image_source("clipboard_cache")
results, gaps, errors = [], [], []
last = time.perf_counter()


def heartbeat():
    global last
    now = time.perf_counter()
    gaps.append(now - last)
    last = now


def save():
    started = time.perf_counter()
    future = controller.session.request_save(force=True)
    request_seconds = time.perf_counter() - started
    def completed(result):
        try:
            result.result()
        except Exception as error:
            errors.append(str(error))
        results.append(dict(request_seconds=request_seconds,
                            complete_seconds=time.perf_counter() - started))
        QTimer.singleShot(100, save if len(results) == 1 else app.quit)
    future.add_done_callback(completed)


timer = QTimer()
timer.timeout.connect(heartbeat)
timer.start(20)
QTimer.singleShot(100, save)
try:
    app.exec()
finally:
    timer.stop()
    controller.close(save=False)
    root.cleanup()
report = dict(image_size=[2048, 2048], mode="RGBA seeded noise", saves=results,
              timer_period_ms=20, max_heartbeat_gap_seconds=max(gaps), errors=errors)
output = Path(args.output)
output.parent.mkdir(parents=True, exist_ok=True)
output.write_text(json.dumps(report, indent=2), encoding="utf-8")
print(json.dumps(report), flush=True)
raise SystemExit(bool(errors))
