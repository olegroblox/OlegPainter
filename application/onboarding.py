"""Quick start: where the user draws and what is still missing, as plain data.

The QML wizard only renders this. Step completion is read from the same
preparation snapshot that gates the start button, so the wizard can never call
a step done that the engine would still refuse.
"""
from __future__ import annotations

from application.settings import COLOR_METHODS

# Targets a newcomer recognises. Roblox places keep their tuned presets; anything
# else (browser games such as Gartic Phone, Paint, other apps) uses the universal
# profile and asks one question: how that program picks a colour.
TARGETS = (
    dict(id="speed_draw", place="speed_draw", group="Roblox", title="Speed Draw!",
         detail="Цвет выбирается кругом и ползунком яркости.",
         # Live 2026-10-01: the «+90/+30/−60 секунд» chips cover the top of the canvas
         # and spend coins; the default brush is ~40 px, the program draws 2 px cells.
         area_hint="Обведите холст ниже полосок «+90 секунд» — щелчок по ним тратит монеты. "
                   "Раунд длится 3–6 минут: рисунок примерно 220 × 275 пикселей в 8 цветах занимает около минуты.",
         colour_hint="В игре возьмите карандаш (кнопка 1 внизу) — слева откроется палитра. "
                     "Укажите цветовой круг, затем ползунок яркости справа от него.",
         brush_hint="Важно: в панели карандаша сдвиньте ползунок «Кисть» в самое начало, но не дальше края — "
                    "иначе он перескочит на максимум. Программа рисует тонкими штрихами, толстая кисть превратит рисунок в кляксы."),
    dict(id="spray_paint", place="spray_paint", group="Roblox", title="Spray Paint!",
         detail="Цвет вводится кодом в поле HEX.",
         # After joining the HEX row is collapsed; the size box takes 0.1..1.2.
         colour_hint="Возьмите баллончик (клавиша 1), нажмите стрелку у «Цвет», чтобы появилось поле «ХЕКС», и укажите это поле.",
         brush_required=True,  # the 0.1 stamp is several screen pixels: without learning the route overdraws
         brush_hint="Рекомендуется: камера сверху, кисть-квадрат. Укажите число рядом с «Размер» и место на пустом полу — "
                    "программа сама измерит размеры 0,1–1,2 и скорость. После смены приближения камеры обучите заново."),
    dict(id="draw_donate", place="draw_donate", group="Roblox", title="Draw & Donate",
         detail="Цвет вводится кодом в поле HEX."),
    # Live 2026-10-01: a round lasts 5 minutes; the player to draw stands in front of you.
    dict(id="draw_me", place="draw_me", group="Roblox", title="Нарисуй меня!",
         detail="Рисуйте игрока по его снимку. Любые цвета — через колесо.",
         image_hint="Наведите камеру на игрока, которого нужно нарисовать, нажмите «Снимок экрана» и обведите его рамкой. Фон уберётся сам: для этого места включено «Убирать фон у новых картинок» (переключается в «Обработке»).",
         snapshot_first=True,
         speed_step="extra",
         area_hint="Обведите белый холст. Кнопка ⤢ в игре разворачивает холст — рисунок будет крупнее, но обведите его заново.",
         colour_hint="В игре на вкладке «Цвет» нажмите «Колесо» и обведите рамкой колесо целиком — кольцо и квадрат. Непрозрачность — 100% (ползунок влево до упора), «Стабилизатор» — 0%, стиль — «Погружная ручка».",
         brush_hint="Поставьте в игре «Размер кисти» 4 кнопками ◀ ▶: соседние полосы клетки 4 перекрываются, и в заливке не остаётся белых щелей."),
    dict(id="gartic_phone", place="gartic_phone", group="Сайт или программа", title="Gartic Phone",
         detail="Любые цвета через поле HEX, рисунок прямыми штрихами.",
         # {record_pre}/{record_post} are the keys that finish the recordings, filled in by build().
         # Live 2026-10-01: the picker stays open after the code, and a first stroke
         # landing on its colour square changed the colour — step 3 closes it.
         colour_hint="1) «Записать открытие»: щёлкните большую плашку цвета под палитрой, затем дважды переключатель под числами (RGB → HSL → HEX) и нажмите клавишу {record_pre}.\n"
                     "2) «Указать поле HEX»: программа сама откроет окно, щёлкните поле с кодом.\n"
                     "3) «Записать закрытие»: щёлкните пустое место страницы (не холст и не палитру) и нажмите клавишу {record_post} — окно цвета закроется и не помешает рисовать.",
         colour_actions="hex_with_opening",
         speed_step=True,
         brush_hint="Необязательно. Разверните браузер на весь экран — чем крупнее холст, тем точнее рисунок. В игре выберите самую тонкую кисть (клавиша 1)."),
    dict(id="other", place="universal", group="Сайт или программа", title="Другая программа",
         detail="Paint, другие игры и редакторы.", speed_step="extra"),
)

