"""Editable drawing settings: validated commands and presentation metadata.

No Qt dependency. All writes go through the existing service setters so image
invalidation and session persistence have the same owner in every presentation.
"""
from dataclasses import dataclass
import math

from engine.olegpainter.drawing_events import DrawingPhase


SETTING_HELP = {
    "mode": "Цветной режим использует палитру оттенков. Чёрно-белый оставляет чёрное и белое; после его выбора появится настройка, какие из этих цветов рисовать.",
    "bw_draw_mode": "Можно рисовать оба цвета или пропустить цвет самого холста: например, на белом холсте обычно достаточно чёрных участков.",
    "k_clusters": "Максимальное число цветов подготовленного изображения. Больше цветов сохраняет больше оттенков, но требует больше переключений. Объединение похожих оттенков может уменьшить итоговую палитру.",
    "prep_color_space": "Способ сравнивать оттенки при подготовке. OKLab и CIELAB учитывают зрительное различие цветов. RGB оставлен для совместимости с прежней обработкой; объединение оттенков и передача полутонов рассчитаны на OKLab/CIELAB.",
    "prep_quantization_mode": "Алгоритм подбора ограниченной палитры. K-means группирует похожие цвета; MiniBatch использует приближённую группировку; ImageQuant применяет отдельный алгоритм квантования. Сравнивайте результат на своём изображении.",
    "prep_cleanup_mode": "Убирает слишком маленькие цветные области. Дополнительное объединение одиночных точек уменьшает мелкие мазки, но может убрать мелкие детали. Минимальная площадь задаёт размер таких областей.",
    "fast_min_region_area": "Области меньше этого числа клеток сливаются с соседними при очистке. Больше — меньше мелких мазков, но могут пропасть детали. 0 — автоматически: очистка убирает пятна до 4 клеток, усиленная очистка точек — до 6, поэтому значения 1–5 чистят слабее, чем 0.",
    "prep_preserve_accents": "Помогает сохранить небольшие яркие цветные детали при подборе палитры. Сравнивайте с оригиналом: результат зависит от изображения и выбранного числа цветов.",
    "aggressive_despeckle_enabled": "Дополнительная очистка отдельных точек. Уменьшает шум и мелкие мазки, но может удалить нужные детали.",
    "prep_keep_details": "Очистка и слияние мелких областей не трогают маленькие пятна, которые заметно темнее соседей или другого цвета: зрачки, брови, рот, тонкие линии, губы. Сливаются шум близких оттенков и светлые искры. Выключите, если остаются лишние точки.",
    "tone_sequence": "Какие цвета рисовать первыми. «Мелкие детали последними» сначала закрашивает крупные области, а контуры, глаза и надписи рисует в конце: их не затирают мазки других цветов, когда кисть в программе шире клетки рисунка. При точной кисти, например в Paint 1 px, порядок на результат не влияет.",
    "area_sequence": "Определяет порядок отдельных областей одного цвета: сначала крупные, мелкие или ближайшие к предыдущей области. Сокращение переходов и дополнительная перестановка работают только с ближайшими областями.",
    "fill_traversal_mode": "Выбирает траекторию заполнения области. Автоматический режим выбирает путь; кривая Гильберта и спираль Ферма задают конкретный вариант. Оценивайте результат в целевой программе.",
    "run_length_merge_enabled": "Объединяет соседние шаги на прямой в длинный штрих. Может сократить число движений; программа рисования должна успевать обработать такой штрих.",
    "astar_bridge_enabled": "Ищет соединения между участками внутри того же цвета, чтобы реже поднимать перо.",
    "area_order_2opt_enabled": "Работает при очередности «Ближайшие области». Уточняет их порядок для сокращения пустых перемещений. Подготовка маршрута может занять больше времени.",
    "area_order_or_opt_enabled": "Работает при очередности «Ближайшие области». Дополнительно переставляет области для сокращения перемещений. Это дополнительная работа при подготовке.",
    "area_entry_exit_routing_enabled": "Выбирает точки начала и окончания областей с учётом соседних переходов.",
    "fill_route_polish_enabled": "Дополнительно уточняет путь заполнения внутри области. Может увеличить время подготовки маршрута.",
    "snake_turn_minimize_enabled": "Старается уменьшить количество поворотов траектории заполнения.",
    "motion_profile_enabled": "Меняет скорость внутри длинного штриха только при ненулевой «Паузе на пиксель длинного штриха». При нулевой паузе не действует. Проверяйте, успевает ли целевая программа принимать движения на быстрых участках.",
    "focus_target_window_enabled": "Перед запуском активирует выбранное внешнее окно, чтобы первый клик попал в программу рисования. Проверки целевого окна во время работы остаются включены.",
    "hex_add_hash": "Добавляет символ # перед кодом цвета при вводе в HEX-поле. Включайте, если целевая программа ожидает запись вида #FF0000.",
    "manual_match_space": "Способ поиска ближайшего доступного цвета в ручной палитре. Это влияет на сопоставление с выбранными вами образцами.",
    "palette_match_metric": "Способ измерять различие оттенков при подборе цвета палитры. Обычное расстояние проще; CIEDE2000 использует более сложную оценку различия цветов.",
    "post_draw_repair_passes": "Максимальное число дополнительных проверок и проходов после основного рисунка. Больше проходов занимает больше времени.",
    "post_draw_repair_mode": "Бережный режим осторожнее выбирает места для исправления; усиленный может перерисовать больше участков. Работает при включённом исправлении пропусков.",
    "post_draw_repair_sensitivity": "Чувствительность проверки пропущенных участков. 100% — базовая настройка; сравнивайте найденные пропуски с реальным холстом.",
    "post_draw_repair_color_mismatch_sensitivity": "Чувствительность проверки несовпадения цвета на холсте. 100% — базовая настройка. Влияет на дополнительное исправление после рисунка.",
    "telemetry_enabled": "Сохраняет диагностические сведения о выполнении рисунка для разбора ошибок и времени работы.",
    "color_picking_method": "Как в программе выбирается цвет: поле HEX, круг и яркость, готовые цвета по одному или палитра рамкой. После выбора укажите это место на экране.",
}


