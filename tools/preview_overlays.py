"""Render real desktop widgets offscreen and measure idle repaint requests.

Usage: python tools/preview_overlays.py [output-directory] [--theme dark|light] [--lang ru|en]
Does not start global listeners, drivers, hotkeys or the drawing engine.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
from unittest.mock import patch

os.environ["QT_QPA_PLATFORM"] = "offscreen"
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QColor, QImage, QPainter, QPixmap, QFontDatabase, QFont
from PySide6.QtWidgets import QApplication

from ui.helpers.viewport_mapper import MonitorSnapshot
from ui.overlays.coordinates import DesktopCoordinates
from ui.overlays.capture_guide import CaptureGuideOverlay
from ui.overlays.calib_flash import CalibFlashOverlay
from ui.overlays.region_pick import RegionPickOverlay, PaletteRegionPickOverlay
from ui.widgets.kalka_stencil import KalkaStencilOverlay
from ui.widgets.hud_overlay import HudOverlay


def render(widget, path, size=None):
    image = QImage(size or widget.size(), QImage.Format_ARGB32_Premultiplied)
    image.fill(QColor("#44474a"))
    layer = QImage(image.size(), QImage.Format_ARGB32_Premultiplied)
    layer.fill(Qt.transparent)
    widget.render(layer)
    painter = QPainter(image)
    painter.drawImage(0, 0, layer)
    painter.end()
    if not image.save(str(path)):
        raise RuntimeError(f"Cannot save preview: {path}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", nargs="?", default="test-results/overlays")
    parser.add_argument("--theme", choices=("dark", "light"), default="dark")
    parser.add_argument("--lang", choices=("ru", "en"), default="ru")
    args = parser.parse_args()
    target = Path(args.output).resolve()
    target.mkdir(parents=True, exist_ok=True)
    app = QApplication.instance() or QApplication([])
    from ui.helpers.theme_manager import theme_manager
    from ui.i18n import i18n
    theme_manager.set_theme(args.theme)
    i18n.set_language(args.lang)
    # The Windows offscreen plugin does not enumerate installed fonts automatically.
    for name in ("segoeui.ttf", "segoeuib.ttf"):
        font = Path(os.environ["WINDIR"]) / "Fonts" / name
        if QFontDatabase.addApplicationFont(str(font)) < 0:
            raise RuntimeError(f"Cannot load preview font: {font}")
    app.setFont(QFont("Segoe UI", 10))
    monitor = MonitorSnapshot("preview", "preview", (0, 0, 1280, 800), (0, 0, 1280, 800), 1, 1)
    report = {}
    with patch("ui.overlays.coordinates.collect_monitor_snapshots", return_value=[monitor]), \
         patch("ui.overlays.pointer_surface.QCursor.pos", return_value=QPoint(850, 500)):
        guide = CaptureGuideOverlay()
        picker = RegionPickOverlay("Выделите ползунок яркости рамкой", lambda _: None)
        for name, surface in (("capture", guide), ("region", picker)):
            report[name] = {"timer_before_show": surface._anim.isActive()}
            surface.show()  # Not open(): no listeners.
            surface.setGeometry(0, 0, 1280, 800)
            surface.active_screen_rect = lambda: QRect(0, 0, 1280, 800)
            app.processEvents()
            with patch.object(surface, "update") as repaint:
                for _ in range(120):
                    surface._tick()
                report[name]["idle_repaint_requests_per_120_ticks"] = repaint.call_count
        from ui.helpers.hotkey_definitions import default_hotkey_profile
        from ui.overlays.controller import capture_hint
        keys = default_hotkey_profile()
        guide.set_state(capture_hint("layers", None, keys), 3,
                        [{"x": 180 + i * 110, "y": 240, "label": str(i + 1)} for i in range(3)])
        render(guide, target / "capture.png")
        guide.set_state(capture_hint("palette", None, keys), 13,
                        [{"x": 180 + (i % 10) * 30, "y": 240 + (i // 10) * 30, "label": str(i + 1)} for i in range(13)],
                        compact_marks=True)
        render(guide, target / "palette-compact-marks.png")
        picker._first_phys = (310, 220)
        render(picker, target / "region.png")
        for surface in (guide, picker):
            surface.close()
        palette = PaletteRegionPickOverlay(lambda _: None)
        palette.show()  # Render only; no hooks or device access.
        palette.setGeometry(0, 0, 1280, 800)
        palette.active_screen_rect = lambda: QRect(0, 0, 1280, 800)
        app.processEvents()
        render(palette, target / "palette-colors.png")
        palette._accept_region((310, 220, 280, 220))
        render(palette, target / "palette-brightness.png")
        palette.close()
        flash = CalibFlashOverlay([
            {"type": "point", "x": 220, "y": 320, "label": "HEX"},
            {"type": "circle", "x": 570, "y": 400, "r": 120, "label": "Круг цвета"},
            {"type": "line", "x1": 850, "y1": 280, "x2": 850, "y2": 580, "label": "Яркость"},
        ], "Клик или Esc — закрыть", 6000)
        flash.show()
        flash.setGeometry(0, 0, 1280, 800)
        report["calibration_marks"] = {"continuous_repaint_timer": hasattr(flash, "_anim")}
        render(flash, target / "calibration.png")
        flash.close()

        kalka = KalkaStencilOverlay()
        source = QImage(640, 440, QImage.Format_ARGB32)
        source.fill(QColor("#31575a"))
        painter = QPainter(source)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor("#e4b75a"))
        painter.drawEllipse(180, 70, 270, 270)
        painter.end()
        kalka.set_source_pixmap(QPixmap.fromImage(source))
        kalka.setGeometry(100, 130, 880, 600)
        kalka.show()
        kalka.set_edit_mode(True)
        app.processEvents()
        render(kalka, target / "stencil.png")
        render(kalka._edit_toolbar, target / "stencil-toolbar.png")
        kalka.begin_area_placement(monitor)
        kalka._view._placement_coordinates = DesktopCoordinates([monitor])
        kalka._view._rb_rect = QRect(260, 180, 700, 440)
        render(kalka, target / "f1.png")
        kalka.cancel_area_placement()
        kalka.close()

        hud = HudOverlay()
        hud.set_drawing_state("started")
        hud.update_stats({"percent": 42, "elapsed_seconds": 128, "eta_seconds": 305,
                          "colors_total": 24, "colors_done": 10, "current_color": "#58a89a"})
        hud.set_edit_mode(True)
        hud.setGeometry(0, 0, 1280, 800)
        app.processEvents()
        render(hud, target / "hud.png")
        render(hud._panel, target / "hud-card.png")
        render(hud._toolbar, target / "hud-toolbar.png")
        hud._appearance_pop.open_below(hud._appearance_btn)
        app.processEvents()
        render(hud._appearance_pop, target / "hud-appearance.png")
        hud._appearance_pop.hide()
        hud.close()
    report["scope"] = "Offscreen idle repaint requests, not GPU FPS or whole-app CPU performance"
    (target / "idle-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(json.dumps({"output": str(target), "measurements": report}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
