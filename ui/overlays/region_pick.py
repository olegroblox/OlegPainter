"""Screen pickers. Global listeners deliver physical points to the Qt event queue."""
from __future__ import annotations

import logging
import math
from queue import SimpleQueue, Empty

from PySide6.QtCore import QPoint, QRect, Qt, QTimer, Signal
from PySide6.QtGui import QPainter, QPen

from ui.i18n import i18n, localized
from .pointer_surface import PointerSurface
from .theme import ACCENT, ACCENT_SOFT, DIM, draw_banner, draw_crosshair, draw_label
from infrastructure.capture_session import CaptureSession

log = logging.getLogger("olegpainter.overlays.region_pick")

class _BasePickOverlay(PointerSurface):
    finished = Signal()
    failed = Signal(str)

    def __init__(self, on_done, on_cancel):
        # An interactive picker is the active task while the main window is
        # hidden. Keep it discoverable as a window, unlike a passive HUD/tool.
        super().__init__(window_kind=Qt.Window)
        self._on_done = on_done
        self._on_cancel = on_cancel
        self._closed = False
        self._pending = SimpleQueue()
        self._first_phys = None
        self._mouse_listener = None
        self._key_listener = None
        self._capture_session = None
        self._timeout = QTimer(self)
        self._timeout.setSingleShot(True)
        self._timeout.setInterval(180_000)
        self._timeout.timeout.connect(self.cancel)
        # Selecting a test area must not paint a drag into the target canvas.
        # Global hooks still provide physical coordinates across DPI boundaries.
        self.set_passthrough(False)

    def _set_hint(self, hint):
        self._hint = str(hint)
        # The main window is hidden during capture. Give the remaining native
        # window an identity for accessibility and desktop window selection.
        self.setWindowTitle(f"OlegPainter — {self._hint}")
        self.setAccessibleName(self._hint)
        self.setAccessibleDescription(
            "ПКМ / Esc — отмена" if i18n.current_language() == "ru"
            else "Right click / Esc — cancel"
        )

    def cancel(self):
        self._finish(cancelled=True)

    def closeEvent(self, event):
        self.cancel()
        super().closeEvent(event)

    def hideEvent(self, event):
        self.cancel()
        super().hideEvent(event)

    # --------------------------------------------------------------- listeners
    def open(self) -> None:
        self._capture_session = CaptureSession("выбор экранных координат", on_error=self._capture_failed)
        self.show()
        self.raise_()
        self._timeout.start()
        self._start_listeners()

    def _start_listeners(self) -> None:
        if self._capture_session is None:
            self._capture_session = CaptureSession("выбор экранных координат", on_error=self._capture_failed)
        try:
            from pynput import mouse, keyboard
        except Exception as error:
            log.exception("pynput unavailable — pick overlay cannot capture clicks")
            self._finish(cancelled=True)
            self.failed.emit("Не удалось открыть захват ввода: " + str(error))
            return

        def on_click(x, y, button, pressed):
            name = getattr(button, "name", str(button))
            if name == "right" and pressed:
                self._pending.put(("cancel", None))
                return
            if name != "left":
                return
            # Windows pynput listeners report physical pixels; keep the event position.
            self._pending.put(("down" if pressed else "up", (int(x), int(y))))

        def on_key(key):
            if key == keyboard.Key.esc:
                self._pending.put(("cancel", None))
            elif key == keyboard.Key.enter:
                self._pending.put(("accept", None))

        try:
            self._mouse_listener = self._capture_session.listener(mouse.Listener, on_click=on_click)
            self._mouse_listener.start()
            self._key_listener = self._capture_session.listener(keyboard.Listener, on_press=on_key)
            self._key_listener.start()
        except Exception as error:
            log.exception("failed to start pick listeners")
            self._finish(cancelled=True)
            self.failed.emit("Не удалось открыть захват ввода: " + str(error))

    def _capture_failed(self, error):
        # Native hook callbacks cannot mutate Qt widgets.
        self._pending.put(("error", str(error)))

    def _stop_listeners(self) -> None:
        if self._capture_session is not None:
            self._capture_session.close()
        for lst in (self._mouse_listener, self._key_listener):
            try:
                if lst is not None:
                    lst.stop()
            except Exception:
                pass
        self._mouse_listener = self._key_listener = None

    def _finish(self, *, cancelled: bool, result=None) -> None:
        if self._closed:
            return
        self._closed = True
        self._timeout.stop()
        try:
            self._stop_listeners()
        except Exception:
            # Keep the picker and its lease recoverable by another cancellation.
            self._closed = False
            raise
        self.close()
        try:
            if cancelled:
                if self._on_cancel:
                    self._on_cancel()
            elif self._on_done:
                self._on_done(result)
        except Exception:
            log.exception("pick overlay callback failed")
        finally:
            self.finished.emit()

    def _tick(self) -> None:
        if self._closed or not self.isVisible():
            return
        changed = False
        while True:
            try:
                kind, data = self._pending.get_nowait()
            except Empty:
                break
            if kind == "cancel":
                self.cancel()
                return
            if kind == "error":
                self.cancel()
                self.failed.emit("Ошибка захвата ввода: " + data)
                return
            self._handle_event(kind, data)
            if self._closed:
                return
            changed = True
        before = self._cursor
        super()._tick()
        if changed and before == self._cursor:
            self.update()

    def _handle_event(self, kind: str, phys: tuple[int, int] | None) -> None:  # noqa: D401
        raise NotImplementedError

    # ------------------------------------------------------------------- draw
    def _draw_dim(self, p):
        p.fillRect(self.rect(), DIM)

    def _draw_crosshair(self, p, pos):
        draw_crosshair(p, self.active_screen_rect(), pos)

    def _draw_banner(self, p, text):
        cancel = "ПКМ / Esc — отмена" if i18n.current_language() == "ru" else "Right click / Esc — cancel"
        draw_banner(p, self.active_screen_rect(), text, cancel)

    def _draw_size_label(self, p, anchor, text):
        draw_label(p, self.active_screen_rect(), anchor + QPoint(18, 18), text)

    def _coordinate_label(self, p, pos, detail=""):
        x, y = self.coordinates.to_physical(self._cursor.x(), self._cursor.y())
        self._draw_size_label(p, pos, f"X {x}   Y {y} px" + (f"  ·  {detail}" if detail else ""))

    def _first_logical(self) -> QPoint | None:
        if self._first_phys is None:
            return None
        lx, ly = self.coordinates.to_logical(*self._first_phys)
        off = self.geometry().topLeft()
        return QPoint(int(lx - off.x()), int(ly - off.y()))


