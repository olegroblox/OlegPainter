from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterable, Sequence

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from app_paths import get_app_paths
import logging

log = logging.getLogger("olegpainter.ui.helpers.icon_tinter")


_THEME_COLORS: dict[str, QColor] = {
    "dark": QColor(230, 230, 230),
    "light": QColor(13, 13, 13),
}


def resolve_icon_path(paths: str | Path | Iterable[str | Path]) -> Path | None:
    roots = _asset_roots()
    for candidate in _iter_candidates(paths):
        if candidate.is_absolute():
            if candidate.exists():
                return candidate
            continue
        for root in roots:
            resolved = (root / candidate).resolve()
            if resolved.exists():
                return resolved
    return None


def theme_color(theme: str | None) -> QColor:
    return _THEME_COLORS.get((theme or "dark").lower(), _THEME_COLORS["dark"])


def themed_svg_icon(
    paths: str | Path | Iterable[str | Path],
    *,
    theme: str | None = None,
    size: QSize | None = None,
) -> QIcon:
    svg_path = resolve_icon_path(paths)
    if svg_path is None:
        return QIcon()
    icon = load_svg_icon(svg_path, color=theme_color(theme), size=size)
    if not icon.isNull():
        return icon
    return QIcon(str(svg_path))


def load_svg_icon(
    paths: str | Path | Iterable[str | Path],
    *,
    color: QColor | str | int | tuple[int, int, int] | None = None,
    size: QSize | None = None,
) -> QIcon:
    """Load an SVG icon and optionally tint it with the provided color."""
    svg_path = resolve_icon_path(paths)
    if svg_path is None:
        return QIcon()

    renderer = QSvgRenderer(str(svg_path))
    if not renderer.isValid():
        return QIcon(str(svg_path))

    target_size = _resolve_size(size, renderer)
    icon = QIcon()
    for scale in (1, 2, 3):
        # Render the vector again at each device size. Enlarging the 1x bitmap
        # made overlay/Widgets icons soft on scaled Windows displays.
        pixmap = QPixmap(target_size * scale)
        pixmap.fill(Qt.transparent)
        painter = QPainter(pixmap)
        painter.setRenderHint(QPainter.Antialiasing)
        renderer.render(painter)
        if color is not None:
            qcolor = color if isinstance(color, QColor) else QColor(color)
            painter.setCompositionMode(QPainter.CompositionMode_SourceIn)
            painter.fillRect(pixmap.rect(), qcolor)
        painter.end()
        pixmap.setDevicePixelRatio(scale)
        icon.addPixmap(pixmap)
    return icon


def _iter_candidates(paths: str | Path | Iterable[str | Path]) -> Sequence[Path]:
    if isinstance(paths, (str, Path)):
        return (Path(paths),)
    return tuple(Path(p) for p in paths)


def _asset_roots() -> Sequence[Path]:
    paths = get_app_paths()
    roots: list[Path] = [paths.resource_root, paths.app_root]
    if getattr(sys, "frozen", False):
        try:
            roots.append(Path(sys.executable).resolve().parent)
        except Exception:
            log.debug('ignored exception in roots.append(Path(sys.executable).resolve().parent)', exc_info=True)
    unique: list[Path] = []
    for root in roots:
        if not root:
            continue
        try:
            resolved = root.resolve()
        except Exception:
            resolved = root
        if resolved not in unique:
            unique.append(resolved)
    return tuple(unique)


def _resolve_size(size: QSize | None, renderer: QSvgRenderer) -> QSize:
    if size is not None and size.isValid():
        return size
    default = renderer.defaultSize()
    if default.isValid() and default.width() > 0 and default.height() > 0:
        return default
    return QSize(24, 24)
