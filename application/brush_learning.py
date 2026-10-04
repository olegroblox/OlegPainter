"""An owned calibration run: snapshot in, validated profile out, no Qt access."""
from copy import deepcopy
from queue import SimpleQueue
from threading import Event, Thread

from engine.olegpainter.core import OlegPainter
from infrastructure.automation_transport import InputCancelled


class BrushLearningJob:
    def __init__(self, config, mode="brush"):
        self.mode = mode  # "brush": sizes + pacing; "speed": pacing only, no size control needed
        self.config = deepcopy(config)
        self.config.pop("IMAGE_PATH", None)
        self.config.pop("source_is_clipboard_placeholder", None)
        self.cancelled = Event()
        self.messages = SimpleQueue()
        self.profile = None
        self.slider_params = None
        self.timing = None
        self.error = ""
        self.last_message = ""
        self.thread = Thread(target=self.run, name="OlegPainter brush learning")

    def _status(self, message):
        self.last_message = str(message)
        self.messages.put(self.last_message)

    def _learn_timing(self, engine):
        """How fast the program accepts pointer turns, measured with the smallest
        size (the one that paints edges and details). Optional: a failure keeps
        the learned brush and the user's pacing."""
        from engine.olegpainter.input_timing import learn
        try:
            table = engine._dynamic_brush_v2_radius_table()
            radius = float(table[0][0]) if table is not None else 1.0
            if table is not None and not engine._apply_dynamic_brush_value(float(table[1][0]), force=True):
                return None
            return learn(engine, engine.dynamic_brush_scratch_zone, brush_radius_px=radius, status=self._status)
        except InputCancelled:
            raise
        except Exception as error:
            self._status(f"Скорость ввода не подобрана: {error}")
            return None

    def _learn_speed(self, engine):
        """Pacing without the brush size control: the stroke gap in straight-stroke
        mode, otherwise the turn and lift pauses."""
        from engine.olegpainter.input_timing import learn, learn_stroke_gap
        radius = max(1.0, float(engine.brush_size) / 2 + 0.5)
        zone = engine.dynamic_brush_scratch_zone
        if engine.pen_split_strokes:
            return learn_stroke_gap(engine, zone, brush_radius_px=radius, status=self._status)
        return learn(engine, zone, brush_radius_px=radius, status=self._status)

    def run(self):
        engine = None
        try:
            engine = OlegPainter(status_callback=self._status)
            engine._drawing_cancel = self.cancelled
            if not engine.load_config(self.config):
                raise ValueError("Не удалось прочитать настройки обучения кисти.")
            if self.cancelled.is_set():
                return
            if self.mode == "speed":
                with engine._owned_automation_input("проба скорости", target_region=engine.dynamic_brush_scratch_zone):
                    self.timing = self._learn_speed(engine)
                    if self.timing is None and not self.cancelled.is_set():
                        self.error = ("Пробные штрихи не видны или ни одна пауза не прошла: выберите в программе "
                                      "тёмный цвет и чистое место для проб.")
                return
            with engine._owned_automation_input("обучение кисти", target_region=engine.dynamic_brush_scratch_zone):
                if not engine.learn_dynamic_brush_profile():
                    if not self.cancelled.is_set():
                        self.error = self.last_message or "Кисть не прошла проверку. Проверьте регулятор и свободное место в тестовой зоне."
                    return
                if not self.cancelled.is_set():
                    self.profile = deepcopy(engine.dynamic_brush_profile)
                    # learning may have swapped the ends of a reversed slider
                    self.slider_params = deepcopy(engine.dynamic_brush_slider_params)
                    self.timing = self._learn_timing(engine)
        except InputCancelled:
            self.profile = None
        except Exception as error:
            self.error = str(error)
            self.profile = None
