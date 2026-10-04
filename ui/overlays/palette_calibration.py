"""Qt owner of a transactional, cancellable window-palette measurement."""
from PySide6.QtCore import QObject, QTimer, Signal
import numpy as np

from infrastructure.palette_sampling import PaletteSampleRequest, sample_window_palette
from infrastructure.window_sampling import WindowSampleAPI


class PaletteCalibrationJob(QObject):
    finished = Signal()

    def __init__(self, service, regions, parent=None):
        super().__init__(parent)
        self.service = service
        self.engine = service.engine
        self.previous = self.engine.screen_palette_calib
        self.method = self.engine.color_picking_method
        self.request = PaletteSampleRequest.from_regions(*regions)
        self.result = None
        self.error = None
        self.thread = None
        self.session = None
        self.timer = QTimer(self)
        self.timer.setInterval(20)
        self.timer.timeout.connect(self._poll)

    def start(self):
        self.engine._begin_capture_session("измерение палитры окна")
        self.session = self.engine._capture_session
        try:
            self.thread = self.session.start_worker(self._run)
        except Exception:
            self.session.close()
            raise
        self.timer.start()
        self.service.statusChanged.emit("Измеряем палитру… Остановка отменит измерение и сохранит прежние настройки.")

    def _run(self):
        try:
            samples = sample_window_palette(self.request, self.session.cancelled)
            # Reject a moved/replaced target even after its child process exits.
            self.request.validate(WindowSampleAPI())
            self.result = dict(lab=self.engine._srgb_to_lab_array(np.asarray(samples["rgb"])).astype(np.float32),
                               coords=np.asarray(samples["coords"], dtype=np.int32),
                               slider=tuple(samples["slider"]) if samples["slider"] else None)
        except Exception as error:
            self.error = str(error)

    def cancel(self):
        if self.session is not None:
            self.session.close()

    def _poll(self):
        if self.thread.is_alive():
            return
        self.timer.stop()
        try:
            if self.session.closed:
                return
            if (self.engine._capture_session is not self.session
                    or self.engine.screen_palette_calib is not self.previous
                    or self.engine.color_picking_method != self.method
                    or getattr(self.service, "_is_shutting_down", False)):
                self.error = "Настройки изменились во время измерения. Повторите калибровку."
            if not self.error:
                try:
                    self.request.validate(WindowSampleAPI())
                except Exception as error:
                    self.error = str(error)
            if self.error:
                self.service.statusChanged.emit("error: " + self.error)
            elif self.result is not None:
                def commit():
                    self.engine.screen_palette_calib = self.result
                    self.service._emit_session_state_changed("screen-palette-calibration", sections=("painter",))
                    # The number of grid samples is not a number of colours (2020 points were 20 swatches).
                    self.service.statusChanged.emit("Палитра сохранена. Проверить её можно кнопкой «Показать калибровку».")
                self.session.publish(commit)
        finally:
            self.session.close()
            self.finished.emit()
