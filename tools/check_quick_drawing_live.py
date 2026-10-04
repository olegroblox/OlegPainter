"""Opt-in QML/real-input inspection in an independently opened Paint canvas.

This fixture writes a small source image and isolated settings. The operator
opens the image, captures the area/palette, and starts/stops through real UI.
It never starts drawing by itself. A deadline closes the app (ten minutes by default).
--load-source/--area/--palette-color may supply an explicit fixture; such a run
does not verify the file dialog or manual F1/F9 capture. Reported completion is engine telemetry,
not proof that the target canvas matches the source; inspect the saved canvas.
"""
import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import time

parser = argparse.ArgumentParser()
parser.add_argument("--live", action="store_true", required=True)
parser.add_argument("--output", required=True)
parser.add_argument("--load-source", action="store_true", help="Load the generated fixture through the presenter")
parser.add_argument("--duration", type=int, default=600, help="Live inspection deadline in seconds (60–3600)")
parser.add_argument("--trace-brush", action="store_true", help="Save actual before/after probe images and measurements")
parser.add_argument("--keep-session", action="store_true",
                    help="Use a session copied into output/configs without replacing its image, profile or timing settings")
parser.add_argument("--area", nargs=4, type=int, metavar=("X", "Y", "WIDTH", "HEIGHT"),
                    help="Explicit test fixture in observed physical screen coordinates; not an F1 test")
parser.add_argument("--palette-color", nargs=3, action="append", default=[], metavar=("HEX", "X", "Y"),
                    help="Explicit palette fixture at observed physical coordinates; not a capture test")
args = parser.parse_args()
if not 60 <= args.duration <= 3600:
    parser.error("--duration must be between 60 and 3600 seconds")
target = Path(args.output).resolve()
target.mkdir(parents=True, exist_ok=True)
os.environ["OLEGPAINTER_CONFIG_DIR"] = str(target / "configs")
os.environ.pop("QT_QPA_PLATFORM", None)
os.environ.pop("QT_QUICK_BACKEND", None)
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PIL import Image, ImageDraw
from PySide6.QtCore import QSettings, QTimer, QUrl
from PySide6.QtQuickControls2 import QQuickStyle
from PySide6.QtWidgets import QApplication
from ui.helpers.app_setup import _apply_app_font, _configure_high_dpi

source = Image.new("RGB", (64, 64), "white")
draw = ImageDraw.Draw(source)
draw.rectangle((8, 8, 55, 55), fill="black")
draw.ellipse((20, 20, 43, 43), fill="white")
source.save(target / "source.png")
_configure_high_dpi()
QSettings.setDefaultFormat(QSettings.IniFormat)
QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(target / "settings"))
QQuickStyle.setStyle("Basic")
app = QApplication([])
_apply_app_font(app)

from ui.quick.application import QuickApplication
if args.trace_brush:
    # Observe the real measurement boundary. Save only after measurement so
    # diagnostic I/O does not delay either image capture within this probe.
    from engine.olegpainter.core import OlegPainter
    from threading import Lock
    trace_dir = target / "brush-trace"
    trace_dir.mkdir(exist_ok=False)
    trace_lock = Lock()
    trace_count = 0
    original_measure_stamp = OlegPainter._dynamic_brush_v2_measure_stamp

    def traced_measure_stamp(engine, before, after, *, anchor=None):
        global trace_count
        measured = original_measure_stamp(engine, before, after, anchor=anchor)
        with trace_lock:
            trace_count += 1
            stem = f"{trace_count:03d}"
            before.save(trace_dir / f"{stem}-before.png")
            after.save(trace_dir / f"{stem}-after.png")
            (trace_dir / f"{stem}.json").write_text(json.dumps(dict(
                value=engine._last_dynamic_brush_value, anchor=anchor, measured=measured,
                timestamp=time.time()), ensure_ascii=False, indent=2), encoding="utf-8")
        return measured

    OlegPainter._dynamic_brush_v2_measure_stamp = traced_measure_stamp

