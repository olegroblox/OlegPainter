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
                     "«Указать круг»: щёлкните центр круга, затем красный цвет на его краю — здесь он слева. "
                     "«Указать яркость»: обведите рамкой ползунок справа от круга.",
         # Live 2026-10-07: at the very start of the track the stroke is thinner than
         # the 2 px rows and fills come out striped; a few pixels right closes them.
         brush_hint="Важно: в панели карандаша сдвиньте ползунок «Кисть» почти в самое начало — чуть правее края. "
                    "Дальше края не тяните: ползунок перескочит на максимум. На самом краю кисть тоньше строк рисунка "
                    "и в заливке остаются белые полоски, а толстая кисть превратит рисунок в кляксы."),
    dict(id="spray_paint", place="spray_paint", group="Roblox", title="Spray Paint!",
         detail="Цвет вводится кодом в поле HEX.",
         # After joining the HEX row is collapsed; the size box takes 0.1..1.2.
         colour_hint="Возьмите баллончик (клавиша 1), нажмите стрелку у «Цвет», чтобы появилось поле «ХЕКС», и укажите это поле.",
         # Owner 2026-10-07: the automatic brush is optional in every place; the place
         # draws well with its measured defaults (2 px rows, 2 ms a hop) at size 0.1.
         brush_hint="В игре поставьте «Размер» 0,1 и форму-квадрат (щелчок по превью кисти), камера сверху. "
                    "Автоматическая кисть — по желанию: программа измерит размеры 0,1–1,2 и будет менять их сама.",
         speed_step="extra"),
    dict(id="draw_donate", place="draw_donate", group="Roblox", title="Draw & Donate",
         detail="Цвет вводится кодом в поле HEX.",
         area_hint="В игре нажмите «Change Resolution» → «Detailed» (700 × 700) → «Apply» — так рисунок выйдет "
                   "самым детальным. Затем обведите белый холст.",
         colour_hint="В игре откройте палитру (кнопка с цветным кругом) и перетащите её за заголовок в сторону от "
                     "холста — она останется открытой. Укажите поле с кодом цвета (#ff9000).",
         brush_hint="В игре поставьте «Brush Size» 1 — самая тонкая кисть."),
    # Live 2026-10-01: a round lasts 5 minutes; the player to draw stands in front of you.
    dict(id="draw_me", place="draw_me", group="Roblox", title="Draw Me!",
         detail="Рисуйте игрока по его снимку. Любые цвета — через колесо.",
         # Live 2026-10-07: a player standing right behind went into the cutout.
         image_hint="Наведите камеру на игрока, которого нужно нарисовать, нажмите «Снимок экрана» и обведите его рамкой. "
                    "Если за ним стоят другие игроки, поверните камеру правой кнопкой мыши, чтобы позади были только небо и трава.",
         background_hint=True,
         snapshot_first=True,
         speed_step="extra",
         area_hint="Обведите белый холст. Кнопка ⤢ в игре разворачивает холст — рисунок будет крупнее, но обведите его заново.",
         colour_hint="В игре на вкладке «Цвет» нажмите «Колесо» и обведите рамкой колесо целиком — кольцо и квадрат. Непрозрачность — 100% (ползунок влево до упора), «Стабилизатор» — 0%, стиль — «Погружная ручка».",
         brush_hint="Кнопками ◀ ▶ поставьте в игре «Размер кисти» на 4: тогда соседние полосы перекрываются и в заливке не остаётся белых щелей."),
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
         # The speed step replaces the brush step here: its advice goes with the canvas.
         area_hint="Разверните браузер на весь экран — чем крупнее холст, тем точнее рисунок — и обведите белый холст. "
                   "В игре выберите самую тонкую кисть (клавиша 1)."),
    dict(id="rust_sign", place="rust_sign", group="Steam", title="Rust",
         detail="Табличка: цвет вводится кодом в поле HEX.",
         area_hint="Откройте в игре редактор таблички и обведите её доски целиком.",
         colour_hint="Справа в разделе «ЦВЕТ» нажмите кнопку с валиком — вместо готовых цветов появится поле с кодом (#FFFFFF). Укажите это поле.",
         brush_hint="В разделе «КИСТЬ» выберите квадратную кисть, «РАЗМЕР» 2, «ПРОЗРАЧНОСТЬ» 1."),
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


