"""Validation for manual color mixing; no Qt or screen I/O."""
import math
import re


def valid_alpha_slider(value):
    return (isinstance(value, (list, tuple)) and len(value) == 4
            and value[0] in ("horizontal", "vertical")
            and all(type(n) in (int, float) and math.isfinite(n) and -(2**31) <= n < 2**31
                    for n in value[1:])
            and abs(value[2] - value[3]) >= 5)


def alpha_slider_from_points(points):
    if (not isinstance(points, (list, tuple)) or len(points) != 2
            or any(not isinstance(p, (list, tuple)) or len(p) != 2
                   or any(type(n) is not int or not -(2**31) <= n < 2**31 for n in p) for p in points)):
        raise ValueError("Укажите две точки ползунка в экранных пикселях.")
    (x0, y0), (x1, y1) = points
    dx, dy = abs(x1 - x0), abs(y1 - y0)
    if max(dx, dy) < 5 or min(dx, dy) > max(3, max(dx, dy) * .05):
        raise ValueError("Точки должны лежать на одном горизонтальном или вертикальном ползунке. Повторите калибровку.")
    return (("horizontal", (y0 + y1) / 2, float(x1), float(x0)) if dx >= dy
            else ("vertical", (x0 + x1) / 2, float(y1), float(y0)))


def validate_mix_patch(patch):
    if not isinstance(patch, dict) or set(patch) - {"enabled", "alpha", "canvas_hex"}:
        raise ValueError("Неизвестная настройка смешивания.")
    values = dict(patch)
    if "enabled" in values and type(values["enabled"]) is not bool:
        raise ValueError("Включение смешивания должно быть логическим значением.")
    if "alpha" in values:
        n = values["alpha"]
        if type(n) not in (float, int) or not math.isfinite(n) or not 0 <= n <= 1:
            raise ValueError("Непрозрачность должна быть числом от 0 до 1.")
    if "canvas_hex" in values:
        color = values.pop("canvas_hex")
        if not isinstance(color, str) or not re.fullmatch(r"#?[0-9a-fA-F]{6}", color.strip()):
            raise ValueError("Введите цвет холста в формате #RRGGBB.")
        color = color.strip().removeprefix("#")
        values["canvas_rgb"] = tuple(int(color[n:n + 2], 16) for n in (0, 2, 4))
    return values
