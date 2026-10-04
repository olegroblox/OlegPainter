"""Offline diagnostics for saved Paint experiments; never sends desktop input."""
import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))


def user_preview(root):
    import shutil
    config = root / "preview-configs"
    shutil.copytree(root / "user-configs-before", config, dirs_exist_ok=True)
    os.environ["OLEGPAINTER_CONFIG_DIR"] = str(config.resolve())
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PySide6.QtWidgets import QApplication
    from PySide6.QtCore import QTimer
    from application.controller import ApplicationController
    from ui.services.painter_service import PainterService
    app = QApplication([])
    service = PainterService()
    controller = ApplicationController(service)
    controller.restore()
    completed = []

    def finish():
        if service._last_qimg is not None and not service._last_qimg.isNull():
            service._last_qimg.save(str(root / "spongebob-expected.png"))
            (root / "spongebob-restored-config.json").write_text(json.dumps(
                service.snapshot_painter_config(), ensure_ascii=False, indent=2), encoding="utf-8")
            completed.append(True)
            app.quit()
    timer = QTimer()
    timer.timeout.connect(finish)
    timer.start(250)
    QTimer.singleShot(45000, app.quit)
    app.exec()
    timer.stop()
    controller.close(save=False)
    if not completed:
        raise RuntimeError("Saved user session did not produce a preview")


def geometry(root):
    import numpy as np
    from PIL import Image
    from helpers.sim_pen import engine_for
    source = np.asarray(Image.open(root / "00/expected.png").convert("RGB"))
    colours = np.unique(source.reshape(-1, 3), axis=0)
    rows = []
    for path in sorted(root.glob("[0-9][0-9]/result.json")):
        case = json.loads(path.read_text(encoding="utf-8"))
        settings = case["settings"]
        colour_rows = []
        for colour in colours:
            mask = np.all(source == colour, axis=2).astype(np.uint8)*255
            engine, pen = engine_for(mask, runlen=False, astar=False)
            for key, value in settings.items():
                setattr(engine, key, value)
            engine.dfs_4dir_fill_current_color(mask, 0, 0, 1)
            colour_rows.append(dict(rgb=colour.tolist(), pixels=int((mask>0).sum()),
                missing=pen.missing(mask), stray=pen.stray(mask),
                lines=pen.lines, downs=pen.downs))
        rows.append(dict(index=case["index"], colours=colour_rows))
    (root / "geometry.json").write_text(json.dumps(rows, indent=2), encoding="utf-8")
    print(json.dumps(rows))


def trace(root):
    import numpy as np
    from PIL import Image, ImageDraw, ImageFont
    rows = []
    font = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 16)
    sheet = Image.new("RGB", (6*285, 3*265), "#eeeeee")
    labels = ImageDraw.Draw(sheet)
    for path in sorted(root.glob("[0-9][0-9]/input.json")):
        events = json.loads(path.read_text())
        case = json.loads(path.with_name("result.json").read_text(encoding="utf-8"))
        reference = Image.open(path.with_name("expected.png")).convert("RGBA")
        expected = np.array(Image.alpha_composite(Image.new("RGBA",reference.size,"white"),reference).convert("RGB"))
        x0, y0, w, h = case["region"]
        im = Image.new("RGB", (w, h), "white")
        draw = ImageDraw.Draw(im)
        palette = {(p["x"], p["y"]): tuple(p["rgb"]) for p in case["configuration"]["manual_palette_coords"]}
        colour = (0, 0, 0)
        down = False
        pos = None
        selections = []
        move_times = []
        held_intervals = []
        last_move = None
        stroke_count = 0
        for t, kind, a, b, end in events:
            if kind == "move":
                if down and pos is not None:
                    draw.line((pos[0]-x0, pos[1]-y0, a-x0, b-y0), fill=colour, width=1)
                    if last_move is not None:
                        held_intervals.append((t-last_move)*1000)
                pos = (a, b)
                move_times.append(t)
                last_move = t
            elif kind == "down" and a == "left":
                if pos in palette:
                    colour = palette[pos]
                    selections.append(dict(rgb=colour, seconds=t-case["recording"]["started_at"]))
                elif pos is not None:
                    draw.point((pos[0]-x0, pos[1]-y0), fill=colour)
                    stroke_count += 1
                down = True
                last_move = None
            elif kind == "up" and a == "left":
                down = False
                last_move = None
        im.save(path.with_name("command-render.png"))
        row = dict(index=case["index"], moves=len(move_times), strokes=stroke_count,
            command_render_different=int(np.any(np.asarray(im)!=expected, axis=2).sum()),
            held_move_interval_ms=np.percentile(held_intervals, [10,50,90]).tolist() if held_intervals else [],
            selections=selections)
        rows.append(row)
        # Exact timestamps identify colour boundaries. Frames are only diagnostic;
        # the Paint PNG and command render remain separate measurements.
        if (w,h) == (128,96) and case["index"] in (0,2,6):
            k = (0,2,6).index(case["index"])
            frames = json.loads((path.parent/"frames/frames.json").read_text())
            ends = [s["seconds"]-.12 for s in selections[1:]] + [frames[-1]["seconds"]]
            for n, end in enumerate(ends):
                frame = min(frames, key=lambda f: abs(f["seconds"]-end))
                pic = Image.open(path.parent/"frames"/frame["file"]).convert("RGB")
                pic = pic.resize((272,208), Image.Resampling.NEAREST)
                sheet.paste(pic, (n*285+6,k*265+28))
                labels.text((n*285+6,k*265+5), f"№{case['index']:02d} · {frame['seconds']:.2f} с", fill="black",font=font)
            actual_path = path.with_name("actual.png")
            if actual_path.exists():
                pic = Image.open(actual_path).resize((256,192),Image.Resampling.NEAREST)
                sheet.paste(pic,(5*285+6,k*265+28))
                labels.text((5*285+6,k*265+5),"Итоговый PNG Paint",fill="black",font=font)
    (root/"input-analysis.json").write_text(json.dumps(rows,ensure_ascii=False,indent=2),encoding="utf-8")
    if rows and (w,h) == (128,96):
        sheet.save(root/"frame-stages.png")
    print(json.dumps(rows))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("preview", "geometry", "trace"))
    parser.add_argument("root", type=Path)
    args = parser.parse_args()
    {"preview": user_preview, "geometry": geometry, "trace": trace}[args.mode](args.root)