def colour_parts(service) -> dict:
    """Which parts of each colour method are already pointed at on screen."""
    engine = service.engine
    from engine.olegpainter import wheel_picker
    return dict(hex=engine.hex_input_coord is not None,
                circle=bool(service._is_valid_hsv_circle_calibration(engine.circle_params_calib)),
                slider=bool(service._is_valid_hsv_slider_calibration(engine.slider_params_calib)),
                screen=bool(getattr(engine, "screen_palette_calib", None)),
                wheel=bool(wheel_picker.valid(getattr(engine, "wheel_square_calib", None))))


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
    engine = getattr(service, "engine", None)
    if method == "screen_palette":
        done = bool(getattr(engine, "screen_palette_calib", None))
        return [dict(label="Обвести палитру" + (" ✓" if done else ""), action="calibrate_screen_palette")]
    if method == "wheel_square":
        from engine.olegpainter import wheel_picker
        done = wheel_picker.valid(getattr(service.engine, "wheel_square_calib", None))
        return [dict(label="Обвести колесо" + (" ✓" if done else ""), action="calibrate_wheel_square")]
    if method == "manual_palette":
        return [dict(label="Открыть палитру", action="open_palette")]
    field = getattr(engine, "hex_input_coord", None) is not None
    return [dict(label="Указать поле HEX" + (" ✓" if field else ""), action="capture_hex_palette")]


def _image_hint(target_info, service) -> str:
    hint = target_info.get("image_hint", "")
    if hint and target_info.get("background_hint"):
        # Only what is really on: the place turns it on just for a user who never chose.
        if getattr(service, "auto_background", "off") != "off":
            hint += " Фон уберётся сам: включено «Убирать фон у новых картинок» (переключается в «Обработке»)."
        else:
            hint += " Фон уберите в «Обработке» или включите там «Убирать фон у новых картинок»."
    return hint


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


def _colour_clicks(service, keys) -> dict:
    """Optional clicks around every colour change (open the colour window before
    typing a code, close it after): asked right in the colour step, because a
    newcomer never finds them on the «Слои и действия» page."""
    engine = getattr(service, "engine", None)
    before, after = (bool(engine is not None and engine._should_play_actions(slot)) for slot in ("pre", "post"))
    pre, post = keys.get("record_pre_color_actions"), keys.get("record_post_color_actions")
    finish = (f"нажмите клавишу {pre}, чтобы закончить запись щелчков до цвета, или {post} — после"
              if pre and post else "вернитесь в OlegPainter и нажмите «Сохранить запись» на странице «Слои и действия»")
    return dict(question="Действия до и после выбора цвета",
                detail=("Те же, что на странице «Слои и действия». Например, открыть окно цвета перед вводом кода и закрыть его после. "
                        f"Нажмите кнопку записи, щёлкните нужное в программе, затем {finish}. "
                        "Программа будет повторять эти щелчки при каждой смене цвета."),
                actions=[dict(label="Записать действия до выбора цвета" + (" ✓" if before else ""), action="record_pre_color_actions"),
                         dict(label="Записать действия после выбора цвета" + (" ✓" if after else ""), action="record_post_color_actions"),
                         dict(label="Открыть «Слои и действия»", action="open_sequences")])


# Full video guides on the «Олег Роблокс» channel (2026-10-08): the video id and the
# second where each quick-start step begins in it.
GUIDES = {
    "speed_draw": ("09Br3P5Kv_U", dict(image=9, area=37, brush=45, colour=51, start=68)),
    "spray_paint": ("fTpMN7gU82E", dict(image=9, area=39, colour=49, start=67)),
    "draw_me": ("BMGyUZ3Ly0g", dict(image=9, area=25, colour=33, brush=41, start=48)),
    "gartic_phone": ("ldZ9z3KTQV0", dict(image=14, area=34, colour=40, start=75)),
    "draw_donate": ("r7Ff3gp0ELc", dict(image=17, area=5, colour=43, start=51)),
    "rust_sign": ("hRVb8BMvJvQ", dict(image=8, area=30, colour=37, brush=44, start=44)),
}


def guide_url(target: str, step_id: str = "") -> str:
    """The place's video guide, at the step when it is known; "" without a guide."""
    video, steps = GUIDES.get(target, ("", {}))
    if not video:
        return ""
    second = steps.get(step_id)
    return f"https://youtu.be/{video}" + (f"?t={second}" if second else "")


