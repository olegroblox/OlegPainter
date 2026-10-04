# -*- coding: utf-8 -*-
"""Professional, framework-based editor for the draw area / stencil.

This replaces the hand-rolled QPainter + manual-hit-test overlay with Qt's
**Graphics View Framework** (``QGraphicsView`` / ``QGraphicsScene`` /
``QGraphicsItem``) — the standard, modern way to build interactive, directly
manipulable canvas editors (the same model PureRef-style tools use).

Design goals (all live / direct manipulation, NO "Apply" button):
- Drag the body to move, drag the 8 handles to resize (Shift = keep aspect).
- The item lives in scene coordinates == desktop pixels, so emitted geometry is
  ready to use as the engine's ``draw_region`` directly.
- ``DrawAreaItem.geometryChanged`` fires continuously while dragging, so the rest
  of the app updates in real time.

Geometry math is delegated to the already-unit-tested
:class:`ui.helpers.area_interaction.AreaInteractionController`, so the tricky bits
(hit-testing, clamping, aspect lock) stay testable while Qt handles the scene,
events and coordinate transforms.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QRectF, QPointF, Signal
from PySide6.QtGui import QPixmap, QPainter, QPen, QColor
from PySide6.QtWidgets import QGraphicsObject, QGraphicsItem, QGraphicsScene, QGraphicsView

from ui.helpers.area_interaction import (
    AreaInteractionController,
    Rect as ARect,
    cursor_for_region,
    CURSOR_MOVE,
    CURSOR_SIZE_VER,
    CURSOR_SIZE_HOR,
    CURSOR_SIZE_FDIAG,
    CURSOR_SIZE_BDIAG,
)
from ui.helpers.viewport_mapper import desktop_rect_to_ui_rect, qrect_from_tuple

log = logging.getLogger("olegpainter.board_editor")

_CURSOR_MAP = {
    CURSOR_MOVE: Qt.SizeAllCursor,
    CURSOR_SIZE_VER: Qt.SizeVerCursor,
    CURSOR_SIZE_HOR: Qt.SizeHorCursor,
    CURSOR_SIZE_FDIAG: Qt.SizeFDiagCursor,
    CURSOR_SIZE_BDIAG: Qt.SizeBDiagCursor,
}


class DrawAreaItem(QGraphicsObject):
    """A movable/resizable rectangle that shows the image ghost and exposes its
    geometry (in scene == desktop coordinates) live via ``geometryChanged``."""

    geometryChanged = Signal(QRectF)
    areaDragFinished = Signal()                       # mouse released after a move/resize
    cropApplied = Signal(float, float, float, float)  # view-normalized crop (l,t,r,b)

    HANDLE = 12.0

    def __init__(self, rect: QRectF, pixmap: QPixmap | None = None) -> None:
        super().__init__()
        self._rect = QRectF(rect)
        self._pixmap = pixmap
        self._opacity = 0.55
        self._ctrl = AreaInteractionController(handle_size=self.HANDLE, min_size=12.0, snap=0.0)
        self._drag_region = ""
        self._start_scene = QPointF()
        self._start_rect = QRectF()
        # crop sub-mode (PureRef-style: drag the edges to crop the source)
        self._crop_mode = False
        self._crop_rect = QRectF()
        self._crop_ctrl = AreaInteractionController(handle_size=self.HANDLE, min_size=8.0, snap=0.0)
        self._crop_region = ""
        self._crop_start_scene = QPointF()
        self._crop_start_rect = QRectF()
        self.setAcceptHoverEvents(True)
        self.setFlag(QGraphicsItem.ItemIsSelectable, True)

    # ----- geometry ------------------------------------------------------- #
    def boundingRect(self) -> QRectF:
        m = self.HANDLE
        return self._rect.adjusted(-m, -m, m, m)

    def rect(self) -> QRectF:
        return QRectF(self._rect)

    def setRect(self, rect: QRectF) -> None:
        new = QRectF(rect).normalized()
        if new == self._rect:
            return
        self.prepareGeometryChange()
        self._rect = new
        self.update()
        self.geometryChanged.emit(QRectF(self._rect))

    def setGhostPixmap(self, pixmap: QPixmap | None) -> None:
        self._pixmap = pixmap
        self.update()

    def setGhostOpacity(self, opacity: float) -> None:
        self._opacity = max(0.05, min(1.0, float(opacity)))
        self.update()

    # ----- crop sub-mode -------------------------------------------------- #
    def set_crop_mode(self, on: bool) -> None:
        on = bool(on)
        if on == self._crop_mode:
            return
        self._crop_mode = on
        if on:
            self._crop_rect = QRectF(self._rect)
        self._crop_region = ""
        self.update()

    def is_cropping(self) -> bool:
        return self._crop_mode

    def _crop_region_at(self, pos: QPointF) -> str:
        c = self._crop_rect
        if c.isNull():
            return ""
        return self._crop_ctrl.hit_test(pos.x(), pos.y(), ARect(c.x(), c.y(), c.width(), c.height()))

    def apply_crop_drag(self, scene_pos: QPointF) -> None:
        """Resolve the current crop-handle drag (confined to the area). Testable."""
        if not self._crop_region:
            return
        sr = self._crop_start_rect
        self._crop_ctrl.begin(
            self._crop_start_scene.x(), self._crop_start_scene.y(), self._crop_region,
            ARect(sr.x(), sr.y(), sr.width(), sr.height()),
        )
        r = self._rect
        bounds = ARect(r.x(), r.y(), r.width(), r.height())
        new = self._crop_ctrl.update(scene_pos.x(), scene_pos.y(), bounds, keep_aspect=False, snap=False)
        self._crop_rect = QRectF(new.x, new.y, new.w, new.h)
        self.update()

    def _emit_crop(self) -> None:
        r, c = self._rect, self._crop_rect
        if r.width() <= 0 or r.height() <= 0 or c.isNull():
            return
        vl = (c.left() - r.left()) / r.width()
        vt = (c.top() - r.top()) / r.height()
        vr = (c.right() - r.left()) / r.width()
        vb = (c.bottom() - r.top()) / r.height()
        vl = min(max(0.0, vl), 1.0); vr = min(max(0.0, vr), 1.0)
        vt = min(max(0.0, vt), 1.0); vb = min(max(0.0, vb), 1.0)
        if vr - vl < 1e-3 or vb - vt < 1e-3:
            return
        if (round(vl, 4), round(vt, 4), round(vr, 4), round(vb, 4)) == (0.0, 0.0, 1.0, 1.0):
            return
        self.cropApplied.emit(vl, vt, vr, vb)

    def _paint_crop(self, painter: QPainter) -> None:
        r, c = self._rect, self._crop_rect
        if c.isNull():
            return
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 130))
        painter.drawRect(QRectF(r.left(), r.top(), r.width(), c.top() - r.top()))
        painter.drawRect(QRectF(r.left(), c.bottom(), r.width(), r.bottom() - c.bottom()))
        painter.drawRect(QRectF(r.left(), c.top(), c.left() - r.left(), c.height()))
        painter.drawRect(QRectF(c.right(), c.top(), r.right() - c.right(), c.height()))
        pen = QPen(QColor(255, 200, 90, 240), 1.5)
        pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(c)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 200, 90, 235))
        h = self.HANDLE
        cx, cy = c.center().x(), c.center().y()
        for hx, hy in [
            (c.left(), c.top()), (cx, c.top()), (c.right(), c.top()), (c.right(), cy),
            (c.right(), c.bottom()), (cx, c.bottom()), (c.left(), c.bottom()), (c.left(), cy),
        ]:
            painter.drawRoundedRect(QRectF(hx - h / 2, hy - h / 2, h, h), 3, 3)

    # ----- painting ------------------------------------------------------- #
    def paint(self, painter: QPainter, option, widget=None) -> None:
        r = self._rect
        if self._pixmap is not None and not self._pixmap.isNull():
            painter.setRenderHint(QPainter.SmoothPixmapTransform, True)
            painter.setOpacity(self._opacity)
            painter.drawPixmap(r, self._pixmap, QRectF(self._pixmap.rect()))
            painter.setOpacity(1.0)
        painter.setPen(QPen(QColor(120, 180, 255, 235), 1.5))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(r)
        if self._crop_mode:
            self._paint_crop(painter)
            return
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 235))
        h = self.HANDLE
        for hx, hy in self._handle_points():
            painter.drawRoundedRect(QRectF(hx - h / 2, hy - h / 2, h, h), 3, 3)

    def _handle_points(self):
        r = self._rect
        cx, cy = r.center().x(), r.center().y()
        return [
            (r.left(), r.top()), (cx, r.top()), (r.right(), r.top()),
            (r.right(), cy), (r.right(), r.bottom()), (cx, r.bottom()),
            (r.left(), r.bottom()), (r.left(), cy),
        ]

    # ----- interaction ---------------------------------------------------- #
    def _region_at(self, pos: QPointF) -> str:
        r = self._rect
        return self._ctrl.hit_test(pos.x(), pos.y(), ARect(r.x(), r.y(), r.width(), r.height()))

    def _scene_bounds(self) -> ARect:
        sc = self.scene()
        if sc is not None and not sc.sceneRect().isNull():
            sr = sc.sceneRect()
            return ARect(sr.x(), sr.y(), sr.width(), sr.height())
        return ARect(-1.0e6, -1.0e6, 2.0e6, 2.0e6)

    def hoverMoveEvent(self, event) -> None:
        region = self._crop_region_at(event.pos()) if self._crop_mode else self._region_at(event.pos())
        self.setCursor(_CURSOR_MAP.get(cursor_for_region(region), Qt.ArrowCursor))
        super().hoverMoveEvent(event)

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            if self._crop_mode:
                region = self._crop_region_at(event.pos())
                if region:
                    self._crop_region = region
                    self._crop_start_scene = event.scenePos()
                    self._crop_start_rect = QRectF(self._crop_rect)
                    event.accept()
                    return
            else:
                region = self._region_at(event.pos())
                if region:
                    self._drag_region = region
                    self._start_scene = event.scenePos()
                    self._start_rect = QRectF(self._rect)
                    event.accept()
                    return
        super().mousePressEvent(event)

    def apply_drag(self, scene_pos: QPointF, keep_aspect: bool = False) -> None:
        """Resolve the current drag to a new rect (testable without a live view)."""
        if not self._drag_region:
            return
        sr = self._start_rect
        self._ctrl.begin(
            self._start_scene.x(), self._start_scene.y(), self._drag_region,
            ARect(sr.x(), sr.y(), sr.width(), sr.height()),
        )
        new = self._ctrl.update(
            scene_pos.x(), scene_pos.y(), self._scene_bounds(), keep_aspect=keep_aspect, snap=False
        )
        self.setRect(QRectF(new.x, new.y, new.w, new.h))

    def mouseMoveEvent(self, event) -> None:
        if self._crop_region:
            self.apply_crop_drag(event.scenePos())
            event.accept()
            return
        if self._drag_region:
            self.apply_drag(event.scenePos(), bool(event.modifiers() & Qt.ShiftModifier))
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event) -> None:
        if self._crop_region:
            self._crop_region = ""
            self._emit_crop()
            event.accept()
            return
        if self._drag_region:
            self._drag_region = ""
            self.areaDragFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


class BoardView(QGraphicsView):
    """A frameless, translucent, always-on-top view that hosts the editing scene
    over the target monitor. Click-through is toggled by :meth:`set_passthrough`."""

    def __init__(self, parent=None) -> None:
        scene = QGraphicsScene(parent)
        super().__init__(scene, parent)
        self._scene = scene
        self.setWindowFlags(Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.Tool)
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setStyleSheet("QGraphicsView{background:transparent;border:none;}")
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setRenderHints(QPainter.Antialiasing | QPainter.SmoothPixmapTransform)
        self.setFrameShape(QGraphicsView.NoFrame)
        self._snapshot = None

    def scene(self) -> QGraphicsScene:  # type: ignore[override]
        return self._scene

    def set_scene_to_screen(self, geometry) -> None:
        """DEPRECATED (test-only). Use :meth:`cover_monitor` for correct physical/logical
        coordinate spaces. This naive version sets scene == window geometry (logical px)."""
        self.setGeometry(geometry)
        self._scene.setSceneRect(QRectF(geometry))
        self.setSceneRect(QRectF(geometry))

    def cover_monitor(self, snapshot) -> None:
        """Cover the target monitor: window geometry in LOGICAL Qt px, scene rect in
        PHYSICAL desktop px. After this, item rects (scene coords) are physical pixels
        == the engine's ``draw_region`` directly, across DPI / multi-monitor.

        ``snapshot`` is a ``ui.helpers.viewport_mapper.MonitorSnapshot``.
        """
        self._snapshot = snapshot
        dx, dy, dw, dh = (float(v) for v in snapshot.desktop_rect)
        logical = desktop_rect_to_ui_rect(snapshot.desktop_rect, snapshot) or snapshot.desktop_rect
        self.setGeometry(qrect_from_tuple(logical))
        scene_rect = QRectF(dx, dy, dw, dh)
        self._scene.setSceneRect(scene_rect)
        self.setSceneRect(scene_rect)
        self._fit_scene()

    def _fit_scene(self) -> None:
        sr = self._scene.sceneRect()
        if sr.isNull() or self.viewport().width() <= 0 or self.viewport().height() <= 0:
            return
        # 1 scene unit (physical px) -> the logical window, independently on each axis.
        self.resetTransform()
        self.fitInView(sr, Qt.IgnoreAspectRatio)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._fit_scene()

    def set_passthrough(self, enabled: bool) -> None:
        enabled = bool(enabled)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, enabled)
        was_visible = self.isVisible()
        try:
            if was_visible:
                super().hide()
            self.setWindowFlag(Qt.WindowTransparentForInput, enabled)
            if was_visible:
                super().show()
        except Exception:
            log.debug("ignored exception toggling board passthrough", exc_info=True)
        finally:
            if not was_visible:
                super().hide()