@dataclass(frozen=True)
class Setting:
    key: str
    label: str
    kind: str
    minimum: float = 0
    maximum: float = 1
    step: float = 1
    options: tuple = ()
    help: str = ""
    slider_maximum: float | None = None
    display_scale: float = 1
    visible_when: tuple = ()
    editable_while_paused: bool = False

    def is_editable(self, state):
        if state.can_edit_source:
            return True
        return (self.editable_while_paused and state.drawing == DrawingPhase.PAUSED
                and not state.closing and not state.brush_learning
                and not state.capture.active and not state.desktop_mode and not state.hotkey_capture)

    def is_visible(self, values):
        return all(values.get(key) == expected for key, expected in self.visible_when)

    def validate(self, value):
        if self.kind == "bool":
            if type(value) is not bool:
                raise ValueError("Нужно значение «включено» или «выключено».")
            return value
        if self.kind == "choice":
            if not isinstance(value, str) or value not in dict(self.options):
                raise ValueError("Неподдерживаемое значение настройки.")
            return value
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("Введите число, например 0,01.")
        if not self.minimum <= value <= self.maximum:
            low, high = self.minimum * self.display_scale, self.maximum * self.display_scale
            raise ValueError(f"Значение должно быть от {low:g} до {high:g}.")
        if self.kind == "int":
            if value != int(value):
                raise ValueError("Введите целое число.")
            return int(value)
        return float(value)

    def descriptor(self):
        return dict(key=self.key, label=self.label, kind=self.kind,
                    minimum=self.minimum * self.display_scale, maximum=self.maximum * self.display_scale,
                    step=self.step * self.display_scale, scale=self.display_scale,
                    options=[dict(id=value, label=label) for value, label in self.options],
                    help=self.help or SETTING_HELP.get(self.key, ""),
                    sliderMaximum=(self.maximum if self.slider_maximum is None else self.slider_maximum) * self.display_scale)


