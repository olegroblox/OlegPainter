"""StencilImageItem — изображение как интерактивный элемент сцены.

Важные принципы:
  * Локальная система координат элемента ЦЕНТРИРОВАНА: прямоугольник изображения
    равен (-w/2, -h/2, w, h). Поэтому setPos() задаёт положение ЦЕНТРА картинки
    в координатах сцены, а поворот и масштаб выполняются вокруг центра.
  * boundingRect() возвращает БАЗОВЫЙ размер пиксмапа (без учёта масштаба) —
    масштаб входит в матрицу трансформации элемента. Значит изменение масштаба
    не меняет boundingRect и не требует prepareGeometryChange().
  * Сам элемент рисует только пиксмап. Рамку выделения и маркеры (resize handles)
    рисует CanvasView в координатах viewport, чтобы они имели постоянный размер
    на экране независимо от зума.

Маркеры (ключи): 'tl' 't' 'tr' 'r' 'br' 'b' 'bl' 'l'  (углы и середины сторон).
"""

from __future__ import annotations

import math
from PySide6.QtCore import QPointF, QRectF
from PySide6.QtGui import QPixmap, QPainter, QTransform
from PySide6.QtWidgets import QGraphicsItem

from .geometry import vmul


# Маркер -> его противоположный (для вычисления неподвижной точки при ресайзе)
OPPOSITE = {
    "tl": "br", "br": "tl", "tr": "bl", "bl": "tr",
    "t": "b", "b": "t", "l": "r", "r": "l",
}
CORNERS = {"tl", "tr", "br", "bl"}
HANDLES_X = {"l", "r", "tl", "tr", "bl", "br"}   # маркеры, меняющие ширину
HANDLES_Y = {"t", "b", "tl", "tr", "bl", "br"}   # маркеры, меняющие высоту


class StencilImageItem(QGraphicsItem):
    """Один пиксмап с независимыми position / scale_x / scale_y / rotation / opacity."""

    def __init__(self) -> None:
        super().__init__()
        self._pixmap = QPixmap()
        self._source_path = ""
        self.scale_x = 1.0
        self.scale_y = 1.0
        self.rotation_deg = 0.0
        self.setZValue(0)

    # ------------------------------------------------------------------ #
    #   Данные изображения
    # ------------------------------------------------------------------ #

    def load(self, path: str) -> bool:
        pix = QPixmap(path)
        if pix.isNull():
            return False
        self.prepareGeometryChange()
        self._pixmap = pix
        self._source_path = path
        self.update()
        return True

    def set_pixmap(self, pix: QPixmap, source_path: str = "") -> bool:
        """Load directly from a QPixmap (olegpainter feeds a PIL/clipboard image)."""
        if pix is None or pix.isNull():
            return False
        self.prepareGeometryChange()
        self._pixmap = pix
        self._source_path = source_path
        self.update()
        return True

    @property
    def source_path(self) -> str:
        return self._source_path

    def has_image(self) -> bool:
        return not self._pixmap.isNull()

    def pixmap_size(self) -> tuple[int, int]:
        return self._pixmap.width(), self._pixmap.height()

    # ------------------------------------------------------------------ #
    #   Обязательные методы QGraphicsItem
    # ------------------------------------------------------------------ #

    def boundingRect(self) -> QRectF:
        w = self._pixmap.width()
        h = self._pixmap.height()
        if w == 0 or h == 0:
            return QRectF()
        return QRectF(-w / 2.0, -h / 2.0, w, h)

    def paint(self, painter: QPainter, option, widget=None) -> None:
        if self._pixmap.isNull():
            return
        painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
        w = self._pixmap.width()
        h = self._pixmap.height()
        target = QRectF(-w / 2.0, -h / 2.0, w, h)
        painter.drawPixmap(target, self._pixmap, QRectF(self._pixmap.rect()))

    # ------------------------------------------------------------------ #
    #   Трансформация
    # ------------------------------------------------------------------ #

    def apply_transform(self) -> None:
        """Собирает матрицу (поворот + неравномерный масштаб) вокруг центра.

        Положение центра задаётся отдельно через setPos().
        """
        t = QTransform()
        t.rotate(self.rotation_deg)
        t.scale(self.scale_x, self.scale_y)
        self.setTransform(t)

    def set_geometry(self, center: QPointF, scale_x: float, scale_y: float) -> None:
        self.scale_x = scale_x
        self.scale_y = scale_y
        self.apply_transform()
        self.setPos(center)

    def set_center(self, center: QPointF) -> None:
        self.setPos(center)

    # ------------------------------------------------------------------ #
    #   Геометрия в координатах сцены (для маркеров и попаданий)
    # ------------------------------------------------------------------ #

    def center(self) -> QPointF:
        return self.pos()

    def axes(self) -> tuple[QPointF, QPointF]:
        """Единичные оси изображения (локальные X и Y) в координатах сцены."""
        rad = math.radians(self.rotation_deg)
        cos, sin = math.cos(rad), math.sin(rad)
        u = QPointF(cos, sin)     # локальная ось X
        v = QPointF(-sin, cos)    # локальная ось Y
        return u, v

    def half_extents(self) -> tuple[float, float]:
        """Половины ширины/высоты с учётом масштаба (в единицах сцены)."""
        w = self._pixmap.width() * abs(self.scale_x) / 2.0
        h = self._pixmap.height() * abs(self.scale_y) / 2.0
        return w, h

    def handle_positions(self) -> dict[str, QPointF]:
        """Позиции 8 маркеров в координатах сцены."""
        c = self.center()
        u, v = self.axes()
        hw, hh = self.half_extents()
        hwu = vmul(u, hw)
        hhv = vmul(v, hh)
        return {
            "tl": c - hwu - hhv,
            "t": c - hhv,
            "tr": c + hwu - hhv,
            "r": c + hwu,
            "br": c + hwu + hhv,
            "b": c + hhv,
            "bl": c - hwu + hhv,
            "l": c - hwu,
        }

    def corners(self) -> list[QPointF]:
        """Четыре угла изображения в координатах сцены (по часовой стрелке)."""
        h = self.handle_positions()
        return [h["tl"], h["tr"], h["br"], h["bl"]]

    def contains_scene_point(self, scene_pt: QPointF) -> bool:
        """Лежит ли точка сцены внутри (повёрнутого) прямоугольника изображения."""
        if self._pixmap.isNull():
            return False
        local = self.mapFromScene(scene_pt)  # учитывает pos/rotation/scale
        return self.boundingRect().contains(local)
