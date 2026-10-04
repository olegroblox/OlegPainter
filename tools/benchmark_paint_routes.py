"""Opt-in real Paint comparison through the unchanged QML service/engine.

The operator observes a blank Paint canvas at 100%, selects its 1 px brush,
and supplies physical screen coordinates. Each case occupies a separate tile.
No canvas clearing, window manipulation, repair, or result-driven input occurs.
Frames are diagnostic recordings only; accuracy is measured later from Paint's
saved PNG, not from the screen recorder. F4 aborts the entire series.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import json
import os
from pathlib import Path
import sys
import threading
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

FLAGS = ["run_length_merge_enabled", "astar_bridge_enabled", "euler_greedy_pairing_enabled",
         "area_order_2opt_enabled", "area_order_or_opt_enabled", "area_entry_exit_routing_enabled",
         "fill_route_polish_enabled", "snake_turn_minimize_enabled", "motion_profile_enabled"]
LABELS = ["Прямые штрихи", "Переходы внутри цвета", "Один сложный маршрут",
          "Сокращение переходов", "Перестановка областей", "Точки входа/выхода",
          "Уточнение маршрута", "Меньше поворотов", "Плавная скорость"]


def cases():
    rows = [("Базовый: всё выключено", {})]
    rows += [(label, {key: True}) for key, label in zip(FLAGS, LABELS)]
    rows += [
        ("Штрихи + переходы", {FLAGS[0]: True, FLAGS[1]: True}),
        ("Штрихи + один маршрут", {FLAGS[0]: True, FLAGS[2]: True}),
        ("Все настройки переходов", {k: True for k in FLAGS[3:6]}),
        ("Штрихи + уточнение + повороты", {k: True for k in (FLAGS[0], FLAGS[6], FLAGS[7])}),
        ("Всё включено", dict.fromkeys(FLAGS, True)),
        ("Гильберт, всё выключено", {"fill_traversal_mode": "gilbert"}),
        ("Ферма, всё выключено", {"fill_traversal_mode": "fermat"}),
        ("Гильберт, всё включено", dict(dict.fromkeys(FLAGS, True), fill_traversal_mode="gilbert")),
        ("Ферма, всё включено", dict(dict.fromkeys(FLAGS, True), fill_traversal_mode="fermat")),
        ("Всё включено, светлые первыми", dict(dict.fromkeys(FLAGS, True), tone_sequence="light_to_dark")),
        ("Всё включено, маленькие первыми", dict(dict.fromkeys(FLAGS, True), area_sequence="small_to_large")),
        ("Штрихи, пауза 1 мс, плавность выкл.", {FLAGS[0]: True, "area_fill_delay": .001}),
        ("Штрихи, пауза 1 мс, плавность вкл.", {FLAGS[0]: True, FLAGS[8]: True, "area_fill_delay": .001}),
        ("Базовый, повтор", {}),
        ("Штрихи, 1 мс — повтор", {FLAGS[0]: True, "area_fill_delay": .001}),
        ("Штрихи, пауза 2 мс", {FLAGS[0]: True, "area_fill_delay": .002}),
        ("Штрихи, пауза 3 мс", {FLAGS[0]: True, "area_fill_delay": .003}),
        ("Штрихи + плавность, 2 мс", {FLAGS[0]: True, FLAGS[8]: True, "area_fill_delay": .002}),
        ("Всё включено, пауза 2 мс", dict(dict.fromkeys(FLAGS, True), area_fill_delay=.002)),
        ("Ферма, всё включено, 2 мс", dict(dict.fromkeys(FLAGS, True), fill_traversal_mode="fermat", area_fill_delay=.002)),
        ("Штрихи, 3 мс — повтор", {FLAGS[0]: True, "area_fill_delay": .003}),
    ]
    base = dict.fromkeys(FLAGS, False)
    base.update(fill_traversal_mode="auto", tone_sequence="dark_to_light",
                area_sequence="large_to_small", area_fill_delay=0.0)
    return [dict(index=i, name=name, settings=dict(base, **patch)) for i, (name, patch) in enumerate(rows)]


def timing_cases():
    base = cases()[1]["settings"]
    rows = [(f"Пауза после штриха {ms:g} мс", dict(base, draw_delay=ms/1000))
            for ms in (.1, .5, 1, 2, 3, 5)]
    rows += [("Пауза на пиксель 3 мс", dict(base, draw_delay=.0001, area_fill_delay=.003)),
             ("Пауза после штриха 2 мс — повтор", dict(base, draw_delay=.002)),
             ("Пауза после штриха 1 мс — повтор", dict(base, draw_delay=.001)),
             ("Ближайшие области + переходы, 1 мс", dict(base, draw_delay=.001,
                  area_sequence="nearest", area_order_2opt_enabled=True,
                  area_order_or_opt_enabled=True, area_entry_exit_routing_enabled=True)),
             ("Без объединения, безопасный DFS, 1 мс", dict(base,
                  run_length_merge_enabled=False, draw_delay=.001))]
    return [dict(index=i, name=name, settings=settings) for i, (name, settings) in enumerate(rows)]


def sponge_cases(cfg):
    base = {key: cfg[key] for key in FLAGS}
    base.update(draw_delay=.0001, pen_settle_delay=.01, area_fill_delay=0.,
                fill_traversal_mode="auto", area_sequence="large_to_small", tone_sequence="dark_to_light")
    rows = [("Твои настройки — контроль", base),
            ("Уточнение маршрута, старт 5 мс", dict(base, fill_route_polish_enabled=True, pen_settle_delay=.005)),
            ("Уточнение, старт 3 мс, движение 1 мс", dict(base, fill_route_polish_enabled=True, pen_settle_delay=.003, draw_delay=.001)),
            ("Уточнение, старт 1 мс, движение 1 мс", dict(base, fill_route_polish_enabled=True, pen_settle_delay=.001, draw_delay=.001)),
            ("Уточнение, старт 3 мс, движение 2 мс", dict(base, fill_route_polish_enabled=True, pen_settle_delay=.003, draw_delay=.002)),
            ("Старт 3 мс, движение 2 мс — повтор", dict(base, fill_route_polish_enabled=True, pen_settle_delay=.003, draw_delay=.002)),
            ("Уточнение, старт 5 мс, движение 1 мс", dict(base, fill_route_polish_enabled=True, pen_settle_delay=.005, draw_delay=.001)),
            ("Старт 5 мс, движение 1 мс — повтор", dict(base, fill_route_polish_enabled=True, pen_settle_delay=.005, draw_delay=.001)),
            ("Уточнение, старт 5 мс, движение 2 мс", dict(base, fill_route_polish_enabled=True, pen_settle_delay=.005, draw_delay=.002)),
            ("Старт 5 мс, движение 2 мс — повтор", dict(base, fill_route_polish_enabled=True, pen_settle_delay=.005, draw_delay=.002))]
    return [dict(index=i,name=name,settings=settings) for i,(name,settings) in enumerate(rows)]


def make_source(path):
    from PIL import Image, ImageDraw
    im = Image.new("RGB", (128, 96), "white")
    d = ImageDraw.Draw(im)
    # Solid areas, holes, 1px diagonals, disconnected dots and a branching fence.
    d.rectangle((14, 42, 67, 82), fill="#00A2E8", outline="black")
    d.polygon([(8, 42), (40, 13), (73, 42)], fill="#ED1C24", outline="black")
    d.rectangle((23, 49, 38, 63), fill="white", outline="black")
    d.line((30, 49, 30, 63), fill="black")
    d.line((23, 56, 38, 56), fill="black")
    d.rectangle((48, 58, 58, 82), fill="#FFF200", outline="black")
    d.point((55, 70), fill="black")
    d.ellipse((89, 7, 113, 31), fill="#FFF200", outline="black")
    d.ellipse((95, 13, 107, 25), fill="white")
    d.line((80, 60, 119, 60), fill="black")
    d.line((80, 73, 119, 73), fill="black")
    for x in range(80, 120, 6):
        d.line((x, 53, x, 82), fill="black")
    d.line((5, 89, 122, 89), fill="black")
    for x, y in [(5, 5), (72, 7), (80, 19), (118, 38), (9, 72), (72, 87)]:
        d.rectangle((x, y, x+1, y+1), fill="#ED1C24")
    im.save(path)


class Recorder(threading.Thread):
    def __init__(self, folder, region):
        super().__init__(daemon=True)
        self.folder, self.region = folder, region
        self.stop_event = threading.Event()
        self.frames, self.errors = [], []
        self.origin = time.perf_counter()

    def run(self):
        from infrastructure.screen_capture import capture_screen
        x, y, w, h = self.region
        try:
            while not self.stop_event.is_set():
                begin = time.perf_counter()
                frame = capture_screen(bbox=(x-4, y-4, x+w+4, y+h+4))
                captured = time.perf_counter()
                name = f"{len(self.frames):05d}.png"
                frame.save(self.folder / name)
                self.frames.append(dict(file=name, seconds=captured-self.origin,
                                        capture_ms=(captured-begin)*1000))
                self.stop_event.wait(max(0, .2 - (time.perf_counter()-begin)))
        except Exception as error:
            self.errors.append(str(error))
        finally:
            (self.folder / "frames.json").write_text(json.dumps(self.frames, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--origin", type=int, nargs=2, required=True)
    parser.add_argument("--columns", type=int, default=6)
    parser.add_argument("--start-index", type=int, default=0)
    parser.add_argument("--layout-start-index", type=int, default=0,
                        help="Case index placed at --origin when continuing in another blank strip")
    parser.add_argument("--limit", type=int, default=24)
    parser.add_argument("--suite", choices=("routes", "timing", "sponge"), default="routes")
    parser.add_argument("--user-config", type=Path)
    parser.add_argument("--image", type=Path)
    parser.add_argument("--expected-reference", type=Path)
    parser.add_argument("--trace-input", action="store_true")
    args = parser.parse_args()
    output = args.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    os.environ["OLEGPAINTER_CONFIG_DIR"] = str(output / f"session-{args.start_index:02d}")
    os.environ.pop("QT_QPA_PLATFORM", None)
    os.environ.pop("QT_QUICK_BACKEND", None)
    from PIL import ImageColor
    from PySide6.QtCore import QSettings, QTimer, QUrl
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication
    from ui.helpers.app_setup import _configure_high_dpi
    _configure_high_dpi()
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, str(output / "qt-settings"))
    QQuickStyle.setStyle("Basic")
    app = QApplication([])
    from ui.quick.application import QuickApplication
    from application.settings import apply_setting
    quick = QuickApplication(initialize_runtime=True)
    service = quick.service
    engine = service.engine
    input_trace = []
    if args.trace_input:
        backend = engine._input.backend
        original_move, original_button = backend.move, backend.button

        def traced_move(x, y):
            started = time.perf_counter()
            result = original_move(x, y)
            input_trace.append((started, "move", int(x), int(y), time.perf_counter()))
            return result

        def traced_button(button, down):
            started = time.perf_counter()
            result = original_button(button, down)
            input_trace.append((started, "down" if down else "up", button, None, time.perf_counter()))
            return result

        backend.move, backend.button = traced_move, traced_button
    quick.window.setTitle("OlegPainter — сравнение маршрутов, F4: остановить серию")
    quick.window.showMinimized()
    quick.controller.profiles.select("universal", "dfs_4dir")
    cfg = service.snapshot_painter_config()
    cfg.update(brush_size=1, dynamic_brush_enabled=False, post_draw_repair_enabled=False,
               semantic_region_split_enabled=False, draw_delay=.0001, pen_button_delay=0.,
               pen_settle_delay=.01, area_fill_delay=0., pen_max_step=0,
               pre_actions_enabled=False, post_actions_enabled=False, draw_with_layers_enabled=False,
               color_picking_method="manual_palette", manual_palette_mix_enabled=False,
               k_clusters=5, color_merge_threshold=0., prep_cleanup_mode="off",
               aggressive_despeckle_enabled=False, prep_dither_enabled=False,
               min_region_area=0, small_components_threshold=0,
               remove_background=False, background_removal_enabled=False,
               background_removal_mode="corner", background_color_tolerance=0,
               telemetry_enabled=True)
    # Palette coordinates observed in maximized Paint at this desktop's 125% DPI.
    cfg["manual_palette_coords"] = [dict(hex=c, rgb=list(ImageColor.getrgb(c)), x=x, y=y)
                                    for c, x, y in [("#000000",1000,105),("#ED1C24",1089,105),
                                                   ("#FFF200",1149,105),("#00A2E8",1209,105),
                                                   ("#FFFFFF",1000,135)]]
    if args.suite == "sponge":
        if not args.user_config or not args.image or not args.expected_reference:
            raise ValueError("Sponge comparison needs saved configuration, source and expected reference")
        cfg = json.loads(args.user_config.read_text(encoding="utf-8"))
        cfg.update(dynamic_brush_enabled=False,post_draw_repair_enabled=False,
                   pre_actions_enabled=False,post_actions_enabled=False,draw_with_layers_enabled=False)
        # The saved palette is the same two standard rows observed in maximized Paint.
        for i, sample in enumerate(cfg["manual_palette_coords"]):
            sample.update(x=1000+(i%10)*30, y=105+(i//10)*30)
    service.load_config(cfg)
    if args.image:
        import shutil
        shutil.copyfile(args.image, output / "source.png")
    else:
        make_source(output / "source.png")
    quick.presenter.openSource(QUrl.fromLocalFile(str(output / "source.png")))
    if args.suite == "sponge":
        # Opening a source resets crop; restore the user's preparation explicitly.
        service.load_config(cfg)
    plan = sponge_cases(cfg) if args.suite == "sponge" else timing_cases() if args.suite == "timing" else cases()
    width, height = (246,306) if args.suite == "sponge" else (128,96)
    (output / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    pending = plan[args.start_index:args.start_index + args.limit]
    current, recorder = None, None
    ready_at = time.monotonic()+4
    phase = "configure"
    status = []

    def save():
        (output / "progress.json").write_text(json.dumps(dict(phase=phase, current=current,
            remaining=len(pending), state=asdict(quick.controller.state),
            recent_status=status[-5:]), ensure_ascii=False, indent=2), encoding="utf-8")

    def event_received(event):
        nonlocal phase, ready_at
        if current is None or "requested_at" not in current:
            return
        current.setdefault("events", []).append(dict(phase=event.phase.value, at=time.perf_counter(), error=event.error))
        if event.phase.value == "started":
            current["started_at"] = time.perf_counter()
        if event.phase.terminal:
            current["finished_at"] = time.perf_counter()
            current["elapsed_seconds"] = current["finished_at"] - current.get("started_at", current["requested_at"])
            current["result"] = event.phase.value
            phase = "finish"
            ready_at = time.monotonic()+.5
        save()

    service.drawingEvent.connect(event_received)
    service.statusChanged.connect(lambda value: status.append(str(value)))

    def tick():
        nonlocal current, recorder, phase, ready_at
        try:
            if time.monotonic() < ready_at:
                return
            if phase == "configure":
                if not pending:
                    phase = "complete"
                    save()
                    quick.window.close()
                    return
                current = dict(pending.pop(0))
                input_trace.clear()
                index = current["index"]
                tile_index = index - args.layout_start_index
                if tile_index < 0:
                    raise ValueError("Layout start must not exceed the first selected case")
                region = (args.origin[0]+(tile_index % args.columns)*(width+32),
                          args.origin[1]+(tile_index // args.columns)*(height+32), width, height)
                current["region"] = region
                folder = output / f"{index:02d}"
                if (folder / "result.json").exists() or list((folder / "frames").glob("*.png")):
                    raise RuntimeError(f"Refusing to overwrite recorded case {index}")
                folder.mkdir(exist_ok=True)
                (folder / "frames").mkdir(exist_ok=True)
                for key, value in current["settings"].items():
                    apply_setting(quick.controller, key, value)
                service.apply_viewport_state(draw_region_desktop_px=region)
                service.request_preview_refresh()
                current["configured_at"] = time.monotonic()
                phase = "prepare"
                ready_at = time.monotonic()+.7
                save()
            elif phase == "prepare":
                if time.monotonic()-current["configured_at"] > 30:
                    raise RuntimeError("Preview did not become ready within 30 seconds")
                if not quick.controller.state.preparation.can_start:
                    save()
                    return
                assert not engine.dynamic_brush_enabled and not engine.post_draw_repair_enabled
                assert engine.brush_size == 1
                folder = output / f"{current['index']:02d}"
                if service._last_qimg is None or service._last_qimg.isNull():
                    return
                service._last_qimg.save(str(folder / "expected.png"))
                if args.expected_reference:
                    from PIL import Image
                    import numpy as np
                    actual_reference = np.array(Image.open(folder / "expected.png").convert("RGBA"))
                    reference = np.array(Image.open(args.expected_reference).convert("RGBA"))
                    if not np.array_equal(actual_reference, reference):
                        raise RuntimeError("Preparation differs from the saved user reference; no input sent")
                current["configuration"] = service.snapshot_painter_config()
                recorder = Recorder(folder / "frames", current["region"])
                recorder.start()
                current["requested_at"] = time.perf_counter()
                phase = "drawing"
                service.start()
                save()
            elif phase == "finish":
                if engine.drawing_thread and engine.drawing_thread.is_alive():
                    return
                recorder.stop_event.set()
                if recorder.is_alive():
                    return
                current["recording"] = dict(frames=len(recorder.frames), errors=recorder.errors,
                    started_at=recorder.origin, period_seconds=.2)
                folder = output / f"{current['index']:02d}"
                if args.trace_input:
                    (folder / "input.json").write_text(json.dumps(input_trace), encoding="utf-8")
                (folder / "result.json").write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
                if current["result"] != "completed" or recorder.errors:
                    pending.clear()
                phase = "configure"
                ready_at = time.monotonic()+.7
                save()
            elif phase == "drawing" and time.perf_counter()-current["requested_at"] > 240:
                service.stop()
        except Exception as error:
            phase = "failed"
            status.append(repr(error))
            save()
            if recorder:
                recorder.stop_event.set()
            service.stop()
            timer.stop()
            QTimer.singleShot(1000, quick.window.close)

    timer = QTimer()
    timer.timeout.connect(tick)
    timer.start(100)
    QTimer.singleShot(1800000, quick.window.close)
    try:
        app.exec()
    finally:
        timer.stop()
        if recorder:
            recorder.stop_event.set()
        quick.dispose(save=False)
        save()


if __name__ == "__main__":
    main()
