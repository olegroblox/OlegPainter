"""Render the shared SVG assets at the requested device size for QML."""
import re

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtSvg import QSvgRenderer

from app_paths import get_app_paths


class IconProvider(QQuickImageProvider):
    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self.root = get_app_paths().assets_root / "icons"

    def requestImage(self, identifier, size, requested_size):
        name, _, tint = identifier.partition("/")
        name = {"chevron": "chevron-down", "command": "keyboard"}.get(name, name)
        if not re.fullmatch(r"[a-z][a-z-]*", name):
            return QImage()
        renderer = QSvgRenderer(str(self.root / (name + ".svg")))
        color = QColor("#" + tint)
        if not renderer.isValid() or not color.isValid():
            return QImage()
        target = requested_size if requested_size is not None and requested_size.isValid() else QSize(24, 24)
        target = QSize(min(512, max(1, target.width())), min(512, max(1, target.height())))
        image = QImage(target, QImage.Format_ARGB32_Premultiplied)
        image.fill(Qt.transparent)
        painter = QPainter(image)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter)
        painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
        painter.fillRect(image.rect(), color)
        painter.end()
        if size is not None:
            size.setWidth(image.width())
            size.setHeight(image.height())
        return image
