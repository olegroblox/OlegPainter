"""Image conversion utilities shared by PainterService and its mixins."""
from __future__ import annotations

from PIL import Image, ImageQt
from PySide6.QtGui import QImage


def pil_to_qimage(pil_img: Image.Image | None) -> QImage:
    """Convert a PIL Image to a Qt QImage. Returns empty QImage if `pil_img` is None."""
    if pil_img is None:
        return QImage()
    if pil_img.mode != "RGBA":
        pil_img = pil_img.convert("RGBA")
    try:
        return ImageQt.toqimage(pil_img)
    except AttributeError:
        return QImage(ImageQt.ImageQt(pil_img))


def qimage_to_pil(qimg: QImage | None) -> Image.Image | None:
    """Convert a Qt QImage to a PIL Image (RGBA). Returns None for a null image."""
    if qimg is None or qimg.isNull():
        return None
    try:
        return ImageQt.fromqimage(qimg).convert("RGBA")
    except Exception:
        return None