# Colour methods: the same words as the settings and the calibration dialog.
METHODS = tuple(dict(id=method["id"], title=method["label"], detail=method["detail"]) for method in COLOR_METHODS)

_METHOD_HINTS = {
    "hex_field": "Откройте в программе окно выбора цвета и укажите поле, куда вводится код цвета.",
    "hsv_palette": "Откройте палитру: укажите цветовой круг, затем ползунок яркости.",
    "screen_palette": "Откройте панель с готовыми цветами и обведите её рамкой.",
    "wheel_square": "Откройте палитру с цветным кольцом и обведите рамкой колесо целиком — кольцо и квадрат внутри.",
    "manual_palette": "Добавьте готовые цвета программы по одному на странице «Палитра».",
}


def _key_names(service, language: str = "ru") -> dict:
    """Assigned keys by action code; texts must not promise F3 after a rebind."""
    try:
        profile = service.get_hotkeys()
    except Exception:
        return {}
    names = {code: sequence for group in profile.values() for code, sequence in group.items()}
    if language != "ru":
        return names
    # "[" and "]" are the Russian Х/Ъ keys: name the letter a Russian user sees.
    return {code: {"[": "[ (русская Х)", "]": "] (русская Ъ)"}.get(sequence, sequence)
            for code, sequence in names.items()}


def target_for_place(place_id: str) -> str:
    for target in TARGETS:
        if target["place"] == place_id and target["id"] != "other":
            return target["id"]
    return "other"


def _colour_actions(method: str, prep, service, target_info=None) -> list[dict]:
    if (target_info or {}).get("colour_actions") == "hex_with_opening":
        # The colour box opens only after recorded clicks (Gartic Phone's picker).
        opened = bool(service.engine._should_play_actions("pre"))
        field = service.engine.hex_input_coord is not None
        closed = bool(service.engine._should_play_actions("post"))
        return [dict(label="Записать открытие" + (" ✓" if opened else ""), action="record_pre_color_actions"),
                dict(label="Указать поле HEX" + (" ✓" if field else ""), action="capture_hex_palette"),
                dict(label="Записать закрытие" + (" ✓" if closed else ""), action="record_post_color_actions")]
    if method == "hsv_palette":
        engine = service.engine
        circle = service._is_valid_hsv_circle_calibration(engine.circle_params_calib)
        slider = service._is_valid_hsv_slider_calibration(engine.slider_params_calib)
        return [dict(label="Указать круг" + (" ✓" if circle else ""), action="calibrate_color_circle"),
                dict(label="Указать яркость" + (" ✓" if slider else ""), action="calibrate_brightness_slider")]
    if method == "screen_palette":
        return [dict(label="Обвести палитру", action="calibrate_screen_palette")]
    if method == "wheel_square":
        from engine.olegpainter import wheel_picker
        done = wheel_picker.valid(getattr(service.engine, "wheel_square_calib", None))
        return [dict(label="Обвести колесо" + (" ✓" if done else ""), action="calibrate_wheel_square")]
    if method == "manual_palette":
        return [dict(label="Открыть палитру", action="open_palette")]
    return [dict(label="Указать поле цвета", action="capture_hex_palette")]


def _colour_hint(target_info, method, keys) -> str:
    hint = target_info.get("colour_hint") or _METHOD_HINTS.get(method, _METHOD_HINTS["hex_field"])
    for slot, code in (("{record_pre}", "record_pre_color_actions"), ("{record_post}", "record_post_color_actions")):
        if slot not in hint:
            continue
        record = keys.get(code)
        hint = (hint.replace(slot, record) if record
                else hint.replace("и нажмите клавишу " + slot,
                                  "и вернитесь в окно OlegPainter, чтобы нажать «Сохранить запись»"))
    return hint


def _start_hint(keys) -> str:
    start, stop = keys.get("start_pause"), keys.get("stop")
    if start and stop:
        return f"Переключитесь в программу и нажмите «Начать» или {start}. {stop} — остановить."
    if start:
        return f"Переключитесь в программу и нажмите «Начать» или {start}."
    return "Нажмите «Начать рисовать»: окно свернётся на время рисунка."


QUICK_PLACE = "quick_place"     # profile category marking a user's own place (PLACES-003)