class PointPickOverlay(_BasePickOverlay):
    def __init__(self, hint, on_done, on_cancel=None):
        super().__init__(on_done, on_cancel)
        self._set_hint(hint)

    def _handle_event(self, kind, phys):
        # Close on release: the target must not receive half of this click.
        if kind == "down" and phys is not None:
            self._first_phys = tuple(phys)
        elif kind == "up" and self._first_phys is not None:
            self._finish(cancelled=False, result=self._first_phys)

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._draw_dim(p)
        pos = self.mapFromGlobal(self._cursor)
        self._draw_crosshair(p, pos)
        self._coordinate_label(p, pos)
        self._draw_banner(p, self._hint)
        p.end()


class TwoPointPickOverlay(_BasePickOverlay):
    """Центр круга → красная точка. on_done: ((x1,y1),(x2,y2)) физ. px."""

    def __init__(self, hint_first: str, hint_second: str, on_done, on_cancel=None):
        super().__init__(on_done, on_cancel)
        self._set_hint(hint_first)
        self._hint2 = hint_second

    def _handle_event(self, kind: str, phys) -> None:
        if kind != "down" or phys is None:
            return
        if self._first_phys is None:
            self._first_phys = (int(phys[0]), int(phys[1]))
        else:
            self._finish(cancelled=False, result=(self._first_phys, (int(phys[0]), int(phys[1]))))

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_dim(p)
        pos = self.mapFromGlobal(self._cursor)
        self._draw_crosshair(p, pos)
        c = self._first_logical()
        if c is None:
            self._coordinate_label(p, pos)
            self._draw_banner(p, self._hint)
        else:
            p.setPen(QPen(ACCENT, 2)); p.setBrush(ACCENT)
            p.drawEllipse(c, 4, 4)
            p.setBrush(Qt.BrushStyle.NoBrush)
            r = int(math.hypot(pos.x() - c.x(), pos.y() - c.y()))
            if r > 2:
                p.drawEllipse(c, r, r)
                p.setPen(QPen(ACCENT_SOFT, 1)); p.drawLine(c, pos)
                current = self.coordinates.to_physical(self._cursor.x(), self._cursor.y())
                radius = math.dist(self._first_phys, current)
                self._coordinate_label(p, pos, f"R {radius:.0f} px")
            self._draw_banner(p, self._hint2)
        p.end()


