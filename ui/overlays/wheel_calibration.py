"""Measure a framed hue ring + square picker on the screen (WHEEL-001).

Runs on the Qt thread in short steps: the frame overlay must be gone from the
screen before the picture is taken, and a game can deliver a frame without its
UI — such a picture shows no ring and is simply taken again.
"""
from __future__ import annotations

import logging

import numpy as np
from PySide6.QtCore import QObject, QTimer, Signal

from engine.olegpainter import wheel_picker
from infrastructure.screen_capture import capture_screen

log = logging.getLogger(__name__)

ATTEMPTS = 8
INTERVAL_MS = 120


class WheelCalibrationJob(QObject):
    finished = Signal()

    def __init__(self, service, region, *, grab=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.region = tuple(int(v) for v in region)          # x, y, w, h (physical px)
        self._grab = grab or capture_screen
        self._attempt = 0
        self._error = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._step)

    def start(self):
        self._timer.start(250)                             # let the frame overlay disappear first

    def cancel(self):
        self._timer.stop()
        self.finished.emit()

    def _step(self):
        x, y, w, h = self.region
        self._attempt += 1
        try:
            picture = np.asarray(self._grab(bbox=(x, y, x + w, y + h)).convert("RGB"))
            calib = wheel_picker.analyse(picture, origin=(x, y))
        except ValueError as error:
            self._error = str(error)
            if self._attempt < ATTEMPTS:
                self._timer.start(INTERVAL_MS)
                return
            self.service.statusChanged.emit("error: " + self._error)
            self.finished.emit()
            return
        except Exception as error:                          # capture failure: say so, keep old calibration
            log.warning("wheel calibration failed", exc_info=True)
            self.service.statusChanged.emit("error: Не удалось снять колесо с экрана: " + str(error))
            self.finished.emit()
            return
        engine = self.service.engine
        engine.wheel_square_calib = calib
        engine._wheel_last_square = None
        self.service._emit_session_state_changed("wheel-calibration", sections=("painter",))
        self.service.statusChanged.emit("Цветовое колесо откалибровано. Проверить можно кнопкой «Показать калибровку».")
        self.finished.emit()