quick = QuickApplication(initialize_runtime=True)
quick.window.setTitle("OlegPainter — проверка рисования QML")
quick.window.resize(980, 750)
quick.window.setPosition(20, 40)
service = quick.service
service.previewReady.connect(lambda image: image.save(str(target / "prepared.png"))
                             if image is not None and not image.isNull() else None)
if not args.keep_session:
    quick.controller.profiles.select("universal", "dfs_4dir")
    service.set_k_clusters(2)
    service.set_brush_size(1)
    service.set_draw_delay(0.01)
    service.set_pen_button_delay(0.004)
    service.set_pen_settle_delay(0.003)
if args.load_source:
    quick.presenter.openSource(QUrl.fromLocalFile(str(target / "source.png")))
if args.area:
    service.apply_viewport_state(draw_region_desktop_px=tuple(args.area))
if args.palette_color:
    from PIL import ImageColor
    service.engine.manual_palette_coords = [
        dict(hex=color, rgb=list(ImageColor.getrgb(color)), x=int(x), y=int(y))
        for color, x, y in args.palette_color
    ]
    service.engine._invalidate_manual_palette_cache()
    service._on_engine_manual_palette_changed()
events, stats, brush_states, area_selections = [], [], [], []
started = last_tick = time.monotonic()
max_gap = 0
report_replace_retries = 0


def event_received(event):
    events.append(dict(at=time.monotonic() - started, run_id=event.run_id,
                       phase=event.phase.value, error=event.error))


def stats_received(value):
    stats.append(dict(at=time.monotonic() - started, **value))


def brush_state_received():
    brush_states.append(dict(at=time.monotonic() - started, **service.brush_learning_snapshot()))


def area_selected(rect):
    overlay = quick.controller.desktop.kalka_overlay
    area_selections.append(dict(
        at=time.monotonic() - started,
        selected_logical_rect=list(rect.getRect()) if rect is not None else None,
        window_logical_rect=list(overlay.geometry().getRect()),
        stencil_geometry=overlay.get_stencil_geometry(),
    ))


def report(*, final=False):
    global last_tick, max_gap, report_replace_retries
    now = time.monotonic()
    max_gap = max(max_gap, now - last_tick)
    last_tick = now
    engine = service.engine
    result = dict(pid=os.getpid(), state=asdict(quick.controller.state),
                  source_preloaded=args.load_source, fixture_area=args.area,
                  copied_session=args.keep_session,
                  fixture_palette=args.palette_color,
                  brush_trace_enabled=args.trace_brush,
                  events=events, stats=stats, settings=service.snapshot_painter_config(),
                  area_selections=area_selections,
                  area=engine.draw_region, palette=engine.manual_palette_coords,
                  draw_thread_alive=bool(engine.drawing_thread and engine.drawing_thread.is_alive()),
                  stop_pending=service._stop_pending, max_heartbeat_gap=max_gap,
                  brush_learning=service.brush_learning_snapshot(), brush_states=brush_states,
                  controller_closed=quick.controller._closed,
                  qml_warnings=quick.qml_warnings,
                  old_shell_imported="ui.main_window" in sys.modules,
                  presenter_stats=quick.presenter.stats,
                  report_replace_retries=report_replace_retries)
    pending = target / "report.pending.json"
    pending.write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    try:
        pending.replace(target / "report.json")
    except PermissionError:
        # A concurrent Windows reader can temporarily forbid atomic replacement.
        # The next timer tick retries; the final report must succeed or fail loudly.
        if final:
            raise
        report_replace_retries += 1


service.drawingEvent.connect(event_received)
service.brushLearningChanged.connect(brush_state_received)
service.drawingStatsChanged.connect(stats_received)
quick.controller.desktop.kalka_overlay._view.areaRubberBandFinished.connect(area_selected)
timer = QTimer()
timer.timeout.connect(report)
timer.start(250)
QTimer.singleShot(args.duration * 1000, quick.window.close)
try:
    app.exec()
finally:
    timer.stop()
    quick.dispose(save=False)
    report(final=True)
