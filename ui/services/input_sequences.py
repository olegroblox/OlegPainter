"""Shared presentation and commands for saved layers and pre/post actions."""
import hashlib
import json


class InputSequencesMixin:
    def input_sequences_snapshot(self):
        engine = self.engine
        capture = self._compute_capture_state()
        groups = []
        for key, title in (("layers", "Слои программы"), ("pre", "До выбора цвета"),
                           ("post", "После выбора цвета")):
            if key == "layers":
                saved = [dict(x=x, y=y) for x, y in engine.target_app_layer_coords]
                enabled = bool(engine.draw_with_layers_enabled)
                capturing = capture["kind"] == "layers"
                draft = [dict(x=x, y=y) for x, y in engine.layer_capture_points()] if capturing else saved
                maximum = engine.max_definable_app_layers
            else:
                saved = engine._serialize_actions(key)
                enabled = bool(engine.extra_actions_state[key]["enabled"])
                capturing = capture["kind"] == "extra" and capture["slot"] == key
                draft = engine._serialize_actions(key, include_draft=True) if capturing else saved
                maximum = engine.max_extra_action_events
            revision = hashlib.sha256(json.dumps([enabled, saved], sort_keys=True).encode()).hexdigest()
            entries = []
            for index, event in enumerate(draft):
                action = {"click": "Нажатие", "down": "Зажать", "up": "Отпустить", "move": "Перемещение"}.get(event.get("action"), "Слой")
                button = {"left": "левая", "right": "правая", "middle": "средняя"}.get(event.get("button"), "")
                entries.append(dict(index=index + 1, x=str(event["x"]), y=str(event["y"]),
                                    label=action + (" · " + button + " кнопка" if button else ""),
                                    delay=event.get("delay", 0)))
            groups.append(dict(id=key, title=title, enabled=enabled, revision=revision,
                               capturing=capturing, saved_count=len(saved), entries=entries,
                               maximum=maximum))
        return groups

    def _sequence(self, key):
        if key not in ("layers", "pre", "post"):
            raise ValueError("Неизвестная запись действий.")
        return next(group for group in self.input_sequences_snapshot() if group["id"] == key)

    def _require_sequence_edit(self, key, revision):
        if (self._is_shutting_down or self._is_drawing or self._stop_pending or self.brush_learning_active
                or self._compute_capture_state()["active"] or self.desktop_interaction
                or self._hotkey_capture_active or self._thread_is_running(self._worker_thread)
                or self._thread_is_running(self._preview_thread)):
            raise RuntimeError("Сначала завершите текущую операцию.")
        group = self._sequence(key)
        if revision != group["revision"]:
            raise ValueError("Запись изменилась. Проверьте актуальные данные и повторите команду.")
        return group

    def set_sequence_enabled(self, key, enabled, revision):
        group = self._require_sequence_edit(key, revision)
        if type(enabled) is not bool:
            raise ValueError("Нужно значение «включено» или «выключено».")
        if enabled and not group["saved_count"]:
            raise ValueError("Сначала запишите координаты или действия.")
        if key == "layers":
            self.set_layers_enabled(enabled)
        else:
            self.set_extra_actions_enabled(enabled, key)

    def input_sequence_command(self, key, command, revision):
        group = self._sequence(key)
        if command in ("finish", "cancel"):
            if not group["capturing"] or self._stop_pending or self._is_shutting_down:
                raise RuntimeError("Эта запись сейчас не выполняется.")
            if command == "cancel":
                self.stop()
                return
            if key == "layers":
                self.engine.finish_app_layer_coord_capture()
            else:
                self.engine.finish_extra_actions_capture()
            self._emit_capture_state()
            return
        if command not in ("start", "clear"):
            raise ValueError("Неизвестная команда записи.")
        self._require_sequence_edit(key, revision)
        if command == "start":
            if key == "layers":
                self.engine.start_app_layer_coord_capture()
            else:
                self.engine.start_extra_actions_capture(key)
            self._emit_capture_state()
            if not self._sequence(key)["capturing"]:
                raise RuntimeError("Не удалось начать запись. Проверьте сообщение приложения.")
        elif key == "layers":
            self.engine.target_app_layer_coords = []
            self.set_layers_enabled(False)
            self._emit_app_layers_changed()
        else:
            self.engine.extra_actions_state[key]["events"] = []
            self.set_extra_actions_enabled(False, key)