GROUPS = (
    # «Основное» is all a newcomer sees; the other groups open with «Расширенные» (SETTINGS-004).
    ("basic", "Основное", (
        Setting("mode", "Режим рисунка", "choice", options=(
            ("color", "Цветной"), ("bw", "Чёрно-белый"))),
        Setting("bw_draw_mode", "Что рисовать в чёрно-белом режиме", "choice", options=(
            ("both", "Чёрное и белое"), ("black_only", "Только чёрное"), ("white_only", "Только белое")),
                visible_when=(("mode", "bw"),)),
        Setting("k_clusters", "Цветов в палитре", "int", 1, 256),
        Setting("brush_size", "Размер кисти, px", "int", 1, 1000, slider_maximum=100,
                help="Размер мазка в пикселях целевого холста. Большая кисть быстрее заполняет изображение, но теряет мелкие детали. Размер в целевой программе должен соответствовать этому значению, если автоматическая кисть не настроена."),
        Setting("tone_sequence", "Очередность цветов", "choice", options=(
            ("light_to_dark", "От светлых к тёмным"), ("dark_to_light", "От тёмных к светлым"),
            ("details_last", "Мелкие детали последними"))),
        Setting("draw_delay", "Пауза между движениями, с", "float", 0, 10, 0.0005, slider_maximum=0.1, editable_while_paused=True,
                help="Ожидание между последовательными движениями пера при рисовании. Больше — медленнее, но целевая программа успевает принять движения. Если линии прерываются, постепенно увеличивайте паузу. 0,01 с = 10 миллисекунд; 0 убирает эту дополнительную задержку."),
        Setting("skip_matching_canvas", "Не рисовать то, что на холсте уже совпадает", "bool",
                help="Перед рисунком делает снимок места рисования и сразу засчитывает участки, где уже стоит нужный цвет: например, белое на белом холсте или уже нарисованную часть после остановки."),
        Setting("post_draw_repair_enabled", "Исправлять пропуски после рисунка", "bool",
                help="После рисунка делает снимок места рисования и дорисовывает найденные пропуски. Когда включено, его настройки появляются прямо ниже."),
        Setting("post_draw_repair_passes", "Число проходов исправления", "int", 1, 3,
                visible_when=(("post_draw_repair_enabled", True),)),
        Setting("post_draw_repair_mode", "Режим исправления", "choice", options=(
            ("conservative", "Бережный"), ("aggressive", "Усиленный")), visible_when=(("post_draw_repair_enabled", True),)),
        Setting("post_draw_repair_sensitivity", "Чувствительность к пропускам, %", "int", 50, 200, 5,
                visible_when=(("post_draw_repair_enabled", True),)),
        Setting("post_draw_repair_color_mismatch_sensitivity", "Чувствительность к неверному цвету, %", "int", 50, 200, 5,
                visible_when=(("post_draw_repair_enabled", True),)),
    )),
    ("speed", "Скорость и ввод", (
        Setting("pen_button_delay", "Пауза нажатия мыши, с", "float", 0, 10, 0.001, slider_maximum=0.1, editable_while_paused=True,
                help="Ожидание после нажатия и отпускания кнопки мыши. Помогает целевой программе заметить начало и конец мазка. Особенно влияет на скорость множества маленьких областей. Если мазки не начинаются или слипаются, увеличьте значение понемногу."),
        Setting("pen_settle_delay", "Пауза после движения, с", "float", 0, 10, 0.001, slider_maximum=0.1, editable_while_paused=True,
                help="Ожидание при переходе к началу новой области и после опускания пера перед её заполнением. Даёт курсору и инструменту установиться на месте. Увеличьте, если начало мазка смещается или пропадает. Это отдельная задержка от паузы каждого движения."),
        Setting("pen_press_nudge", "Подводить курсор перед нажатием", "bool", editable_while_paused=True,
                help="Перед каждым нажатием слегка шевелит курсор на месте, а перед отпусканием повторяет последнюю точку. Нужно играм, которые читают мышь раз в кадр (например, Roblox Spray Paint): без этого остаются лишние точки вдоль перелётов пера и обрезается конец линии. Замедляет каждый отрыв пера примерно на 60 мс. Включается само при обучении кисти, если проба видит такой след."),
        Setting("pen_split_strokes", "Прямые штрихи (браузерные игры)", "bool",
                help="Каждый прямой отрезок рисуется отдельным нажатием, без поворотов внутри штриха. Нужно играм в браузере, которые читают мышь раз в кадр и сглаживают штрих кривыми (например, Gartic Phone): иначе углы срезаются и заливка превращается в кляксы. Включается само, если выбрать место Gartic Phone."),
        Setting("pen_stroke_gap", "Пауза между штрихами, с", "float", 0, 1, 0.001, slider_maximum=0.1,
                visible_when=(("pen_split_strokes", True),),
                help="Ожидание после каждого отпускания в режиме прямых штрихов, чтобы страница успела принять штрих. 0 — автоматически: три кадра по частоте монитора, не меньше 0,012 с (60 Гц — 0,052 с). В Gartic Phone при паузе в один кадр часть штрихов теряется."),
    )),
    ("preparation", "Подготовка изображения", (
        Setting("prep_color_space", "Обработка цвета", "choice", options=(
            ("legacy_rgb", "RGB — совместимость"), ("cielab", "CIELAB"), ("oklab", "OKLab"))),
        Setting("prep_quantization_mode", "Подбор палитры", "choice", options=(
            ("kmeans", "K-means"), ("minibatch", "MiniBatch"), ("imagequant", "ImageQuant"))),
        Setting("prep_cleanup_mode", "Очистка мелких областей", "choice", options=(
            ("off", "Выключена"), ("vectorized", "Очистка"),
            ("vectorized_plus_stray_merge", "Очистка и объединение одиночных точек"))),
        Setting("fast_min_region_area", "Минимальная площадь области, клеток", "int", 0, 9999, slider_maximum=100),
        Setting("color_merge_threshold", "Объединение близких цветов · шкала 0–100", "float", 0, 1, 0.001,
                slider_maximum=1.0, display_scale=100,
                help="Сливает похожие оттенки в один цвет, чтобы в рисунке было меньше переключений цвета. 0 и малые значения ничего не меняют: цвета после подбора палитры и так заметно разные. На обычной картинке эффект начинается примерно с 15–20; 50 — сильное упрощение, 100 — почти всё в один цвет. Работает при обработке OKLab/CIELAB."),
        Setting("prep_preserve_accents", "Сохранять цветовые акценты", "bool"),
        Setting("prep_dither_enabled", "Передавать полутона точками", "bool",
                help="Для CIELAB/OKLab. Может увеличить число движений и время рисунка."),
        Setting("aggressive_despeckle_enabled", "Усиленная очистка одиночных точек", "bool"),
        Setting("prep_keep_details", "Сохранять контрастные детали", "bool"),
    )),
    ("routing", "Порядок рисования", (
        Setting("area_sequence", "Очередность областей", "choice", options=(
            ("large_to_small", "От больших к маленьким"), ("small_to_large", "От маленьких к большим"),
            ("nearest", "Ближайшие области"))),
        Setting("fill_traversal_mode", "Маршрут заполнения", "choice", options=(
            ("auto", "Автоматически"), ("gilbert", "Кривая Гильберта"), ("fermat", "Спираль Ферма"))),
        Setting("run_length_merge_enabled", "Объединять прямые штрихи", "bool"),
        Setting("astar_bridge_enabled", "Проводить переходы внутри цвета", "bool"),
        Setting("cheap_bridges_enabled", "Короткие переходы вместо отрыва пера", "bool",
                help="Если до следующего участка того же цвета можно дойти коротким путём по своему цвету и это быстрее, чем поднять и опустить перо при текущих паузах, перо не отрывается. Путь идёт только по горизонтали и вертикали и не задевает чужие цвета."),
        Setting("euler_greedy_pairing_enabled", "Соединять сложные области одним маршрутом", "bool",
                help="Маршрут может повторно проходить по уже нарисованным участкам. В Paint с подобранными паузами это было в 4–12 раз медленнее, обычно не включайте."),
        Setting("area_order_2opt_enabled", "Сокращать переходы между областями", "bool",
                visible_when=(("area_sequence", "nearest"),)),
        Setting("area_order_or_opt_enabled", "Дополнительно переставлять области", "bool",
                visible_when=(("area_sequence", "nearest"),)),
        Setting("area_entry_exit_routing_enabled", "Подбирать точки входа и выхода", "bool"),
        Setting("fill_route_polish_enabled", "Уточнять маршрут внутри области", "bool"),
        Setting("snake_turn_minimize_enabled", "Уменьшать число поворотов", "bool"),
        Setting("motion_profile_enabled", "Плавное изменение скорости", "bool"),
    )),
    ("timing", "Совместимость с программой", (
        Setting("area_fill_delay", "Пауза на пиксель длинного штриха, с", "float", 0, 10, 0.0001, slider_maximum=0.1,
                help="Разбивает длинную линию на шаги по пикселю и делает паузу на каждом шаге. 0 — без разбиения и ожидания. При пропусках сначала попробуйте «Паузу между движениями»: она ждёт в конце движения и не замедляет каждый пиксель линии. Подготовка изображения от этих пауз не меняется."),
        Setting("pen_max_step", "Максимальный шаг пера, px", "int", 0, 1000,
                help="0 — без ограничения. Меньший шаг помогает программам, пропускающим быстрые движения."),
        Setting("focus_target_window_enabled", "Активировать целевое окно перед рисованием", "bool"),
        Setting("hex_add_hash", "Добавлять # в поле HEX", "bool"),
        Setting("manual_match_space", "Сопоставление ручной палитры", "choice", options=(
            ("oklab", "OKLab"), ("lab", "CIELAB"))),
        Setting("palette_match_metric", "Сравнение цветов палитры", "choice", options=(
            ("euclidean", "Расстояние в цветовом пространстве"), ("ciede2000", "CIEDE2000"))),
    )),
    ("diagnostics", "Диагностика", (
        Setting("telemetry_enabled", "Записывать диагностику рисунка", "bool"),
    )),
)