class AlphaSliderPickOverlay(_BasePickOverlay):
    """Two endpoint clicks; keep the input lease through both releases."""

    def __init__(self, on_done, on_cancel=None):
        super().__init__(on_done, on_cancel)
        self._pressed_phys = None
        self._set_hint(localized("Шаг 1 из 2 — точка 0% непрозрачности ползунка",
                                 "Step 1 of 2 — the 0% opacity point of the slider"))

    def _handle_event(self, kind, phys):
        if kind == "down" and phys is not None:
            self._pressed_phys = tuple(phys)
        elif kind == "up" and self._pressed_phys is not None:
            point, self._pressed_phys = self._pressed_phys, None
            if self._first_phys is None:
                self._first_phys = point
                self._set_hint(localized("Шаг 2 из 2 — точка 100% непрозрачности того же ползунка",
                                         "Step 2 of 2 — the 100% opacity point of the same slider"))
            else:
                self._finish(cancelled=False, result=(self._first_phys, point))

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        self._draw_dim(p)
        pos = self.mapFromGlobal(self._cursor)
        self._draw_crosshair(p, pos)
        first = self._first_logical()
        if first is not None:
            p.setPen(QPen(ACCENT, 2))
            p.drawLine(first, pos)
            p.setBrush(ACCENT)
            p.drawEllipse(first, 4, 4)
            self._draw_size_label(p, first, "0%")
        self._coordinate_label(p, pos)
        self._draw_banner(p, self._hint)
        p.end()


class RegionPickOverlay(_BasePickOverlay):
    """Зажать → отпустить: прямоугольник. on_done: (x, y, w, h) физ. px."""

    def __init__(self, hint: str, on_done, on_cancel=None):
        super().__init__(on_done, on_cancel)
        self._set_hint(hint)

    def _handle_event(self, kind: str, phys) -> None:
        if phys is None:
            return
        if kind == "down":
            self._first_phys = (int(phys[0]), int(phys[1]))
        elif kind == "up" and self._first_phys is not None:
            x1, y1 = self._first_phys
            x2, y2 = int(phys[0]), int(phys[1])
            w, h = abs(x2 - x1), abs(y2 - y1)
            if w < 4 or h < 4:
                self._first_phys = None  # слишком мелко — ждём новую попытку
                return
            self._accept_region((min(x1, x2), min(y1, y2), w, h))

    def _accept_region(self, region):
        self._finish(cancelled=False, result=region)

    def paintEvent(self, event) -> None:  # noqa: N802
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        self._draw_dim(p)
        pos = self.mapFromGlobal(self._cursor)
        c = self._first_logical()
        if c is None:
            self._coordinate_label(p, pos)
            self._draw_crosshair(p, pos)
            self._draw_banner(p, self._hint)
        else:
            rect = QRect(c, pos).normalized()
            p.save()
            p.setCompositionMode(QPainter.CompositionMode.CompositionMode_Clear)
            p.fillRect(rect, Qt.GlobalColor.transparent)
            p.restore()
            p.setPen(QPen(ACCENT, 2)); p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(rect)
            current = self.coordinates.to_physical(self._cursor.x(), self._cursor.y())
            w, h = (abs(current[i] - self._first_phys[i]) for i in (0, 1))
            self._coordinate_label(p, pos, f"{w} × {h} px")
            self._draw_banner(p, self._hint)
        p.end()


class PaletteRegionPickOverlay(RegionPickOverlay):
    """Two regions in one input session; Enter explicitly skips only the slider."""

    def __init__(self, on_done, on_cancel=None):
        super().__init__(localized("Шаг 1 из 2 — выделите цветную область палитры",
                                   "Step 1 of 2 — drag a frame over the palette colors"), on_done, on_cancel)
        self._palette_region = None

    def _accept_region(self, region):
        if self._palette_region is None:
            self._palette_region = region
            self._first_phys = None
            self._set_hint(localized("Шаг 2 из 2 — выделите яркость; Enter — палитра без ползунка",
                                     "Step 2 of 2 — frame the brightness slider; Enter — palette without it"))
            self.update()
        else:
            self._finish(cancelled=False, result=(self._palette_region, region))

    def _handle_event(self, kind, phys):
        if kind == "accept" and self._palette_region is not None and self._first_phys is None:
            self._finish(cancelled=False, result=(self._palette_region, None))
        else:
            super()._handle_event(kind, phys)
