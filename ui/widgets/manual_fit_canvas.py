from __future__ import annotations

from typing import Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QSize, QTimer, Signal
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap, QPalette
from PySide6.QtWidgets import QWidget

from ui.i18n import tr


class ManualFitCanvas(QWidget):
    """Interactive canvas for manual image placement within the drawing area."""

    rectChanged = Signal(tuple)
    rectChangeFinished = Signal(tuple)

    _HANDLE_SIZE = 12.0
    _MIN_SIZE = 16.0
    _MARGIN = 16.0

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setMouseTracking(True)
        self.setMinimumSize(QSize(220, 180))

        self._area_w: float = 0.0
        self._area_h: float = 0.0
        self._scale: float = 1.0
        self._area_rect: QRectF = QRectF()
        self._size_limit: Optional[float] = None
        self._viewport_rect: Optional[QRectF] = None

        self._pixmap: QPixmap = QPixmap()
        self._auto_mode: bool = True

        self._rect: Optional[tuple[float, float, float, float]] = None

        self._drag_active: bool = False
        self._drag_mode: str = ""
        self._drag_anchor: str = ""
        self._drag_start_pos: QPointF = QPointF()
        self._drag_start_rect: tuple[float, float, float, float] | None = None
        self._rect_mode: str = "fit"  # fit | actual | custom

        # Дебаунс «конца жеста» зума колесом: один rectChangeFinished
        # после ПОСЛЕДНЕГО тика, как у драга (а не на каждый тик).
        self._zoom_commit_timer = QTimer(self)
        self._zoom_commit_timer.setSingleShot(True)
        self._zoom_commit_timer.setInterval(190)
        self._zoom_commit_timer.timeout.connect(self._emit_zoom_finished)

    # ------------------------------------------------------------------ Public API

    def setAreaSize(self, width: float, height: float) -> None:
        self._area_w = max(0.0, float(width))
        self._area_h = max(0.0, float(height))
        self._ensure_rect_initialized()
        self.update()

    def setSizeLimit(self, limit: Optional[float]) -> None:
        if limit is not None:
            try:
                limit = max(self._MIN_SIZE, float(limit))
            except Exception:
                limit = None
        if self._size_limit == limit:
            return
        self._size_limit = limit
        if self._rect is not None:
            x, y, w, h = self._rect
            clamped = (x, y, self._bounded_size(w), self._bounded_size(h))
            if clamped != self._rect:
                self._rect = clamped
                self.rectChanged.emit(clamped)
                self.rectChangeFinished.emit(clamped)
            self.update()

    def setViewportRect(self, rect: Optional[QRectF]) -> None:
        if rect is None or rect.isNull():
            self._viewport_rect = None
        else:
            self._viewport_rect = QRectF(rect)
        self.update()

    def areaSize(self) -> tuple[float, float]:
        return self._area_w, self._area_h

    def setPixmap(self, pixmap: QPixmap | None) -> None:
        if isinstance(pixmap, QPixmap) and not pixmap.isNull():
            self._pixmap = pixmap
        else:
            self._pixmap = QPixmap()
        self.update()

    def setAutoFit(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if self._auto_mode != enabled:
            self._auto_mode = enabled
            self.update()

    def isAutoFit(self) -> bool:
        return self._auto_mode

    def setRect(self, rect: Optional[tuple[float, float, float, float]]) -> None:
        if rect is None:
            self._rect = None
            self._rect_mode = "fit"
        else:
            x, y, w, h = rect
            self._rect = (
                float(x),
                float(y),
                self._bounded_size(w),
                self._bounded_size(h),
            )
            self._rect_mode = "custom"
        self._ensure_rect_initialized()
        self.update()

    def manualRect(self) -> Optional[tuple[float, float, float, float]]:
        return self._rect

    # ------------------------------------------------------------------ QWidget overrides

    def sizeHint(self) -> QSize:
        return QSize(320, 240)

    def minimumSizeHint(self) -> QSize:
        return QSize(220, 180)

    def paintEvent(self, event) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)

        palette = self.palette()
        painter.fillRect(self.rect(), palette.window())

        if self._area_w <= 0 or self._area_h <= 0:
            self._draw_placeholder(painter, tr("draw_manual_fit_no_area"))
            return

        area_rect = self._compute_area_rect()
        painter.save()
        overlay_path = QPainterPath()
        overlay_path.addRect(QRectF(self.rect()))
        overlay_path2 = QPainterPath()
        overlay_path2.addRect(area_rect)
        overlay_path = overlay_path.subtracted(overlay_path2)
        painter.fillPath(overlay_path, QColor(0, 0, 0, 60))
        painter.restore()

        if self._pixmap.isNull():
            self._draw_placeholder(painter, tr("draw_manual_fit_no_image"), area_rect)
            return

        src_rect = QRectF(self._pixmap.rect())

        if self._auto_mode:
            painter.drawPixmap(area_rect, self._pixmap, src_rect)
            self._draw_hint(painter, area_rect, tr("draw_manual_fit_auto_hint"))
            return

        rect = self._ensure_rect_initialized()
        if rect is None:
            return

        target = self._rect_to_widget(rect)
        painter.drawPixmap(target, self._pixmap, src_rect)

        painter.setPen(QPen(QColor(120, 200, 255), 1.4, Qt.SolidLine))
        painter.drawRect(target)

        self._draw_handles(painter, target)
        self._draw_metrics(painter, target)

    def mousePressEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.RightButton:
            event.ignore()
            return

        if self._auto_mode or not self._rect or self._scale <= 0.0:
            super().mousePressEvent(event)
            return

        if event.button() != Qt.LeftButton:
            super().mousePressEvent(event)
            return

        hit = self._hit_test(event.position())
        if hit not in {"move", "nw", "ne", "sw", "se"}:
            super().mousePressEvent(event)
            return

        self._drag_active = True
        self._drag_mode = hit
        self._drag_anchor = hit
        self._drag_start_pos = event.position()
        self._drag_start_rect = self._rect
        self._rect_mode = "custom"
        event.accept()

    def mouseMoveEvent(self, event) -> None:  # noqa: N802
        if event.buttons() & Qt.RightButton:
            event.ignore()
            return

        if self._auto_mode or self._scale <= 0.0:
            super().mouseMoveEvent(event)
            return

        if self._drag_active and self._drag_start_rect is not None:
            delta = (event.position() - self._drag_start_pos) / self._scale
            self._apply_drag(delta.x(), delta.y())
            event.accept()
            return

        cursor = Qt.ArrowCursor
        hit = self._hit_test(event.position())
        if hit == "move":
            cursor = Qt.SizeAllCursor
        elif hit == "nw" or hit == "se":
            cursor = Qt.SizeFDiagCursor
        elif hit == "ne" or hit == "sw":
            cursor = Qt.SizeBDiagCursor
        self.setCursor(cursor)

        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:  # noqa: N802
        if event.button() == Qt.RightButton:
            event.ignore()
            return

        if self._drag_active and event.button() == Qt.LeftButton:
            self._drag_active = False
            if self._rect:
                self.rectChangeFinished.emit(self._rect)
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def wheelEvent(self, event) -> None:  # noqa: N802
        if self._auto_mode or not self._rect or self._scale <= 0.0:
            super().wheelEvent(event)
            return

        delta_y = event.angleDelta().y()
        if delta_y == 0:
            super().wheelEvent(event)
            return

        factor = 1.0 + (delta_y / 1200.0)
        factor = max(0.2, min(5.0, factor))
        if abs(factor - 1.0) < 0.001:
            super().wheelEvent(event)
            return

        self._apply_zoom(factor, event.position())
        event.accept()

    def mouseDoubleClickEvent(self, event) -> None:  # noqa: N802
        if self._auto_mode or event.button() != Qt.LeftButton:
            super().mouseDoubleClickEvent(event)
            return
        toggled = False
        if self._rect_mode == "actual":
            toggled = self.resetRectToFit()
        else:
            toggled = self.resetRectToActualSize()
            if not toggled:
                toggled = self.resetRectToFit()
        if toggled:
            event.accept()
            return
        super().mouseDoubleClickEvent(event)

    # ------------------------------------------------------------------ Internal helpers

    def _draw_placeholder(self, painter: QPainter, text: str, rect: QRectF | None = None) -> None:
        target = rect if rect is not None else QRectF(self.rect())
        painter.save()
        painter.setPen(QPen(QColor(210, 210, 210), 1, Qt.DotLine))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(target)
        painter.setPen(QColor(160, 160, 160))
        painter.drawText(target, Qt.AlignCenter | Qt.TextWordWrap, text)
        painter.restore()

    def _draw_hint(self, painter: QPainter, area_rect: QRectF, text: str) -> None:
        painter.save()
        hint_rect = QRectF(area_rect.left(), area_rect.top(), area_rect.width(), 32.0)
        painter.setPen(QColor(0, 0, 0, 120))
        painter.setBrush(QColor(255, 255, 255, 200))
        painter.drawRoundedRect(hint_rect, 6, 6)
        painter.setPen(QColor(30, 30, 30))
        painter.drawText(hint_rect, Qt.AlignCenter, text)
        painter.restore()

    def _draw_handles(self, painter: QPainter, rect: QRectF) -> None:
        handle = self._HANDLE_SIZE
        half = handle / 2.0
        painter.save()
        painter.setPen(QPen(QColor(255, 255, 255), 1))
        painter.setBrush(QColor(59, 173, 255))
        for point in self._handle_points(rect):
            painter.drawRect(QRectF(point.x() - half, point.y() - half, handle, handle))
        painter.restore()

    def _draw_metrics(self, painter: QPainter, rect: QRectF) -> None:
        if not self._rect:
            return
        text = f"{int(self._rect[2])} × {int(self._rect[3])} px | offset {int(self._rect[0])}, {int(self._rect[1])}"
        metrics = painter.fontMetrics()
        size = metrics.size(Qt.TextSingleLine, text)
        box = QRectF(rect.left(), rect.bottom() + 8, size.width() + 12, size.height() + 8)
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 160))
        painter.drawRoundedRect(box, 6, 6)
        painter.setPen(QColor(240, 240, 240))
        painter.drawText(box, Qt.AlignCenter, text)
        painter.restore()

    def _ensure_rect_initialized(self) -> Optional[tuple[float, float, float, float]]:
        if self._rect is None and self._area_w > 0 and self._area_h > 0:
            self._rect = (
                0.0,
                0.0,
                self._bounded_size(self._area_w),
                self._bounded_size(self._area_h),
            )
            self._rect_mode = "fit"
        return self._rect

    def _compute_area_rect(self) -> QRectF:
        if self._area_w <= 0 or self._area_h <= 0:
            self._scale = 1.0
            self._area_rect = QRectF()
            return self._area_rect

        if self._viewport_rect is not None and not self._viewport_rect.isNull():
            area_rect = QRectF(self._viewport_rect)
            scale_w = area_rect.width() / max(self._area_w, 1e-6)
            scale_h = area_rect.height() / max(self._area_h, 1e-6)
            self._scale = max((scale_w + scale_h) * 0.5, 1e-6)
            self._area_rect = area_rect
            return self._area_rect

        avail_w = max(1.0, self.width() - self._MARGIN * 2)
        avail_h = max(1.0, self.height() - self._MARGIN * 2)
        scale = min(avail_w / self._area_w, avail_h / self._area_h)
        self._scale = max(scale, 0.001)

        target_w = self._area_w * self._scale
        target_h = self._area_h * self._scale
        left = (self.width() - target_w) / 2.0
        top = (self.height() - target_h) / 2.0

        self._area_rect = QRectF(left, top, target_w, target_h)
        return self._area_rect

    def _rect_to_widget(self, rect: tuple[float, float, float, float]) -> QRectF:
        area_rect = self._compute_area_rect()
        x, y, w, h = rect
        left = area_rect.left() + x * self._scale
        top = area_rect.top() + y * self._scale
        width = w * self._scale
        height = h * self._scale
        return QRectF(left, top, width, height)

    def _handle_points(self, rect: QRectF) -> list[QPointF]:
        return [
            QPointF(rect.left(), rect.top()),
            QPointF(rect.right(), rect.top()),
            QPointF(rect.left(), rect.bottom()),
            QPointF(rect.right(), rect.bottom()),
        ]

    def _hit_test(self, pos: QPointF) -> Optional[str]:
        if self._rect is None:
            return None
        rect = self._rect_to_widget(self._rect)
        handle = self._HANDLE_SIZE
        half = handle / 2.0
        handles = {
            "nw": QPointF(rect.left(), rect.top()),
            "ne": QPointF(rect.right(), rect.top()),
            "sw": QPointF(rect.left(), rect.bottom()),
            "se": QPointF(rect.right(), rect.bottom()),
        }
        for name, point in handles.items():
            box = QRectF(point.x() - half, point.y() - half, handle, handle)
            if box.contains(pos):
                return name
        if rect.contains(pos):
            return "move"
        return None

    def _apply_drag(self, dx: float, dy: float) -> None:
        if self._rect is None or self._drag_start_rect is None or not self._drag_mode:
            return

        x, y, w, h = self._drag_start_rect
        min_size = self._MIN_SIZE

        if self._drag_mode == "move":
            new_rect = (x + dx, y + dy, w, h)
        else:
            left = x
            top = y
            right = x + w
            bottom = y + h

            if "n" in self._drag_mode:
                top = min(top + dy, bottom - min_size)
            if "s" in self._drag_mode:
                bottom = max(bottom + dy, top + min_size)
            if "w" in self._drag_mode:
                left = min(left + dx, right - min_size)
            if "e" in self._drag_mode:
                right = max(right + dx, left + min_size)

            new_rect = (left, top, right - left, bottom - top)

        self._rect = new_rect
        self._rect_mode = "custom"
        self.rectChanged.emit(new_rect)
        self.update()

    def _apply_zoom(self, factor: float, focus_pos: QPointF | None = None) -> None:
        rect = self._ensure_rect_initialized()
        if rect is None or self._scale <= 0.0 or self._area_w <= 0.0 or self._area_h <= 0.0:
            return

        area_rect = self._compute_area_rect()
        x, y, w, h = rect

        if focus_pos is None or not area_rect.contains(focus_pos):
            focus_pos = QPointF(area_rect.center())

        focus_area_x = (focus_pos.x() - area_rect.left()) / self._scale
        focus_area_y = (focus_pos.y() - area_rect.top()) / self._scale

        rel_x = 0.5 if w <= 0 else max(0.0, min(1.0, (focus_area_x - x) / max(w, 1e-6)))
        rel_y = 0.5 if h <= 0 else max(0.0, min(1.0, (focus_area_y - y) / max(h, 1e-6)))

        new_w = self._bounded_size(w * factor)
        new_h = self._bounded_size(h * factor)

        new_x = focus_area_x - rel_x * new_w
        new_y = focus_area_y - rel_y * new_h

        new_rect = (new_x, new_y, new_w, new_h)
        self._rect = new_rect
        self._rect_mode = "custom"
        self.rectChanged.emit(new_rect)
        # Не эмитим rectChangeFinished на каждый тик колеса — перезапускаем
        # таймер, который выстрелит один раз после последнего тика (конец жеста).
        self._zoom_commit_timer.start()
        self.update()

    def _emit_zoom_finished(self) -> None:
        # Истинный конец жеста зума колесом: единичный rectChangeFinished.
        if self._rect is not None:
            self.rectChangeFinished.emit(self._rect)

    def resetRectToFit(self) -> Optional[tuple[float, float, float, float]]:
        rect = self._fit_rect()
        if rect is None:
            return None
        self._apply_rect_direct(rect, "fit")
        return rect

    def resetRectToActualSize(self) -> Optional[tuple[float, float, float, float]]:
        rect = self._actual_size_rect()
        if rect is None:
            return None
        self._apply_rect_direct(rect, "actual")
        return rect

    def _apply_rect_direct(self, rect: tuple[float, float, float, float], mode: str) -> None:
        x, y, w, h = rect
        clamped = (x, y, self._bounded_size(w), self._bounded_size(h))
        self._rect = clamped
        self._rect_mode = mode
        self.rectChanged.emit(clamped)
        self.rectChangeFinished.emit(clamped)
        self.update()

    def _fit_rect(self) -> Optional[tuple[float, float, float, float]]:
        if self._area_w <= 0 or self._area_h <= 0:
            return None
        return (
            0.0,
            0.0,
            self._bounded_size(self._area_w),
            self._bounded_size(self._area_h),
        )

    def _actual_size_rect(self) -> Optional[tuple[float, float, float, float]]:
        if self._area_w <= 0 or self._area_h <= 0 or self._pixmap.isNull():
            return None
        img_w = float(self._pixmap.width())
        img_h = float(self._pixmap.height())
        width = min(self._area_w, max(self._MIN_SIZE, img_w))
        height = min(self._area_h, max(self._MIN_SIZE, img_h))
        left = max(0.0, (self._area_w - width) / 2.0)
        top = max(0.0, (self._area_h - height) / 2.0)
        return (
            left,
            top,
            self._bounded_size(width),
            self._bounded_size(height),
        )

    def _bounded_size(self, value: float) -> float:
        size = max(self._MIN_SIZE, float(value))
        if self._size_limit is not None:
            size = min(self._size_limit, size)
        return size