SETTINGS = {setting.key: setting for _, _, settings in GROUPS for setting in settings}

# One vocabulary for the colour methods on every screen (VOCABULARY-001): the
# settings list, the calibration dialog and the quick start show these words.
COLOR_METHODS = (
    dict(id="hex_field", label="Поле HEX",
         detail="Код цвета вводится в поле, например #FF8800: Paint.NET, Photopea, многие игры."),
    dict(id="hsv_palette", label="Круг и яркость",
         detail="Цвет выбирается щелчком по цветовому кругу и ползунку яркости."),
    dict(id="manual_palette", label="Готовые цвета по одному",
         detail="Кнопки-цвета в программе: укажите каждую щелчком. Подходит для Paint и программ с готовой палитрой."),
    dict(id="screen_palette", label="Палитра рамкой",
         detail="Обведите рамкой панель с цветами — программа найдёт цвета сама."),
    dict(id="wheel_square", label="Кольцо и квадрат",
         detail="Оттенок выбирается на кольце, насыщенность и яркость — в квадрате внутри: Draw Me!, Krita. Любые цвета."),
)
SETTINGS["color_picking_method"] = Setting("color_picking_method", "Выбор цвета", "choice", options=tuple(
    (method["id"], method["label"]) for method in COLOR_METHODS))


def descriptors():
    return [dict(id=key, label=label, settings=[setting.descriptor() for setting in settings])
            for key, label, settings in GROUPS]


def apply_setting(controller, key, value):
    if key not in SETTINGS:
        raise ValueError("Неизвестная настройка.")
    if not SETTINGS[key].is_editable(controller.state):
        if SETTINGS[key].editable_while_paused:
            raise RuntimeError("Для изменения тайминга поставьте рисунок на паузу и завершите активный экранный инструмент.")
        raise RuntimeError("Сначала завершите рисование или активный экранный инструмент.")
    value = SETTINGS[key].validate(value)
    current = controller.service.snapshot_painter_config().get(key)
    if current == value or (isinstance(current, (float, int)) and not isinstance(current, bool)
                            and isinstance(value, float) and math.isclose(current, value, rel_tol=0, abs_tol=1e-12)):
        return False
    getattr(controller.service, "set_" + key)(value)
    return True
