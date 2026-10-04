"""Opt-in native-window inspection with isolated data and no device hooks.

Run with --live. Close the window normally; a five-minute deadline also closes
it. The report records real service values and UI-loop timing for this run only.
"""
import argparse
import json
import os
from pathlib import Path
import sys
import time
from threading import Event

parser = argparse.ArgumentParser()
parser.add_argument("--live", action="store_true", required=True)
parser.add_argument("--output", default="test-results/quick-live")
parser.add_argument("--qt-dpi", action="store_true", help="Use Qt's own DPI setup for comparison")
parser.add_argument("--software", action="store_true", help="Explicit rendering comparison, not an app fallback")
parser.add_argument("--render-loop", choices=("basic", "threaded"))
parser.add_argument("--rhi", choices=("d3d11", "d3d12", "opengl"))
parser.add_argument("--exercise", action="store_true", help="Exercise the scene with timed page changes")
parser.add_argument("--exercise-close", action="store_true", help="Close during a controlled background task")
parser.add_argument("--exercise-save", action="store_true", help="Close during a controlled session write")
parser.add_argument("--preparation", action="store_true", help="Load a controlled source for live background editing")
parser.add_argument("--qt-vblank", action="store_true", help="Reproduce Qt's DXGI vblank update delivery")
args = parser.parse_args()
target = Path(args.output).resolve()
target.mkdir(parents=True, exist_ok=True)
os.environ["OLEGPAINTER_CONFIG_DIR"] = str(target / "configs")
os.environ.pop("QT_QPA_PLATFORM", None)
os.environ.pop("QT_QUICK_BACKEND", None)
if args.software:
    os.environ["QT_QUICK_BACKEND"] = "software"
if args.render_loop:
    os.environ["QSG_RENDER_LOOP"] = args.render_loop
if args.rhi:
    os.environ["QSG_RHI_BACKEND"] = args.rhi
if args.qt_vblank:
    os.environ["QT_D3D_NO_VBLANK_THREAD"] = "0"
os.environ["QSG_INFO"] = "1"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QSettings, QTimer, Qt, QUrl, qInstallMessageHandler, qVersion
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication
from ui.helpers.app_setup import _apply_app_font, _configure_high_dpi

qt_messages = []
def qt_message(kind, context, message):
    if len(qt_messages) < 2000:
        qt_messages.append(dict(kind=kind.name, category=context.category, message=message))
qInstallMessageHandler(qt_message)

if not args.qt_dpi:
    _configure_high_dpi()
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(target / "settings"))
QQuickStyle.setStyle("Basic")
app = QApplication([])
_apply_app_font(app)
from ui.quick.application import QuickApplication
quick = QuickApplication(desktop=False)
quick.window.setTitle("OlegPainter — проверка QML")
quick.window.resize(1100, 750)
quick.window.setPosition(30, 30)
changes, gaps = [], []
frames, observations = [], []
render_checks = []
close_check = {}
close_release = Event()
exercise_pages = (1, 2, 3, 4, 0)
last = time.monotonic()
quick.window.frameSwapped.connect(lambda: frames.append(time.monotonic()), Qt.QueuedConnection)


def exercise_page(page):
    before = len(frames)
    started = time.monotonic()
    quick.window.setProperty("page", page)

    def verify():
        screenshot = quick.window.grabWindow()
        captured = not screenshot.isNull() and screenshot.save(str(target / f"page-{page}.png"))
        render_checks.append(dict(
            page=page, actual_page=quick.window.property("page"),
            frames_before=before, frames_after=len(frames),
            first_frame_ms=(frames[before] - started) * 1000 if len(frames) > before else None,
            screenshot_saved=captured,
            passed=quick.window.property("page") == page and len(frames) > before and captured))

    QTimer.singleShot(1000, verify)


def sample():
    global last
    now = time.monotonic()
    gaps.append(now - last)
    last = now
    if len(gaps) % 10 == 0:
        focus = quick.window.activeFocusItem()
        snapshot = dict(page=quick.window.property("page"), active=quick.window.isActive(),
                        focus=focus.objectName() if focus else None, changes=len(changes),
                        frames=len(frames), exposed=quick.window.isExposed(),
                        visible=quick.window.isVisible(), dpi=quick.window.devicePixelRatio(),
                        graphics_api=quick.window.rendererInterface().graphicsApi().name)
        observations.append(dict(at=now, **snapshot))
        (target / "live-state.json").write_text(json.dumps(snapshot), encoding="utf-8")


def changed(*_):
    changes.append(dict(at=time.monotonic(), settings=dict(quick.presenter.view["settings"]),
                        background=dict(quick.presenter.view["background"])))


