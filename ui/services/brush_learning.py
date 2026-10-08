"""Qt ownership and cancellation of the independent brush calibration job."""
from copy import deepcopy
import math
from queue import Empty

from PySide6.QtCore import QTimer

from application.brush_learning import BrushLearningJob


class BrushLearningMixin:
    # Measured lift pause from which routing through own colour pays off.
    _BRIDGE_LIFT_PAUSE = 0.02
    def _reject_input_during_brush_learning(self):
        if self.brush_learning_active:
            self.statusChanged.emit("Дождитесь завершения обучения кисти или остановите его.")
            return True
        return False

    def _guard_brush_edit(self, *, capture_result=False):
        if (self._is_shutting_down or self._close_pending or self.brush_learning_active
                or self._is_drawing or self._worker_thread is not None or self._stop_pending
                or self._hotkey_capture_active or self._compute_capture_state()["active"]
                or (self.desktop_interaction and not (capture_result and self.desktop_interaction == "pick"))):
            raise RuntimeError("Сначала завершите текущую операцию.")

    def _invalidate_brush_profile(self):
        engine = self.engine
        engine.dynamic_brush_profile = None
        engine.dynamic_brush_calibration = None
        engine.dynamic_brush_calibration_updated_at = None
        engine.dynamic_brush_session_active = False
        engine.dynamic_brush_runtime_state = "needs_learning"
        engine._last_dynamic_brush_value = None
        engine._update_dynamic_brush_profile_state()

    def update_brush_settings(self, patch):
        self._guard_brush_edit()
        allowed = {"enabled", "control_mode", "drag_enabled", "verify_at_draw", "min_value", "max_value", "default_value",
                   "step_value", "text_auto"}
        if not isinstance(patch, dict) or set(patch) - allowed:
            raise ValueError("Неизвестная настройка кисти.")
        before = self.engine.get_dynamic_brush_settings()
        values = {key: before[key] for key in allowed}
        values.update(patch)
        if values["control_mode"] not in ("text", "slider", "points"):
            raise ValueError("Выберите поле размера, ползунок или кнопки размеров.")
        for key in ("enabled", "drag_enabled", "verify_at_draw", "text_auto"):
            if not isinstance(values[key], bool):
                raise ValueError("Переключатель кисти должен быть включён или выключен.")
        for key in ("min_value", "max_value", "default_value", "step_value"):
            value = values[key]
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
                raise ValueError("Размеры кисти должны быть конечными числами.")
        # Mode changes expose the new range editor even when a slider's zero
        # minimum is not usable in a text field. Editing or learning that text
        # range still requires a positive minimum; never silently clamp it.
        editing_range = bool(set(patch) & {"min_value", "max_value", "default_value", "step_value"})
        floor = .001 if values["control_mode"] == "text" and editing_range else 0
        if (not floor <= values["min_value"] < values["max_value"] <= 9999
                or not values["min_value"] <= values["default_value"] <= values["max_value"]
                or not 0.0001 <= values["step_value"] <= values["max_value"] - values["min_value"]):
            raise ValueError("Проверьте диапазон размеров, исходный размер и шаг кисти.")
        changed = {key for key in patch if before[key] != values[key]}
        if not changed:
            return
        if changed - {"enabled", "verify_at_draw"}:
            self._invalidate_brush_profile()
        for key in changed:
            setattr(self.engine, "dynamic_brush_" + key, values[key])
        self.engine._update_dynamic_brush_profile_state()
        self._brush_learning_message = self._brush_learning_error = ""
        self.brushLearningChanged.emit()
        self._emit_dynamic_brush_settings_changed()

    def _apply_input_timing(self, timing):
        """Measured pacing replaces hand-tuned pauses: a dwell on every turn,
        vertices only when the program connects points itself, small pen steps
        when it only stamps where it samples the cursor."""
        engine = self.engine
        engine.input_timing_measured = True
        engine.draw_delay = float(timing["draw_delay"])
        engine.pen_max_step = int(timing["pen_max_step"])
        engine.area_fill_delay = 0.0
        engine.target_connects_points = bool(timing.get("interpolates"))
        engine.pen_press_nudge = bool(timing.get("press_nudge", False))
        if timing.get("interpolates"):
            engine.run_length_merge_enabled = True
        lift = timing.get("lift_pause")
        if lift is not None:
            # One measured pause replaces the three hand-tuned ones around a lift.
            engine.pen_settle_delay = engine.mouse_release_settle = float(lift)
            engine.pen_button_delay = 0.0
            # Bridges trade lifts for retraced moves; with a cheap lift they lost
            # (live Paint at 2 ms: Spongebob 21 -> 33 s, mosaic 34 -> 84 s).
            engine.astar_bridge_enabled = float(lift) >= self._BRIDGE_LIFT_PAUSE
        self._input_timing_note = (
            f" Скорость подобрана: пауза на повороте {engine.draw_delay * 1000:.1f} мс"
            + (f", на отрыве пера {float(lift) * 1000:.1f} мс" if lift is not None else "")
            + (", курсор подводится перед нажатием (игра читает мышь раз в кадр)" if timing.get("press_nudge") else "")
            + (", программа сама соединяет точки линией — автоматическая кисть рисует размером деталей,"
               " крупные мазки здесь не быстрее." if timing.get("interpolates")
               else f", программа не соединяет точки — шаг пера {engine.pen_max_step} px,"
                    " автоматическая кисть использует крупные мазки."))
        self._emit_session_state_changed("input-timing", sections=("painter",))

    def request_brush_capture(self, kind, value=None):
        self._guard_brush_edit()
        if kind not in ("text", "slider", "point", "scratch"):
            raise ValueError("Неизвестный инструмент калибровки кисти.")
        if kind == "point" and (isinstance(value, bool) or not isinstance(value, (int, float))
                                or not math.isfinite(value) or not 0 <= value <= 9999):
            raise ValueError("Размер кнопки должен быть числом от 0 до 9999.")
        if not getattr(self, "_brush_capture_available", False):
            raise RuntimeError("Экранные инструменты не подключены.")
        self.brushCalibrationRequested.emit(dict(kind=kind, value=value))

    def apply_brush_capture(self, kind, coordinates, value=None):
        self._guard_brush_edit(capture_result=True)
        engine = self.engine
        if kind in ("scratch", "slider"):
            x, y, w, h = self._brush_rect(coordinates)
            if kind == "scratch":
                engine.dynamic_brush_scratch_zone = (x, y, w, h)
            else:
                engine.dynamic_brush_slider_params = engine.refine_slider_params(
                    ("vertical", x + w / 2, y, y + h) if h >= w else ("horizontal", y + h / 2, x + w, x))
                engine.dynamic_brush_control_mode = "slider"
        elif kind in ("text", "point"):
            if (not isinstance(coordinates, (tuple, list)) or len(coordinates) != 2
                    or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in coordinates)):
                raise ValueError("Некорректная координата регулятора кисти.")
            x, y = map(int, coordinates)
            if kind == "text":
                engine.dynamic_brush_coord = (x, y)
                engine.dynamic_brush_control_mode = "text"
            else:
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 9999:
                    raise ValueError("Некорректный размер кисти.")
                points = [dict(p) for p in engine.dynamic_brush_points or [] if p["value"] != value]
                points.append(dict(x=x, y=y, value=float(value)))
                engine.dynamic_brush_points = sorted(points, key=lambda p: p["value"])
                engine.dynamic_brush_control_mode = "points"
        else:
            raise ValueError("Неизвестный инструмент калибровки кисти.")
        self._invalidate_brush_profile()
        self._brush_learning_message = self._brush_learning_error = ""
        self.brushLearningChanged.emit()
        self._emit_dynamic_brush_settings_changed()
        self.brushCaptureApplied.emit(kind)

    @staticmethod
    def _brush_rect(value):
        if (not isinstance(value, (list, tuple)) or len(value) != 4
                or any(isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) for v in value)
                or value[2] < 4 or value[3] < 4):
            raise ValueError("Задайте корректную прямоугольную область размером не меньше 4 × 4 пикселей.")
        return tuple(int(v) for v in value)

    @property
    def brush_learning_active(self):
        return getattr(self, "_brush_job", None) is not None

    def brush_learning_snapshot(self):
        return dict(active=self.brush_learning_active,
                    cancelling=self.brush_learning_active and self._brush_job.cancelled.is_set(),
                    message=getattr(self, "_brush_learning_message", ""),
                    error=getattr(self, "_brush_learning_error", ""))

    def start_speed_learning(self):
        """Measure the input pacing in the test zone without a brush size control."""
        return self.start_brush_learning(mode="speed")

    def brush_learning_blocker(self, mode="brush"):
        """Why learning (`brush`) or the speed probe (`speed`) cannot start now, in
        the user's words; "" when it can. The window asks before it steps aside:
        minimised for an error, it hid the very message (2026-10-05)."""
        problem = self._brush_learning_problem(mode)
        return problem[1] if problem else ""

    def _brush_learning_problem(self, mode):
        """(exception type, message) or None: a busy program is a RuntimeError, a
        missing setup a ValueError."""
        if self.brush_learning_active:
            return RuntimeError, "Обучение кисти уже выполняется."
        if (self._is_shutting_down or self._close_pending or self._is_drawing
                or self._worker_thread is not None or self._stop_pending
                or self._compute_capture_state()["active"] or self.desktop_interaction or self._hotkey_capture_active):
            return RuntimeError, "Сначала завершите текущую операцию."
        if self._preview_thread is not None or self._preview_pending is not None:
            return RuntimeError, "Подождите: картинка ещё готовится."
        engine = self.engine
        try:
            self._brush_rect(engine.draw_region)
        except ValueError:
            return ValueError, "Сначала обведите на экране холст — кнопка «Область» на странице «Рисование»."
        try:
            self._brush_rect(engine.dynamic_brush_scratch_zone)
        except ValueError:
            return ValueError, ("Сначала выделите свободное место на холсте для пробных мазков."
                                if engine.dynamic_brush_scratch_zone is None
                                else "Место для проб слишком маленькое: выделите его заново, не меньше 4 × 4 пикселей.")
        if mode == "brush" and not engine._dynamic_brush_control_ready():
            return ValueError, "Сначала укажите регулятор размера кисти."
        if (mode == "brush" and engine.dynamic_brush_control_mode == "text" and not engine.dynamic_brush_text_auto
                and engine.dynamic_brush_min_value < .001):
            return ValueError, "Для текстового поля задайте положительный минимальный размер кисти."
        return None

    def start_brush_learning(self, mode="brush"):
        problem = self._brush_learning_problem(mode)
        if problem:
            raise problem[0](problem[1])
        engine = self.engine
        config = self.snapshot_painter_config()
        self._brush_settings_before = deepcopy(engine.get_dynamic_brush_settings())
        self._brush_config_before = deepcopy(config)
        job = BrushLearningJob(config, mode=mode)
        self._brush_job = job
        self._brush_learning_message = ("Подбираем скорость в месте для проб…" if mode == "speed"
                                        else "Обучаем кисть в месте для проб…")
        self._brush_learning_error = ""
        self._hotkey_capture_epoch += 1
        if getattr(self, "_brush_poll_timer", None) is None:
            self._brush_poll_timer = QTimer(self)
            self._brush_poll_timer.setInterval(30)
            self._brush_poll_timer.timeout.connect(self._poll_brush_learning)
        try:
            job.thread.start()
        except Exception:
            self._brush_job = None
            self.brushLearningChanged.emit()
            raise
        self._brush_poll_timer.start()
        self.brushLearningChanged.emit()
        return True

    def cancel_brush_learning(self):
        if self.brush_learning_active:
            self._brush_job.cancelled.set()
            self._brush_learning_message = "Останавливаем обучение и освобождаем мышь…"
            self.brushLearningChanged.emit()

    def _poll_brush_learning(self):
        job = getattr(self, "_brush_job", None)
        if job is None:
            return
        latest = None
        while True:
            try:
                latest = job.messages.get_nowait()
            except Empty:
                break
        if latest is not None and not job.cancelled.is_set():
            self._brush_learning_message = str(latest)
            self.brushLearningChanged.emit()
        if job.thread.is_alive():
            return
        self._brush_poll_timer.stop()
        self._brush_job = None
        self._hotkey_capture_epoch += 1
        error = job.error
        cancelled = job.cancelled.is_set() or self._is_shutting_down or self._close_pending
        if not cancelled and not error:
            if (self.engine.get_dynamic_brush_settings() != self._brush_settings_before
                    or self.snapshot_painter_config() != self._brush_config_before):
                error = "Настройки изменились во время обучения. Повторите обучение."
            elif getattr(job, "mode", "brush") == "speed":
                self._input_timing_note = ""
                if isinstance(job.timing, dict) and "stroke_gap" in job.timing:
                    self.engine.input_timing_measured = True
                    self.set_pen_stroke_gap(float(job.timing["stroke_gap"]))
                    # the probe drew without settle pauses: each one cost 2 waits per stroke
                    self.set_pen_settle_delay(0.0)
                    self._input_timing_note = f" Пауза между штрихами: {self.engine.pen_stroke_gap * 1000:.1f} мс."
                elif isinstance(job.timing, dict):
                    self._apply_input_timing(job.timing)
                else:
                    error = "Скорость не подобрана."
            elif job.profile is None:
                error = "Обучение не создало проверенный профиль."
            else:
                self.engine.dynamic_brush_profile = deepcopy(job.profile)
                if job.slider_params is not None and self.engine.dynamic_brush_control_mode == "slider":
                    self.engine.dynamic_brush_slider_params = tuple(job.slider_params)
                self._input_timing_note = ""
                timing = getattr(job, "timing", None)
                if isinstance(timing, dict):
                    self._apply_input_timing(timing)
                self.engine.dynamic_brush_calibration = deepcopy(job.profile["cached_calibration"])
                self.engine.dynamic_brush_calibration_updated_at = job.profile["cached_calibration"].get("timestamp")
                self.engine.dynamic_brush_enabled = True
                self.engine.dynamic_brush_session_active = False
                self.engine.dynamic_brush_runtime_state = "ready"
                self.engine._sync_dynamic_brush_profile_runtime()
                self.engine._update_dynamic_brush_profile_state()
                self._emit_dynamic_brush_settings_changed()
        done_message = "Скорость подобрана." if getattr(job, "mode", "brush") == "speed" else "Кисть обучена и проверена."
        if not cancelled and not error:
            done_message += getattr(self, "_input_timing_note", "")
            self._input_timing_note = ""
            cell = self.engine.dynamic_brush_recommended_cell() if getattr(job, "mode", "brush") == "brush" else None
            if cell is not None:
                self.set_brush_size(cell)
                done_message += f" Шаг рисунка подстроен под самый маленький мазок: {cell} px."
        self._brush_learning_error = error
        self._brush_learning_message = error or ("Обучение отменено." if cancelled else done_message)
        self.brushLearningChanged.emit()
        self.brushLearningFinished.emit(not cancelled and not error)
        if self._preview_pending and not self._is_shutting_down:
            self._schedule_preview_rebuild(self._preview_pending == "strict")
