"""Opt-in real calibration against a separate, measured native paint surface.

The target receives actual mouse/keyboard input and saves the resulting canvas.
No calibration measurements are mocked. This does not replace a Paint run.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--live", required=True, action="store_true")
parser.add_argument("--target", action="store_true")
parser.add_argument("--output", required=True)
parser.add_argument("--button-delay", type=float, default=.03,
                    help="Real mouse button settle setting, 0–10 seconds; use a longer delay to inspect cancellation")
args = parser.parse_args()
if not 0 <= args.button_delay <= 10:
    parser.error("--button-delay must be between 0 and 10 seconds")
output = Path(args.output).resolve()
output.mkdir(parents=True, exist_ok=True)
os.environ["OLEGPAINTER_CONFIG_DIR"] = str(output / "configs")
os.environ.pop("QT_QPA_PLATFORM", None)
os.environ.pop("QT_QUICK_BACKEND", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings, QTimer, Qt, QPoint, QRectF
from PySide6.QtGui import QImage, QPainter, QColor
from PySide6.QtWidgets import QApplication, QWidget, QVBoxLayout, QLabel, QDoubleSpinBox
from PySide6.QtQuickControls2 import QQuickStyle
from ui.helpers.app_setup import _configure_high_dpi, _apply_app_font

_configure_high_dpi()
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(output / "settings"))
QQuickStyle.setStyle("Basic")
app = QApplication([])
_apply_app_font(app)


def write(name, value):
    path = output / name
    temporary = path.with_suffix(".pending")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(path)


if args.target:
    class Canvas(QWidget):
        def __init__(self, size_field):
            super().__init__()
            self.size_field = size_field
            self.setMinimumSize(500, 550)
            self.image = QImage(900, 900, QImage.Format_RGB32)
            self.image.fill(Qt.white)
            self.stamps = []

        def paintEvent(self, event):
            painter = QPainter(self)
            painter.drawImage(0, 0, self.image)

        def mousePressEvent(self, event):
            if event.button() != Qt.LeftButton:
                return
            point = event.position()
            radius = self.size_field.value()
            painter = QPainter(self.image)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QColor("#000000"))
            painter.drawEllipse(QRectF(point.x()-radius, point.y()-radius, radius*2, radius*2))
            painter.end()
            self.stamps.append(dict(x=point.x(), y=point.y(), radius=radius, at=time.monotonic()))
            self.image.save(str(output / "canvas.png"))
            write("stamps.json", self.stamps)
            self.update()

    window = QWidget()
    window.setWindowTitle("OlegPainter — тестовый холст кисти")
    layout = QVBoxLayout(window)
    layout.addWidget(QLabel("Тестовая поверхность • радиус кисти"))
    field = QDoubleSpinBox()
    field.setRange(1, 40)
    field.setValue(2)
    field.setDecimals(1)
    layout.addWidget(field)
    canvas = Canvas(field)
    layout.addWidget(canvas)
    window.resize(680, 800)
    window.move(1180, 60)
    window.show()

    def publish_target():
        from ui.overlays.coordinates import DesktopCoordinates
        coords = DesktopCoordinates()
        origin = canvas.mapToGlobal(QPoint(0, 0))
        corner = canvas.mapToGlobal(QPoint(canvas.width(), canvas.height()))
        control = field.mapToGlobal(QPoint(60, field.height() // 2))
        x, y = coords.to_physical(origin.x(), origin.y())
        right, bottom = coords.to_physical(corner.x(), corner.y())
        write("target.json", dict(pid=os.getpid(), coord=coords.to_physical(control.x(), control.y()),
                                  scratch=[x+35, y+35, right-x-70, bottom-y-70],
                                  region=[x, y, 100, 100]))

    QTimer.singleShot(500, publish_target)
    exit_timer = QTimer()
    exit_timer.timeout.connect(lambda: window.close() if (output / "close-target").exists() else None)
    exit_timer.start(200)
    QTimer.singleShot(600_000, window.close)
    app.exec()
    sys.exit(0)

if (output / "target.json").exists():
    raise RuntimeError("Use a fresh output directory for each live run")
target = subprocess.Popen([sys.executable, __file__, "--live", "--target", "--output", str(output)],
                          creationflags=subprocess.CREATE_NO_WINDOW)
try:
    deadline = time.monotonic() + 10
    while not (output / "target.json").exists():
        if target.poll() is not None or time.monotonic() > deadline:
            raise RuntimeError("Target did not expose its native canvas")
        time.sleep(.05)
    config = json.loads((output / "target.json").read_text(encoding="utf-8"))
    from ui.quick.application import QuickApplication
    quick = QuickApplication(desktop=True, initialize_runtime=True)
    quick.window.setTitle("OlegPainter — живое обучение кисти")
    quick.window.resize(1100, 850)
    quick.window.setPosition(30, 40)
    engine = quick.service.engine
    quick.service.set_pen_button_delay(args.button_delay)
    engine.draw_region = tuple(config["region"])
    engine.dynamic_brush_coord = tuple(config["coord"])
    engine.dynamic_brush_scratch_zone = tuple(config["scratch"])
    engine.brush_size = 1
    engine.color_picking_method = "manual_palette"
    engine.manual_palette_coords = []  # target already paints black
    quick.presenter.setBrush(dict(control_mode="text", min_value=2., max_value=18., default_value=2., step_value=1.))
    quick.presenter.open_brush_setup()
    states, gaps = [], []
    last = time.monotonic()

    def snapshot():
        global last
        now = time.monotonic()
        gaps.append(now-last)
        last = now
        write("state.json", dict(learning=quick.service.brush_learning_snapshot(),
                                 profile=engine.dynamic_brush_profile, page=quick.window.property("page")))

    def changed():
        state = quick.service.brush_learning_snapshot()
        states.append(dict(at=time.monotonic(), **state))
        if not state["active"]:
            QTimer.singleShot(150, lambda: quick.window.grabWindow().save(str(output / "result.png")))

    quick.service.brushLearningChanged.connect(changed)
    heartbeat = QTimer()
    heartbeat.timeout.connect(snapshot)
    heartbeat.start(100)
    QTimer.singleShot(600_000, quick.window.close)
    exit_code = app.exec()
    quick.dispose(save=False)
    from infrastructure.input_ownership import automation_input
    input_idle = automation_input.current is None
    passed = (exit_code == 0 and quick.controller._closed and not quick.qml_warnings
              and input_idle
              and isinstance(engine.dynamic_brush_profile, dict) and engine.dynamic_brush_profile.get("valid", False)
              and any(s["message"].startswith("Кисть обучена и проверена.") for s in states))
    write("report.json", dict(exit_code=exit_code, passed=passed, states=states, profile=engine.dynamic_brush_profile,
                              closed=quick.controller._closed, qml_warnings=quick.qml_warnings,
                              input_idle=input_idle, button_delay_seconds=args.button_delay,
                              max_timer_gap_seconds=max(gaps, default=0), real_input=True))
finally:
    (output / "close-target").touch()
    target.wait(timeout=10)
sys.exit(0 if passed else 1)