quick.presenter.viewChanged.connect(changed)

if args.preparation:
    from PIL import Image, ImageDraw
    source = Image.new("RGBA", (160, 120), "#326AB2")
    draw = ImageDraw.Draw(source)
    draw.ellipse((35, 20, 110, 95), fill="#FFD426")
    draw.rectangle((90, 65, 145, 105), fill="#FFFFFF")
    path = target / "source.png"
    source.save(path)
    quick.presenter.setChoice("color_picking_method", "hex_field")
    quick.presenter.setNumber("k_clusters", 3)
    quick.presenter.setNumber("brush_size", 1)
    quick.service.apply_viewport_state(draw_region_desktop_px=(0, 0, 160, 120))
    quick.presenter.openSource(QUrl.fromLocalFile(str(path)))
    quick.window.resize(960, 640)


def exercise_close():
    # A real Qt worker, with no device input. The five-second bound makes the
    # original blocking shutdown fail the check instead of hanging this tool.
    if args.exercise_save:
        from PIL import Image
        quick.service.engine.source_pil_image = Image.new("RGBA", (2048, 2048), "#20c480")
        quick.service._set_session_image_source("clipboard_cache")
        write = quick.controller.session.store.write_autosave
        def delayed_write(document, image):
            if not close_release.wait(5):
                raise TimeoutError("Controlled save was not released by the Qt timer")
            return write(document, image)
        quick.controller.session.store.write_autosave = delayed_write
    else:
        quick.service._start_in_worker(lambda: close_release.wait(5))
    QTimer.singleShot(50, request_busy_close)


def request_busy_close():
    started = time.monotonic()
    accepted = quick.window.close()
    close_check.update(accepted_immediately=accepted,
                       request_ms=(time.monotonic() - started) * 1000,
                       frames_before=len(frames))
    def inspect_waiting():
        close_check.update(
            phase=quick.presenter.view.get("close_phase"),
            waiting_visible=quick.window.isVisible() and quick.presenter.view.get("closing", False),
            frames_while_waiting=len(frames) - close_check["frames_before"],
            screenshot_saved=quick.window.grabWindow().save(str(target / "closing.png")))
    QTimer.singleShot(300, inspect_waiting)
    QTimer.singleShot(1200, close_release.set)


heartbeat = QTimer()
heartbeat.timeout.connect(sample)
heartbeat.start(100)
deadline = QTimer()
deadline.setSingleShot(True)
deadline.timeout.connect(quick.window.close)
deadline.start(300_000)
if args.exercise:
    for index, page in enumerate(exercise_pages):
        QTimer.singleShot(1500 + index * 1500, lambda page=page: exercise_page(page))
    QTimer.singleShot(1500 * len(exercise_pages) + 2000,
                      exercise_close if args.exercise_close or args.exercise_save else quick.window.close)
elif args.exercise_close or args.exercise_save:
    QTimer.singleShot(1500, exercise_close)
exit_code = app.exec()
final_settings = quick.service.snapshot_painter_config()
quick.dispose(save=False)
report = dict(exit_code=exit_code, qml_warnings=quick.qml_warnings, changes=changes,
              final_settings=final_settings,
              event_loop_max_gap_seconds=max(gaps, default=0),
              pid=os.getpid(), controller_closed=quick.controller._closed,
              device_hooks_enabled=False, frames=len(frames), observations=observations,
              render_loop=args.render_loop, requested_rhi=args.rhi, software=args.software,
              qt_messages=qt_messages, render_checks=render_checks,
              close_check=close_check,
              qt_version=qVersion(),
              qt_d3d_no_vblank_thread=os.environ.get("QT_D3D_NO_VBLANK_THREAD"))
passed = (exit_code == 0 and not quick.qml_warnings and quick.controller._closed
          and (not args.exercise or len(render_checks) == len(exercise_pages)
               and all(c["passed"] for c in render_checks)))
if args.exercise_close or args.exercise_save:
    passed = (passed and close_check.get("request_ms", 9999) < 200
              and not close_check.get("accepted_immediately", True)
              and close_check.get("waiting_visible", False)
              and close_check.get("frames_while_waiting", 0) > 0
              and close_check.get("screenshot_saved", False))
    if args.exercise_save:
        passed = passed and close_check.get("phase") == "saving"
report["passed"] = passed
(target / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(json.dumps({key: report[key] for key in ("exit_code", "qml_warnings", "controller_closed", "passed", "render_checks")}), flush=True)
sys.exit(0 if passed else 1)
