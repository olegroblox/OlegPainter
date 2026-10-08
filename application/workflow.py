"""User-facing preparation steps derived from the shared application state."""


def _stop_name(service) -> str:
    """The stop button with its key: during a screen tool the window may be hidden."""
    try:
        key = service.get_hotkeys().get("global", {}).get("stop", "")
    except Exception:
        key = ""
    return f"«Стоп» ({key})" if key else "«Стоп»"


def preparation_step(state, service):
    if state.closing:
        return dict(label="Завершение работы…", action="", detail="Сохраняем настройки и освобождаем ввод.")
    if state.drawing_busy:
        return dict(label="Рисование", action="", detail="")
    if state.hotkey_capture:
        return dict(label="Назначение клавиши", action="", detail="Нажмите сочетание клавиш или Esc для отмены.")
    if state.brush_learning:
        return dict(label="Обучение кисти…", action="", detail="Дождитесь результата или остановите обучение.")
    if state.capture.active:
        finishable = state.capture.kind in ("palette", "layers", "extra")
        return dict(label="Сохранить запись" if finishable else "Выберите точку на экране",
                    action="finish_capture" if finishable else "",
                    detail="Получено: " + str(state.capture.count) + ". Отмена сохраняет прежние настройки.")
    if state.desktop_mode == "measure":
        return dict(label="Измеряем палитру…", action="",
                    detail=f"Дождитесь результата или нажмите {_stop_name(service)}. Прежняя калибровка сохранится при отмене.")
    if state.desktop_mode:
        return dict(label="Завершите экранный инструмент", action="",
                    detail=f"Подтвердите выбор на экране или нажмите {_stop_name(service)}.")
    prep = state.preparation
    action = prep.next_action_code
    choices = {
        "load_image": ("Открыть изображение", "open_file"),
        "select_area": ("Выбрать область", "select_area"),
        "reselect_area": ("Выбрать область заново", "select_area"),
        "capture_manual_palette": ("Настроить палитру", "open_palette"),
        "calibrate_hex_field": ("Указать поле HEX", "capture_hex_palette"),
        "calibrate_screen_palette": ("Выделить палитру", "calibrate_screen_palette"),
        "calibrate_wheel_square": ("Обвести цветовое колесо", "calibrate_wheel_square"),
        "calibrate_alpha_slider": ("Настроить непрозрачность", "calibrate_alpha_slider"),
        "train_brush": ("Обучить кисть", "open_brush"),
        "record_open_actions": ("Записать открытие", "record_pre_color_actions"),
        "record_close_actions": ("Записать закрытие", "record_post_color_actions"),
    }
    if action == "calibrate_hsv_palette":
        if not service._is_valid_hsv_circle_calibration(service.engine.circle_params_calib):
            label, command = "Указать цветовой круг", "calibrate_color_circle"
        else:
            label, command = "Указать яркость", "calibrate_brightness_slider"
    else:
        label, command = choices.get(action, ("Подготовка изображения…" if prep.preview_busy else "Готово к рисованию", ""))
    return dict(label=label, action=command, detail=prep.next_requirement.lstrip("• "))
