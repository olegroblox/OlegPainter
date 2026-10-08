"""Quality presets «Быстро / Баланс / Точно» (PRESETS-001).

The old «Шаблоны» and «Быстрый / Сбалансированный / Детальный» of 1.3 in the
current engine's terms. A preset only touches picture preparation and the
colour order; learned brush sizes and input timings of the program stay as
they are. «Баланс» is the preparation every place starts with
(place_catalog.recommended_profile, PRESET-BASE-001), so a new place shows it selected.
Qt-free: the shell passes the controller, writes go through
application.settings.apply_setting like any other change.
"""
from __future__ import annotations

from application.settings import SETTINGS, apply_setting

QUALITY_PRESETS = (
    dict(id="fast", label="Быстро",
         detail="8 цветов и сильная очистка мелочи: рисует быстрее, мелких деталей меньше.",
         values={"k_clusters": 8, "prep_cleanup_mode": "vectorized_plus_stray_merge",
                 "aggressive_despeckle_enabled": True, "tone_sequence": "details_last",
                 "post_draw_repair_enabled": False}),
    dict(id="balanced", label="Баланс",
         detail="25 цветов, убираются только пятна до 4 клеток: подходит большинству картинок.",
         # «Очистка» halves the specks left on cartoons without visible losses
         # (cat 13 -> 6, chick 31 -> 9 at area 4; area 8 already ate small icons).
         values={"k_clusters": 25, "prep_cleanup_mode": "vectorized",
                 "aggressive_despeckle_enabled": False, "tone_sequence": "details_last",
                 "post_draw_repair_enabled": False}),
    dict(id="precise", label="Точно",
         detail="32 цвета без очистки мелочи и проверка холста после рисунка: дольше, зато точнее.",
         values={"k_clusters": 32, "prep_cleanup_mode": "off",
                 "aggressive_despeckle_enabled": False, "tone_sequence": "details_last",
                 "post_draw_repair_enabled": True}),
)


def detect(config: dict, *, auto_colors: bool = False) -> str:
    """The preset the current settings match, or "custom". While new pictures get
    their own colour count (COLORS-AUTO-001) the count is not part of the match."""
    for preset in QUALITY_PRESETS:
        if all(config.get(key) == value for key, value in preset["values"].items()
               if not (auto_colors and key == "k_clusters")):
            return preset["id"]
    return "custom"


def apply(controller, preset_id: str) -> None:
    preset = next((p for p in QUALITY_PRESETS if p["id"] == preset_id), None)
    if preset is None:
        raise ValueError("Неизвестный пресет качества.")
    # All or nothing: a half-applied preset would match no tile.
    if not all(SETTINGS[key].is_editable(controller.state) for key in preset["values"]):
        raise RuntimeError("Сначала завершите рисование или активный экранный инструмент.")
    for key, value in preset["values"].items():
        apply_setting(controller, key, value)


def view() -> list[dict]:
    return [dict(id=p["id"], label=p["label"], detail=p["detail"]) for p in QUALITY_PRESETS]
