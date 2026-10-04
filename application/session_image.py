"""Owned image data for persistence; no service or UI objects cross the worker boundary."""
from dataclasses import dataclass
from io import BytesIO

from PIL import Image


@dataclass(frozen=True)
class SessionImage:
    token: tuple
    pixels: Image.Image | None = None
    png: bytes | None = None

    def encode(self) -> bytes:
        if self.png is not None:
            return self.png
        if self.pixels is None:
            raise ValueError("Не удалось получить исходное изображение для сохранения сессии.")
        buffer = BytesIO()
        self.pixels.save(buffer, format="PNG")
        return buffer.getvalue()