def build(state, service, *, brush_ready: bool, language: str = "ru", target_chosen: bool = True,
          custom_places=(), custom_target: str = "") -> dict:
    """Wizard view: targets, methods, the current choice and six steps.

    `target_chosen` is False until a newcomer picks where they draw: no tile is
    shown as selected, so nobody starts Paint with a Roblox preset (ONBOARD-003)."""
    prep = state.preparation
    keys = _key_names(service, language)
    method = prep.current_method_id or "hex_field"
    target = target_for_place(prep.current_place_id)
    target_info = next((t for t in TARGETS if t["id"] == target), {})
    area_done = bool(prep.area_selected and not prep.area_stale)
    colour_done = not prep.required_calibrations
    if target_info.get("colour_actions") == "hex_with_opening":
        colour_done = (colour_done and bool(service.engine._should_play_actions("pre"))
                       and bool(service.engine._should_play_actions("post")))
    paste = keys.get("paste_clipboard")
    image_actions = [dict(label="Открыть файл", action="open_file"),
                     dict(label="Из буфера", action="paste_clipboard"),
                     dict(label="Найти в интернете", action="search_image"),
                     dict(label="Снимок экрана", action="screen_snapshot")]
    if target_info.get("snapshot_first"):
        image_actions = image_actions[3:] + image_actions[:3]
    steps = [
        dict(id="image", title="Картинка", done=bool(prep.image_loaded),
             detail=target_info.get("image_hint") or (
                 f"Откройте файл, вставьте картинку из буфера ({paste}), найдите её в интернете "
                 "или сделайте снимок экрана." if paste
                 else "Откройте файл, вставьте картинку из буфера, найдите её в интернете "
                      "или сделайте снимок экрана."),
             actions=image_actions),
        dict(id="area", title="Где рисовать на экране", done=area_done,
             detail=(target_info.get("area_hint") or "Откройте программу, где будете рисовать, и обведите на экране холст — ту часть, где должен появиться рисунок."
                     if not prep.area_stale
                     else "Экран или окно изменились — обведите холст заново."),
             actions=[dict(label="Обвести холст", action="select_area")]),
        dict(id="colour", title="Как выбирается цвет", done=colour_done,
             detail=_colour_hint(target_info, method, keys),
             actions=_colour_actions(method, prep, service, target_info)),
        dict(id="brush", title="Размер кисти", done=brush_ready, optional=not target_info.get("brush_required"),
             detail=target_info.get("brush_hint") or "Необязательно. Программа сама научится менять размер кисти: большие области быстрее, мелочь точнее. Без этого рисунок идёт текущим размером кисти.",
             actions=[dict(label="Настроить кисть", action="open_brush")]),
    ]
    if target_info.get("speed_step"):
        # Browser games: no size control to learn, but the pause the page needs
        # between strokes differs between computers and browsers. Any other
        # program or game gets the same optional step next to the brush.
        engine = getattr(service, "engine", None)
        measured = float(getattr(engine, "pen_stroke_gap", 0.0) or 0.0) > 0
        zone = getattr(engine, "dynamic_brush_scratch_zone", None) is not None
        speed = dict(id="speed", title="Скорость", done=measured, optional=True,
                     detail=("Необязательно. Выделите чистый уголок холста и выберите в программе тёмный цвет — "
                             "программа сама подберёт самую быструю паузу между штрихами, при которой ничего не теряется."),
                     actions=[dict(label="Место для проб" + (" ✓" if zone else ""), action="capture_scratch"),
                              dict(label="Подобрать скорость" + (" ✓" if measured else ""), action="learn_speed")])
        if target_info["speed_step"] == "extra":
            steps.append(speed)
        else:
            steps[-1] = speed
    required = [step for step in steps if not step.get("optional")]
    current = next((step["id"] for step in required if not step["done"]), "start")
    steps.append(dict(id="start", title="Рисуем", done=False, optional=False,
                      detail=(_start_hint(keys) if prep.can_start else "Сначала завершите шаги выше."),
                      actions=[dict(label="Начать рисовать", action="start_pause")] if prep.can_start else []))
    for step in steps:
        step.setdefault("optional", False)
        step["current"] = step["id"] == current
    method_choice = method
    targets = [dict(id=t["id"], place=t["place"], group=t["group"], title=t["title"], detail=t["detail"])
               for t in TARGETS]
    targets += [dict(id="custom:" + place["slug"], place="", group="Мои места", title=place["name"],
                     detail="Ваши настройки и калибровки") for place in custom_places]
    if custom_target and any(place["slug"] == custom_target for place in custom_places):
        target = "custom:" + custom_target
    return dict(targets=targets, methods=[dict(m) for m in METHODS],
                target=target if target_chosen else "", target_chosen=bool(target_chosen),
                method=method, method_choice=method_choice, steps=steps, current=current,
                ready=bool(prep.can_start))