def hint_clip(target: str, step_id: str) -> str:
    """A short silent loop of this step cut from the place's video guide
    (assets/hints/<target>/<step>.webp), as a file URL; "" when there is none."""
    from pathlib import Path
    from app_paths import get_app_paths
    for path in get_app_paths().resource_candidates(Path("assets") / "hints" / target / f"{step_id}.webp"):
        if path.is_file():
            return path.as_uri()
    return ""


def _start_hint(keys) -> str:
    start, stop = keys.get("start_pause"), keys.get("stop")
    hint = "Нажмите «Начать рисование»: окно свернётся на время рисунка."
    if start:
        hint += f" Или нажмите {start} прямо в программе рисования."
    if stop:
        hint += f" {stop} — остановить."
    return hint


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
             detail=_image_hint(target_info, service) or (
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
        # A place with its own brush hint says what to set in the game; elsewhere the
        # step is the optional automatic brush, named as such (a bare «Размер кисти»
        # read as «choose a size» — owner, 2026-10-05).
        dict(id="brush", title="Размер кисти" if target_info.get("brush_hint") else "Автоматическая кисть",
             done=brush_ready, optional=not target_info.get("brush_required"),
             detail=target_info.get("brush_hint") or (
                 "Необязательно. Программа будет сама менять размер кисти во время рисунка: большие области — "
                 "крупной кистью (быстрее), края и мелочь — самой маленькой (точнее). Для этого один раз покажите ей "
                 "регулятор размера кисти и свободное место для пробных мазков. Без этого весь рисунок идёт одним "
                 "размером — тем, что выбран в программе."),
             # A manual hint is done in the game: the button is the optional automatic brush.
             actions=[dict(label="Настроить автоматическую кисть", action="open_brush")]),
    ]
    # Every place: ready places too may need a window opened around the colour, and
    # the owner looked for these buttons there (2026-10-06).
    if not any(action["action"].startswith("record_") for action in steps[2]["actions"]):   # Gartic: in the main row
        steps[2]["extra"] = _colour_clicks(service, keys)
    if target_info.get("speed_step"):
        # Browser games: no size control to learn, but the pause the page needs
        # between strokes differs between computers and browsers. Any other
        # program or game gets the same optional step next to the brush.
        engine = getattr(service, "engine", None)
        # Outside straight strokes the probe sets turn and lift pauses, not the stroke gap.
        measured = (bool(getattr(engine, "input_timing_measured", False))
                    or float(getattr(engine, "pen_stroke_gap", 0.0) or 0.0) > 0)
        zone = getattr(engine, "dynamic_brush_scratch_zone", None) is not None
        blocker = getattr(service, "brush_learning_blocker", None)
        speed = dict(id="speed", title="Подбор скорости", done=measured, optional=True,
                     detail=("Необязательно. Программа сама найдёт самую быструю скорость, при которой рисунок "
                             "не теряет штрихи: нарисует несколько пробных линий в чистом уголке холста. "
                             "Выделите этот уголок и выберите в программе тёмный цвет. Пробные линии останутся — "
                             "потом сотрите их. Без подбора рисунок идёт с обычными паузами из настроек."),
                     actions=[dict(label="Выделить место для проб" + (" ✓" if zone else ""), action="capture_scratch"),
                              dict(label="Подобрать скорость" + (" ✓" if measured else ""), action="learn_speed",
                                   blocked=blocker("speed") if callable(blocker) else "")])
        if target_info["speed_step"] == "extra":
            steps.append(speed)
        else:
            steps[-1] = speed
    required = [step for step in steps if not step.get("optional")]
    current = next((step["id"] for step in required if not step["done"]), "start")
    steps.append(dict(id="start", title="Рисуем", done=False, optional=False,
                      detail=(_start_hint(keys) if prep.can_start else "Сначала завершите шаги выше."),
                      actions=[dict(label="Начать рисование", action="start_pause")] if prep.can_start else []))
    for step in steps:
        step.setdefault("optional", False)
        step["current"] = step["id"] == current
        step["hint"] = hint_clip(target, step["id"]) if target != "other" else ""
        step["guide_url"] = guide_url(target, step["id"]) if step["hint"] else ""
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
                ready=bool(prep.can_start), guide_url=guide_url(target) if target_chosen else "")
