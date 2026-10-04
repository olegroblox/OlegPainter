"""Own desktop modes independently of the classic or web application shell."""
from __future__ import annotations

import math

from PySide6.QtCore import QObject, QTimer, Signal

from ui.i18n import localized
from .capture_guide import CaptureGuideOverlay
from .calib_flash import CalibFlashOverlay
from .region_pick import PointPickOverlay, RegionPickOverlay, TwoPointPickOverlay
from .region_pick import PaletteRegionPickOverlay, AlphaSliderPickOverlay
from .palette_calibration import PaletteCalibrationJob
from .wheel_calibration import WheelCalibrationJob
from infrastructure.capture_session import CaptureSession
from infrastructure.input_ownership import InputBusyError


_GUIDE_TEXT = {
    "hex": ("Кликните по HEX-полю в программе", "Click the HEX field in the app"),
    "palette": ("Кликайте по цветам", "Click palette colors"),
    "layers": ("Кликайте по кнопкам слоёв", "Click layer buttons"),
    "extra": ("Запись действий: выполняйте щелчки в программе", "Recording actions: click in the app"),
    "brush": ("Кликните по регулятору размера кисти", "Click the brush size control"),
    "outline_fill": ("Укажите инструменты обводки и заливки", "Select the outline and fill tools"),
    "background": ("Кликните по свободному фону холста", "Click the blank canvas background"),
}

# The main window is hidden while these captures run: the hint must name the
# key that really finishes them (users rebind keys; "[" is the Russian "х").
_FINISH_HOTKEY = {"palette": "define_manual_palette", "layers": "define_app_layers",
                  "extra:pre": "record_pre_color_actions", "extra:post": "record_post_color_actions"}


def _key_label(sequence):
    names = {"[": "[ (русская Х)", "]": "] (русская Ъ)"}
    return localized(names.get(sequence, sequence), sequence)


def capture_hint(kind, slot, bindings):
    """Guide title with the real finish/stop keys for the capture `kind`."""
    title = localized(*_GUIDE_TEXT.get(kind, ("Действуйте в целевой программе", "Act in the target app")))
    code = _FINISH_HOTKEY.get(f"{kind}:{slot or 'pre'}" if kind == "extra" else kind)
    if code is None:
        return title
    finish, stop = bindings.get(code, ""), bindings.get("stop", "")
    if finish:
        title += localized(f" — {_key_label(finish)} сохранить", f" — {_key_label(finish)} to save")
    else:
        title += localized(" — сохранить: окно OlegPainter на панели задач", " — save in the OlegPainter window (taskbar)")
    if stop and kind == "palette":  # colours already measured stay in the palette
        title += localized(f", {stop} стоп", f", {stop} to stop")
    elif stop:  # layers/actions are a draft: stopping keeps the previous record
        title += localized(f", {stop} отмена", f", {stop} to cancel")
    return title




