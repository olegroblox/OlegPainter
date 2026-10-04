"""Picture filters without neural networks (IMAGE-EDIT-002).

Pillow and OpenCV only, offline and quick. Every function takes and returns an
RGBA picture of the same size (except rotation and trimming) and keeps the
transparency: a removed background stays removed.
"""
from __future__ import annotations

from typing import Callable

import numpy as np
from PIL import Image, ImageEnhance, ImageFilter, ImageOps

ADJUST_KEYS = ("brightness", "contrast", "saturation", "sharpness")
ADJUST_RANGE = 100              # sliders go -100..100; 0 changes nothing


def _split(image: Image.Image) -> tuple[Image.Image, Image.Image]:
    rgba = image.convert("RGBA")
    return rgba.convert("RGB"), rgba.getchannel("A")


def _join(rgb: Image.Image, alpha: Image.Image) -> Image.Image:
    out = rgb.convert("RGB").convert("RGBA")
    out.putalpha(alpha)
    return out


def adjust(image: Image.Image, values: dict) -> Image.Image:
    """Brightness, contrast, saturation and sharpness, each -100..100."""
    rgb, alpha = _split(image)
    enhancers = {"brightness": ImageEnhance.Brightness, "contrast": ImageEnhance.Contrast,
                 "saturation": ImageEnhance.Color, "sharpness": ImageEnhance.Sharpness}
    for key in ADJUST_KEYS:
        value = float(values.get(key, 0) or 0)
        if not -ADJUST_RANGE <= value <= ADJUST_RANGE:
            raise ValueError("Значение должно быть от -100 до 100.")
        if value:
            # -100 halves (or blurs, for sharpness), +100 doubles: symmetric to the eye
            factor = 2.0 ** (value / ADJUST_RANGE) if key != "sharpness" else 1.0 + 2.0 * value / ADJUST_RANGE
            rgb = enhancers[key](rgb).enhance(max(0.0, factor))
    return _join(rgb, alpha)


def is_neutral(values: dict) -> bool:
    return not any(float(values.get(key, 0) or 0) for key in ADJUST_KEYS)


# ----- one-click filters --------------------------------------------------------
def _auto(image):
    rgb, alpha = _split(image)
    return _join(ImageOps.autocontrast(rgb, cutoff=1), alpha)


def _grayscale(image):
    rgb, alpha = _split(image)
    return _join(ImageOps.grayscale(rgb), alpha)


def _sepia(image):
    rgb, alpha = _split(image)
    gray = ImageOps.grayscale(rgb)
    return _join(ImageOps.colorize(gray, black=(40, 26, 13), mid=(160, 120, 80), white=(255, 240, 210)), alpha)


def _temperature(image, warm: bool):
    rgb, alpha = _split(image)
    a = np.asarray(rgb, dtype=np.float32)
    shift = np.array([18, 6, -18] if warm else [-18, 0, 18], dtype=np.float32)
    return _join(Image.fromarray(np.clip(a + shift, 0, 255).astype(np.uint8)), alpha)


def _invert(image):
    rgb, alpha = _split(image)
    return _join(ImageOps.invert(rgb), alpha)


def _posterize(image):
    rgb, alpha = _split(image)
    return _join(ImageOps.posterize(rgb, 3), alpha)


def _cartoon(image):
    """Flat colours with dark outlines: reads well as a drawing."""
    import cv2
    rgb, alpha = _split(image)
    a = np.asarray(rgb)
    scale = min(1.0, 1200.0 / max(a.shape[:2]))
    small = cv2.resize(a, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1.0 else a
    smooth = small
    for _ in range(3):
        smooth = cv2.bilateralFilter(smooth, 9, 60, 9)
    gray = cv2.medianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), 5)
    edges = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_MEAN_C, cv2.THRESH_BINARY, 9, 4)
    out = cv2.bitwise_and(smooth, smooth, mask=edges)
    if scale < 1.0:
        out = cv2.resize(out, (a.shape[1], a.shape[0]), interpolation=cv2.INTER_LINEAR)
    return _join(Image.fromarray(out), alpha)


def _pixelate(image):
    """Big square pixels (about 64 across the longer side)."""
    rgba = image.convert("RGBA")
    w, h = rgba.size
    step = max(2, round(max(w, h) / 64))
    small = rgba.resize((max(1, w // step), max(1, h // step)), Image.BOX)
    return small.resize((w, h), Image.NEAREST)


def _blur(image):
    rgb, alpha = _split(image)
    radius = max(1.0, max(image.size) / 400.0)
    return _join(rgb.filter(ImageFilter.GaussianBlur(radius)), alpha)


def _sharpen(image):
    rgb, alpha = _split(image)
    return _join(rgb.filter(ImageFilter.UnsharpMask(radius=2, percent=150, threshold=3)), alpha)


def _simplify(image):
    from engine.ai import vision          # classic filters, no network
    return vision.flatten(image, strength=2)


def _rotate(image):
    return image.convert("RGBA").transpose(Image.Transpose.ROTATE_270)      # clockwise


def _trim(image):
    """Cut transparent margins, so the object fills the drawing area."""
    rgba = image.convert("RGBA")
    box = rgba.getchannel("A").point(lambda v: 255 if v > 8 else 0).getbbox()
    if box is None:
        raise ValueError("Картинка полностью прозрачная — обрезать нечего.")
    if box == (0, 0, rgba.width, rgba.height):
        raise ValueError("Пустых краёв нет: сначала уберите фон.")
    return rgba.crop(box)


# id: (group, label, hint, function)
FILTERS: dict[str, tuple[str, str, str, Callable[[Image.Image], Image.Image]]] = {
    "auto": ("filters", "Авто-улучшение", "Растянуть тона: тусклое фото станет контрастнее", _auto),
    "grayscale": ("filters", "Чёрно-белое", "Только оттенки серого", _grayscale),
    "sepia": ("filters", "Сепия", "Тёплые коричневые тона старого фото", _sepia),
    "warm": ("filters", "Тёплый", "Сдвинуть цвета к жёлтому и красному", lambda i: _temperature(i, True)),
    "cold": ("filters", "Холодный", "Сдвинуть цвета к синему", lambda i: _temperature(i, False)),
    "invert": ("filters", "Инверсия", "Негатив: каждый цвет меняется на противоположный", _invert),
    "posterize": ("filters", "Меньше оттенков", "Резкие переходы вместо плавных: быстрее рисуется", _posterize),
    "cartoon": ("filters", "Мультяшно", "Ровные цвета и тёмные контуры", _cartoon),
    "pixelate": ("filters", "Пиксели", "Крупные квадратные пиксели, как в пиксель-арте", _pixelate),
    "simplify": ("filters", "Упростить", "Крупные ровные пятна вместо шума фото — меньше цветов и быстрее рисунок", _simplify),
    "blur": ("filters", "Размыть", "Смягчить мелкие детали и шум", _blur),
    "sharpen": ("filters", "Резче", "Чётче края и мелкие детали", _sharpen),
    "rotate": ("shape", "Повернуть", "Повернуть по часовой стрелке на 90°", _rotate),
    "trim": ("shape", "Обрезать пустые края", "После удаления фона фигура займёт всю область рисования", _trim),
}


def apply_filter(image: Image.Image, filter_id: str) -> Image.Image:
    if filter_id not in FILTERS:
        raise ValueError("Неизвестный фильтр.")
    return FILTERS[filter_id][3](image)


def catalog() -> list[dict]:
    return [dict(id=key, group=group, label=label, hint=hint) for key, (group, label, hint, _fn) in FILTERS.items()]