class DesktopOverlayController(QObject):
    modeChanged = Signal(str)

    def __init__(self, workspace):
        super().__init__(workspace)
        self.workspace = workspace
        self.service = None
        self.guide = None
        self.picker = None
        self.flash = None
        self._capture_key = None
        self._switching = False
        self._closing = False
        self._input_session = None
        self._palette_job = None
        self._wheel_job = None
        self.mode = ""
        if workspace.kalka_overlay is not None:
            workspace.kalka_overlay.editModeChanged.connect(self._stencil_interaction_changed)
            workspace.kalka_overlay.placementModeChanged.connect(self._stencil_interaction_changed)
        if workspace.hud_overlay is not None:
            workspace.hud_overlay.editModeChanged.connect(lambda on: self._edit_changed("hud", on))

    def bind_service(self, service):
        if self.service is service:
            return
        bindings = (("captureStateChanged", self._capture_changed),
                    ("drawingStateChanged", self._drawing_changed),
                    ("screenCalibrationRequested", self.calibrate),
                    ("brushCalibrationRequested", self.calibrate_brush))
        if self.service is not None:
            self.service._brush_capture_available = False
            for name, slot in bindings:
                getattr(self.service, name).disconnect(slot)
        self.service = service
        if service is not None:
            service._brush_capture_available = True
            for name, slot in bindings:
                getattr(service, name).connect(slot)

    def _close_transient(self):
        if self._palette_job is not None:
            self._palette_job.cancel()
        if getattr(self, "_wheel_job", None) is not None:
            self._wheel_job.cancel()
        if self.picker is not None:
            picker = self.picker
            picker.cancel()
            if self.picker is picker:
                self.picker = None
            picker.deleteLater()
        if self.flash is not None:
            flash, self.flash = self.flash, None
            flash.close()
        if self.guide is not None:
            self.guide.hide()
        self._capture_key = None

    def prepare(self, mode, *, cancel_capture=True):
        """Only one interactive desktop operation owns input at a time."""
        if self._switching or self._closing:
            return
        self._switching = True
        try:
            self._close_transient()
            k = self.workspace.kalka_overlay
            if k is not None and mode != "stencil":
                k.cancel_area_placement()
                k.set_edit_mode(False, capture_on_exit=False)
            hud = self.workspace.hud_overlay
            if hud is not None and mode != "hud":
                hud.set_edit_mode(False)
            if cancel_capture and self.service is not None:
                self.service.cancel_active_captures()
        except Exception as error:
            if self.service is not None:
                self.service.statusChanged.emit(str(error))
            return False
        finally:
            self._switching = False
        try:
            self._set_mode(mode if mode in ("stencil", "hud", "pick", "capture") else "")
            return True
        except InputBusyError as error:
            self._set_mode("")
            if self.service is not None:
                self.service.statusChanged.emit(str(error))
            return False

    def _set_mode(self, mode):
        if mode != self.mode:
            if self._input_session is not None:
                self._input_session.close()
                self._input_session = None
            if mode in ("stencil", "hud"):
                self._input_session = CaptureSession("редактирование трафарета" if mode == "stencil" else "настройка экранной панели")
            self.mode = mode
            hud = self.workspace.hud_overlay
            if hud is not None:
                hud.set_tool_hidden(mode in ("stencil", "pick", "capture", "measure"))
            self.modeChanged.emit(mode)

    def _edit_changed(self, mode, on):
        if self._switching:
            return
        if on:
            if not self.prepare(mode):
                if mode == "stencil":
                    self.workspace.kalka_overlay.cancel_area_placement()
                    self.workspace.kalka_overlay.set_edit_mode(False, capture_on_exit=False)
                else:
                    self.workspace.hud_overlay.set_edit_mode(False)
        elif not self._switching and self.mode == mode:
            self._set_mode("")

    def _stencil_interaction_changed(self, *_):
        stencil = self.workspace.kalka_overlay
        self._edit_changed("stencil", stencil.is_edit_mode() or stencil.is_placing_area())

    def _drawing_changed(self, state):
        if str(state) in ("started", "paused"):
            self.prepare("drawing")
        elif str(state) == "stopping" and not self._closing:
            # F4 must also cancel pickers and editors owned by Qt. Engine hooks
            # are stopped by the existing worker; do not duplicate that I/O here.
            if not self.prepare("stop", cancel_capture=False) and self.service is not None:
                self.service._on_stop_error(localized(
                    "Не удалось закрыть экранный инструмент. Повторите остановку.",
                    "Could not close the screen tool. Try stopping again."))

    def _capture_changed(self, payload):
        if self._closing:
            return
        payload = payload if isinstance(payload, dict) else {}
        if not payload.get("active"):
            if self.guide is not None:
                self.guide.hide()
            self._capture_key = None
            if self.mode == "capture":
                self._set_mode("")
            return
        key = (payload.get("kind"), payload.get("slot"))
        if key != self._capture_key:
            if not self.prepare("capture", cancel_capture=False):
                return
            self._capture_key = key
        if self.guide is None:
            self.guide = CaptureGuideOverlay()
        engine = self.service.engine
        kind, slot = key
        coords = []
        if kind == "palette":
            coords = engine.manual_palette_coords or []
        elif kind == "layers":
            coords = engine.layer_capture_points()
        elif kind == "extra":
            coords = [(ev["x"], ev["y"]) for ev in engine._serialize_actions(slot or "pre", include_draft=True)
                      if isinstance(ev, dict) and ev.get("device") == "mouse"
                      and ev.get("x") is not None and ev.get("y") is not None]
        points = []
        for i, coordinate in enumerate(coords):
            if isinstance(coordinate, dict) and "x" in coordinate and "y" in coordinate:
                x, y = coordinate["x"], coordinate["y"]
            elif isinstance(coordinate, (list, tuple)) and len(coordinate) >= 2:
                x, y = coordinate[:2]
            else:
                continue
            points.append({"x": int(x), "y": int(y), "label": str(i + 1)})
        try:
            bindings = self.service.get_hotkeys().get("global", {})
        except Exception:
            bindings = {}
        title = capture_hint(kind, slot, bindings)
        self.guide.set_state(title, payload.get("count") or 0, points, compact_marks=kind == "palette")
        if not self.guide.isVisible():
            self.guide.show()
            self.guide.raise_()

    def capture_screen_image(self, on_image, *, grab=None, origin=None):
        """«Снимок экрана»: freeze the desktop, then the user frames a part of it.

        The frame is cut from the frozen picture, so neither the dimmed picker
        nor the returning main window can end up in the result.
        """
        if not self.prepare("pick"):
            return
        # The main window has just been hidden: give the desktop a moment to repaint.
        QTimer.singleShot(250, lambda: self._snapshot_frozen(on_image, grab, origin))

    def _snapshot_frozen(self, on_image, grab, origin):
        if self._closing or self.mode != "pick" or self.picker is not None:
            return
        from infrastructure.screen_capture import capture_screen, virtual_screen_origin
        try:
            frozen = (grab or capture_screen)()
            left, top = (origin or virtual_screen_origin)()
        except Exception as error:
            self._set_mode("")
            if self.service is not None:
                self.service.statusChanged.emit("error: Не удалось сделать снимок экрана: " + str(error))
            return

        def framed(region):
            x, y, w, h = (int(v) for v in region)
            box = (x - left, y - top, x - left + w, y - top + h)
            if box[0] < 0 or box[1] < 0 or box[2] > frozen.width or box[3] > frozen.height:
                box = (max(0, box[0]), max(0, box[1]), min(frozen.width, box[2]), min(frozen.height, box[3]))
            if box[2] - box[0] < 4 or box[3] - box[1] < 4:
                return
            on_image(frozen.crop(box).convert("RGBA"))

        def cancelled():
            if self.service is not None and not self._closing:
                self.service.statusChanged.emit(localized("Снимок экрана отменён", "Screenshot cancelled"))

        self._open_picker(RegionPickOverlay,
                          localized("Выделите рамкой то, что станет картинкой", "Drag a frame over what becomes the picture"),
                          framed, cancelled)

    def show_calibration(self, items, hint=""):
        if not self.prepare("inspect"):
            return
        self.flash = CalibFlashOverlay(items, hint, 6000)
        self.flash.closed.connect(self._flash_closed)
        self.flash.show()
        self.flash.raise_()
        self.flash.activateWindow()

    def _flash_closed(self):
        self.flash = None

    def _open_picker(self, factory, *args):
        if not self.prepare("pick"):
            return
        self.picker = factory(*args)
        self.picker.finished.connect(self._picker_finished)
        if self.service is not None:
            self.picker.failed.connect(self.service.statusChanged.emit)
        try:
            self.picker.open()
        except Exception as error:
            self._close_transient()
            self._set_mode("")
            if self.service is not None:
                self.service.statusChanged.emit(str(error))

    def _picker_finished(self):
        picker = self.sender()
        if picker is self.picker:
            self.picker = None
            picker.deleteLater()
            if not self._switching and self._palette_job is None and getattr(self, "_wheel_job", None) is None:
                self._set_mode("")

    def _measure_palette(self, regions):
        try:
            job = PaletteCalibrationJob(self.service, regions, self)
            self._palette_job = job
            job.finished.connect(lambda: self._palette_finished(job))
            job.start()
            self._set_mode("measure")
        except Exception as error:
            if self._palette_job is not None:
                self._palette_job.cancel()
                self._palette_job.deleteLater()
                self._palette_job = None
            self.service.statusChanged.emit("error: " + str(error))

    def _measure_wheel(self, region):
        job = WheelCalibrationJob(self.service, region, parent=self)
        self._wheel_job = job
        job.finished.connect(lambda: self._wheel_finished(job))
        job.start()

    def _wheel_finished(self, job):
        # The screen is measured with the main window still hidden ("pick"): it would
        # cover the wheel. Only now the window may come back.
        if getattr(self, "_wheel_job", None) is job:
            self._wheel_job = None
            if self.picker is None and self.mode == "pick" and not self._closing:
                self._set_mode("")
        job.deleteLater()

    def _palette_finished(self, job):
        if self._palette_job is job:
            self._palette_job = None
            if self.picker is None and self.mode in ("pick", "measure"):
                self._set_mode("")
        job.deleteLater()

    def calibrate(self, kind):
        service = self.service
        engine = service.engine
        if getattr(service, "_is_drawing", False) or getattr(engine, "drawing_enabled", False):
            service.statusChanged.emit(localized("Калибровка недоступна во время рисования", "Cannot calibrate while drawing"))
            return

        def cancelled():
            if not self._closing:
                service.statusChanged.emit(localized("Калибровка отменена", "Calibration cancelled"))

        def done(value):
            if kind == "ring":
                (cx, cy), (rx, ry) = value
                radius = math.hypot(rx - cx, ry - cy)
                if radius < 5:
                    service.statusChanged.emit(localized("Радиус круга слишком мал — повторите калибровку", "Circle radius too small — try again"))
                    return
                engine.circle_params_calib = (int(cx), int(cy), radius, math.atan2(cy - ry, rx - cx))
            else:
                x, y, w, h = value
                engine.slider_params_calib = (("vertical", x + w / 2.0, float(y), float(y + h)) if h >= w
                                              else ("horizontal", y + h / 2.0, float(x + w), float(x)))
            service._emit_session_state_changed("hsv-calibration", sections=("painter",))
            service.statusChanged.emit(localized("Калибровка сохранена", "Calibration saved"))

        if kind == "ring":
            self._open_picker(TwoPointPickOverlay,
                              localized("Шаг 1 из 2 — центр цветового круга", "Step 1 of 2 — center of the color ring"),
                              localized("Шаг 2 из 2 — красная точка на окружности", "Step 2 of 2 — red point on the ring"), done, cancelled)
        elif kind == "slider":
            self._open_picker(RegionPickOverlay, localized("Выделите ползунок яркости рамкой", "Drag a box over the brightness slider"), done, cancelled)
        elif kind == "palette":
            self._open_picker(PaletteRegionPickOverlay, self._measure_palette, cancelled)
        elif kind == "wheel":
            self._open_picker(RegionPickOverlay,
                              localized("Обведите рамкой цветовое колесо целиком: кольцо и квадрат внутри",
                                        "Drag a frame around the whole colour wheel: the ring and the square inside"),
                              self._measure_wheel, cancelled)
        elif kind == "alpha":
            from application.color_mixing import alpha_slider_from_points
            previous = engine.alpha_slider_params
            method = engine.color_picking_method
            def save_alpha(points):
                try:
                    if (self._closing or getattr(service, "_is_shutting_down", False)
                            or engine.alpha_slider_params != previous or engine.color_picking_method != method):
                        raise ValueError("Настройки изменились. Повторите калибровку непрозрачности.")
                    params = alpha_slider_from_points(points)
                    engine.alpha_slider_params = params
                    engine._manual_mix_warned_alpha = False
                    service._emit_session_state_changed("alpha-calibration", sections=("painter",))
                    service.statusChanged.emit("Ползунок непрозрачности сохранён.")
                except Exception as error:
                    service.statusChanged.emit("error: " + str(error))
            self._open_picker(AlphaSliderPickOverlay, save_alpha, cancelled)
        else:
            raise ValueError(f"Unknown screen calibration: {kind}")

    def calibrate_brush(self, payload):
        if self._closing:
            return
        kind, value = payload["kind"], payload.get("value")
        hints = {"text": "Укажите поле размера кисти", "point": "Укажите кнопку выбранного размера",
                 "slider": "Выделите ползунок размера кисти рамкой",
                 "scratch": "Выделите свободную тестовую зону — здесь будут пробные мазки"}

        def done(coordinates):
            try:
                self.service.apply_brush_capture(kind, coordinates, value)
                self.service.statusChanged.emit("Калибровка кисти сохранена.")
            except Exception as error:
                self.service.statusChanged.emit("error: " + str(error))

        self._open_picker(RegionPickOverlay if kind in ("slider", "scratch") else PointPickOverlay,
                          hints[kind], done, lambda: self.service.statusChanged.emit("Калибровка кисти отменена."))

    def shutdown(self):
        if self._closing:
            return
        if not self.prepare("shutdown"):
            raise RuntimeError("Не удалось завершить экранный захват. Повторите закрытие.")
        self._closing = True
        self.bind_service(None)
        if self.guide is not None:
            self.guide.close()
            self.guide.deleteLater()
            self.guide = None
