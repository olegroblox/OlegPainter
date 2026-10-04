"""CanvasView — viewport (QGraphicsView) поверх бесконечного 2D-холста.

Здесь сосредоточена ВСЯ маршрутизация мыши, поэтому критично важно держать
раздельными разные системы координат и разные жесты:

    ПКМ (RMB)           -> перемещение ОКНА (не сцены и не картинки)
    край/угол окна+ЛКМ  -> изменение размера ОКНА (обрезает viewport, не масштабирует)
    Alt+ЛКМ / СКМ       -> панорама ХОЛСТА (pan)
    колесо              -> зум ХОЛСТА относительно курсора
    ЛКМ по картинке     -> перемещение ИЗОБРАЖЕНИЯ в сцене
    ЛКМ по маркеру      -> масштабирование ИЗОБРАЖЕНИЯ (Shift — пропорции, Alt — от центра)
    ЛКМ по маркеру      -> поворот ИЗОБРАЖЕНИЯ (верхний маркер-«антенна»)

Перемещение/ресайз окна делегируются OverlayWindow, но инициируются здесь, т.к.
именно view получает события мыши, занимая всю площадь окна.
"""

from __future__ import annotations

import math

from PySide6.QtCore import Qt, QPointF, QRectF, QRect, QPoint, Signal
from PySide6.QtGui import (
    QPainter, QPen, QColor, QBrush, QPolygonF, QCursor, QPixmap, QRegion, QImage,
)
from PySide6.QtWidgets import QGraphicsView, QGraphicsScene

from .image_item import StencilImageItem, OPPOSITE, CORNERS, HANDLES_X, HANDLES_Y
from .geometry import vmul, vdot, sign, clamp

# Флаги краёв окна для ресайза
EDGE_LEFT, EDGE_RIGHT, EDGE_TOP, EDGE_BOTTOM = 1, 2, 4, 8

# Режимы взаимодействия
IDLE, PAN, WIN_MOVE, WIN_RESIZE, IMG_MOVE, IMG_RESIZE, IMG_ROTATE = range(7)

# Геометрические константы (в пикселях экрана)
SCENE_SPAN = 200_000          # условно бесконечный холст
WINDOW_RESIZE_MARGIN = 8      # ширина зоны ресайза окна по краям
HANDLE_SIZE = 9               # видимый размер маркера
HANDLE_HIT = 9                # радиус попадания по маркеру
ROTATE_OFFSET = 26            # вынос маркера поворота над верхней стороной
MIN_IMAGE_PX = 8              # минимальный размер изображения на экране
MIN_ZOOM, MAX_ZOOM = 0.05, 40.0

from ui.overlays.theme import ACCENT, DIM, draw_banner, draw_chip, draw_label
from ui.i18n import tr
from ui.overlays.coordinates import DesktopCoordinates


class CanvasView(QGraphicsView):
    # Emitted when the F1 rubber-band placement finishes: a QRect in GLOBAL LOGICAL px,
    # or None on cancel.
    areaRubberBandFinished = Signal(object)

    def __init__(self) -> None:
        super().__init__()
        self._placement_mode = False
        self._rb_start = None
        self._rb_rect = None
        self._scene = QGraphicsScene(self)
        self._scene.setSceneRect(
            -SCENE_SPAN / 2, -SCENE_SPAN / 2, SCENE_SPAN, SCENE_SPAN
        )
        self.setScene(self._scene)

        self._item = StencilImageItem()
        self._scene.addItem(self._item)
        self._item.setVisible(False)

        self._zoom = 1.0
        self._mode = IDLE
        self._selected = False

        # состояние текущего жеста
        self._gesture = {}

        # Кастомная «зона рисования» (кисть-трафарет): полупрозрачный ARGB-слой в
        # координатах вьюпорта; alpha>0 = «рисовать здесь». None до первого использования.
        self._mask_mode = False
        self._mask_painting = False
        self._mask_erase = False
        self._mask_brush = 48
        self._mask_img = None
        self._mask_has_paint = False
        self._mask_last = None

        self._configure_view()

    # ------------------------------------------------------------------ #
    #   Настройка viewport
    # ------------------------------------------------------------------ #

    def _configure_view(self) -> None:
        self.setFrameShape(QGraphicsView.NoFrame)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        # Якоря держим NoAnchor: масштаб «относительно курсора» считаем вручную
        # через прокрутку — это надёжно работает и при выключенных полосах прокрутки.
        self.setTransformationAnchor(QGraphicsView.NoAnchor)
        self.setResizeAnchor(QGraphicsView.NoAnchor)
        self.setRenderHints(
            QPainter.Antialiasing | QPainter.SmoothPixmapTransform
        )
        self.setMouseTracking(True)
        self.setFocusPolicy(Qt.StrongFocus)
        # прозрачный фон, чтобы под окном был виден рабочий стол / другое приложение
        self.setStyleSheet("background: transparent; border: 0px;")
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.viewport().setAttribute(Qt.WA_TranslucentBackground, True)
        self.viewport().setAutoFillBackground(False)
        self._scene.setBackgroundBrush(Qt.NoBrush)

    # ------------------------------------------------------------------ #
    #   Доступ к окну-владельцу (OverlayWindow)
    # ------------------------------------------------------------------ #

    @property
    def overlay(self):
        return self.window()

    # ------------------------------------------------------------------ #
    #   Изображение / состояние
    # ------------------------------------------------------------------ #

    def item(self) -> StencilImageItem:
        return self._item

    def load_image(self, path: str) -> bool:
        if not self._item.load(path):
            return False
        self._reset_item_after_load()
        return True

    def load_pixmap(self, pix, source_path: str = "") -> bool:
        """Load directly from a QPixmap (olegpainter source)."""
        if not self._item.set_pixmap(pix, source_path):
            return False
        self._reset_item_after_load()
        return True

    def _reset_item_after_load(self) -> None:
        pi = getattr(self, "_preview_item", None)
        if pi is not None:
            pi.setVisible(False)          # drop stale preview when a new image loads
        self._item.setVisible(True)
        self._item.scale_x = 1.0
        self._item.scale_y = 1.0
        self._item.rotation_deg = 0.0
        self._item.apply_transform()
        self._item.setPos(QPointF(0, 0))
        self._selected = True
        self.centerOn(self._item)
        self.viewport().update()

    def apply_image_state(self, st) -> None:
        if st.source_path and self._item.load(st.source_path):
            self._item.setVisible(True)
        self._item.scale_x = st.scale_x or 1.0
        self._item.scale_y = st.scale_y or 1.0
        self._item.rotation_deg = st.rotation
        self._item.setOpacity(clamp(st.opacity, 0.0, 1.0))
        self._item.apply_transform()
        self._item.setPos(QPointF(st.center_x, st.center_y))
        self.viewport().update()

    def capture_image_state(self, st) -> None:
        st.source_path = self._item.source_path
        st.center_x = self._item.center().x()
        st.center_y = self._item.center().y()
        st.scale_x = self._item.scale_x
        st.scale_y = self._item.scale_y
        st.rotation = self._item.rotation_deg
        st.opacity = self._item.opacity()

    def apply_view_state(self, st) -> None:
        zoom = clamp(st.zoom or 1.0, MIN_ZOOM, MAX_ZOOM)
        self.resetTransform()
        self.scale(zoom, zoom)
        self._zoom = zoom
        self.centerOn(QPointF(st.center_x, st.center_y))

    def capture_view_state(self, st) -> None:
        st.zoom = self._zoom
        center = self.mapToScene(self.viewport().rect().center())
        st.center_x = center.x()
        st.center_y = center.y()

    def set_image_opacity(self, value: float) -> None:
        self._item.setOpacity(clamp(value, 0.0, 1.0))
        self.viewport().update()
        self._request_save()

    def reset_transform(self) -> None:
        if not self._item.has_image():
            return
        self._item.set_geometry(QPointF(0, 0), 1.0, 1.0)
        self._item.rotation_deg = 0.0
        self._item.apply_transform()
        self._item.setPos(QPointF(0, 0))
        self._selected = True
        self.apply_view_state(_ZeroView())
        self.centerOn(self._item)
        self.viewport().update()
        self._request_save()

    def rotate_image(self, delta_deg: float) -> None:
        if not self._item.has_image():
            return
        self._item.rotation_deg = (self._item.rotation_deg + delta_deg) % 360.0
        self._item.apply_transform()
        self.viewport().update()
        self._request_save()

    def deselect(self) -> None:
        self._selected = False
        self.viewport().update()

    # ------------------------------------------------------------------ #
    #   Зум холста колесом — относительно курсора
    # ------------------------------------------------------------------ #

    def wheelEvent(self, event) -> None:
        angle = event.angleDelta().y()
        if angle == 0:
            return
        self.zoom_at(event.position().toPoint(), 1.0015 ** angle)
        self._request_save()
        event.accept()

    def zoom_at(self, p: QPoint, factor: float) -> None:
        """Масштабировать холст в точке viewport ``p`` (точка под курсором
        остаётся на месте). Реализовано через прокрутку, поэтому работает и
        при выключенных полосах прокрутки."""
        new_zoom = clamp(self._zoom * factor, MIN_ZOOM, MAX_ZOOM)
        factor = new_zoom / self._zoom
        if abs(factor - 1.0) < 1e-9:
            return
        old_scene = self.mapToScene(p)
        self.scale(factor, factor)
        self._zoom = new_zoom
        new_vp = self.mapFromScene(old_scene)
        h = self.horizontalScrollBar()
        v = self.verticalScrollBar()
        h.setValue(h.value() + (new_vp.x() - p.x()))
        v.setValue(v.value() + (new_vp.y() - p.y()))
        self.viewport().update()

    # ------------------------------------------------------------------ #
    #   Нажатие мыши — выбор жеста по приоритету
    # ------------------------------------------------------------------ #

    def mousePressEvent(self, event) -> None:
        vpos = event.position()
        gpos = event.globalPosition().toPoint()
        button = event.button()
        mods = event.modifiers()

        # 0. Режим размещения области (F1): ЛКМ тянет рамку, ПКМ/иное — отмена.
        if self._placement_mode:
            if button == Qt.LeftButton:
                self._rb_start = vpos.toPoint()
                self._rb_rect = QRect(self._rb_start, self._rb_start)
                self.viewport().update()
            else:
                self.cancel_placement()
            event.accept()
            return

        # 0.5 Режим «кисть-зона»: ЛКМ рисует/стирает зону. ПКМ/СКМ/Alt+ЛКМ
        # оставлены навигации (двигать/панорамировать окно во время рисования зоны).
        if self._mask_mode and button == Qt.LeftButton and not (mods & Qt.AltModifier):
            self._mask_painting = True
            self._mask_last = vpos
            self._paint_mask_segment(vpos, vpos)
            event.accept()
            return

        # 1. ПКМ — перемещение окна
        if button == Qt.RightButton:
            self._mode = WIN_MOVE
            self.overlay.begin_window_move(gpos)
            event.accept()
            return

        # 2. Средняя кнопка или Alt+ЛКМ — панорама холста
        if button == Qt.MiddleButton or (
            button == Qt.LeftButton and (mods & Qt.AltModifier)
        ):
            self._mode = PAN
            self._gesture = {"last": vpos}
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return

        if button != Qt.LeftButton:
            event.ignore()
            return

        # 3. ЛКМ по маркеру изображения (если выделено)
        if self._selected and self._item.has_image():
            handle = self._handle_at(vpos)
            if handle == "rotate":
                self._begin_image_rotate(vpos)
                event.accept()
                return
            if handle is not None:
                self._begin_image_resize(handle, vpos)
                event.accept()
                return

        # 4. ЛКМ у края окна — изменение размера окна
        edges = self._edges_at(vpos)
        if edges:
            self._mode = WIN_RESIZE
            self.overlay.begin_window_resize(edges, gpos)
            event.accept()
            return

        # 5. ЛКМ по телу изображения — выделить и двигать
        scene_pt = self.mapToScene(vpos.toPoint())
        if self._item.has_image() and self._item.contains_scene_point(scene_pt):
            self._selected = True
            self._mode = IMG_MOVE
            self._gesture = {
                "grab_offset": self._item.center() - scene_pt,
            }
            self.setCursor(Qt.ClosedHandCursor)
            self.viewport().update()
            event.accept()
            return

        # 6. ЛКМ по пустому месту — снять выделение
        self._selected = False
        self.viewport().update()
        event.accept()

    # ------------------------------------------------------------------ #
    #   Движение мыши
    # ------------------------------------------------------------------ #

    def mouseMoveEvent(self, event) -> None:
        vpos = event.position()
        gpos = event.globalPosition().toPoint()

        if self._placement_mode:
            if self._rb_start is not None:
                self._rb_rect = QRect(self._rb_start, vpos.toPoint()).normalized()
                self.viewport().update()
            event.accept()
            return

        if self._mask_mode and self._mask_painting:
            self._paint_mask_segment(self._mask_last or vpos, vpos)
            self._mask_last = vpos
            event.accept()
            return

        if self._mode == IDLE:
            self._update_hover_cursor(vpos)
            return

        if self._mode == WIN_MOVE:
            self.overlay.update_window_move(gpos)
        elif self._mode == WIN_RESIZE:
            self.overlay.update_window_resize(gpos)
        elif self._mode == PAN:
            last = self._gesture["last"]
            delta = vpos - last
            h = self.horizontalScrollBar()
            v = self.verticalScrollBar()
            h.setValue(h.value() - int(delta.x()))
            v.setValue(v.value() - int(delta.y()))
            self._gesture["last"] = vpos
        elif self._mode == IMG_MOVE:
            scene_pt = self.mapToScene(vpos.toPoint())
            self._item.set_center(scene_pt + self._gesture["grab_offset"])
            self.viewport().update()
        elif self._mode == IMG_RESIZE:
            self._update_image_resize(vpos, event.modifiers())
        elif self._mode == IMG_ROTATE:
            self._update_image_rotate(vpos, event.modifiers())

        event.accept()

    def mouseReleaseEvent(self, event) -> None:
        if self._placement_mode:
            if event.button() == Qt.LeftButton and self._rb_start is not None:
                rb = QRect(self._rb_start, event.position().toPoint()).normalized()
                self._rb_start = None
                if rb.width() >= 8 and rb.height() >= 8:
                    g_tl = self.viewport().mapToGlobal(rb.topLeft())
                    g_br = self.viewport().mapToGlobal(rb.bottomRight())
                    self._finish_placement(QRect(g_tl, g_br))
                else:
                    self._rb_rect = None
                    self.viewport().update()
            event.accept()
            return
        if self._mask_painting and event.button() == Qt.LeftButton:
            self._mask_painting = False
            self._mask_last = None
            self._request_save()        # debounced contentChanged → engine re-sync with the zone
            event.accept()
            return
        if self._mode != IDLE:
            if self._mode in (WIN_MOVE, WIN_RESIZE):
                self.overlay.end_window_gesture()
            self._mode = IDLE
            self._gesture = {}
            self._update_hover_cursor(event.position())
            self._request_save()
        event.accept()

    # ------------------------------------------------------------------ #
    #   Изменение размера изображения
    # ------------------------------------------------------------------ #

    def _begin_image_resize(self, handle: str, vpos: QPointF) -> None:
        self._mode = IMG_RESIZE
        pw, ph = self._item.pixmap_size()
        u, v = self._item.axes()
        positions = self._item.handle_positions()
        self._gesture = {
            "handle": handle,
            "u": u,
            "v": v,
            "pw": pw,
            "ph": ph,
            "anchor": positions[OPPOSITE[handle]],   # неподвижная точка
            "center0": self._item.center(),
            "hw0": self._item.half_extents()[0],
            "hh0": self._item.half_extents()[1],
        }

    def _update_image_resize(self, vpos: QPointF, mods) -> None:
        g = self._gesture
        handle = g["handle"]
        u, v = g["u"], g["v"]
        pw, ph = g["pw"], g["ph"]
        m = self.mapToScene(vpos.toPoint())

        shift = bool(mods & Qt.ShiftModifier)   # сохранять пропорции
        alt = bool(mods & Qt.AltModifier)       # от центра

        changes_x = handle in HANDLES_X
        changes_y = handle in HANDLES_Y
        is_corner = handle in CORNERS

        if alt:
            base = g["center0"]
        else:
            base = g["anchor"]

        rel = m - base
        du = vdot(rel, u)
        dv = vdot(rel, v)

        if alt:
            new_w = 2 * abs(du) if changes_x else 2 * g["hw0"]
            new_h = 2 * abs(dv) if changes_y else 2 * g["hh0"]
        else:
            new_w = abs(du) if changes_x else 2 * g["hw0"]
            new_h = abs(dv) if changes_y else 2 * g["hh0"]

        # Пропорции (Shift)
        if shift:
            if is_corner:
                s = max(new_w / pw, new_h / ph)
            elif changes_x:
                s = new_w / pw
            else:
                s = new_h / ph
            new_w, new_h = s * pw, s * ph

        # Минимальный размер на экране (с поправкой на текущий зум)
        min_scene = MIN_IMAGE_PX / max(self._zoom, 1e-6)
        new_w = max(new_w, min_scene)
        new_h = max(new_h, min_scene)

        sx = new_w / pw
        sy = new_h / ph

        # Новый центр
        if alt:
            center = g["center0"]
        elif is_corner:
            su = sign(du) if changes_x else 0.0
            sv = sign(dv) if changes_y else 0.0
            center = base + vmul(u, 0.5 * su * new_w) + vmul(v, 0.5 * sv * new_h)
        else:
            # для рёбер: вдоль изменяемой оси сдвигаемся от anchor, по другой — центр на anchor
            su = sign(du) if changes_x else 0.0
            sv = sign(dv) if changes_y else 0.0
            offset_u = 0.5 * su * new_w if changes_x else 0.0
            offset_v = 0.5 * sv * new_h if changes_y else 0.0
            center = base + vmul(u, offset_u) + vmul(v, offset_v)

        self._item.set_geometry(center, sx, sy)
        self.viewport().update()

    # ------------------------------------------------------------------ #
    #   Поворот изображения
    # ------------------------------------------------------------------ #

    def _begin_image_rotate(self, vpos: QPointF) -> None:
        self._mode = IMG_ROTATE
        center_vp = self.mapFromScene(self._item.center())
        start_angle = math.degrees(
            math.atan2(vpos.y() - center_vp.y(), vpos.x() - center_vp.x())
        )
        self._gesture = {
            "start_angle": start_angle,
            "rot0": self._item.rotation_deg,
        }

    def _update_image_rotate(self, vpos: QPointF, mods) -> None:
        g = self._gesture
        center_vp = self.mapFromScene(self._item.center())
        angle = math.degrees(
            math.atan2(vpos.y() - center_vp.y(), vpos.x() - center_vp.x())
        )
        rotation = g["rot0"] + (angle - g["start_angle"])
        if mods & Qt.ShiftModifier:
            rotation = round(rotation / 15.0) * 15.0   # привязка к 15°
        self._item.rotation_deg = rotation % 360.0
        self._item.apply_transform()
        self.viewport().update()

    # ------------------------------------------------------------------ #
    #   Определение целей под курсором
    # ------------------------------------------------------------------ #

    def _handle_at(self, vpos: QPointF) -> str | None:
        """Какой маркер изображения под курсором ('tl'..'l', 'rotate' или None)."""
        if not (self._selected and self._item.has_image()):
            return None
        positions = self._item.handle_positions()
        # маркер поворота — над верхней серединой (можно отключить: режим геометрии)
        if getattr(self, "_rotate_enabled", True):
            top_vp = self.mapFromScene(positions["t"])
            rot_vp = QPointF(top_vp.x(), top_vp.y() - ROTATE_OFFSET)
            if (vpos - rot_vp).manhattanLength() <= HANDLE_HIT + 2:
                if math.hypot(vpos.x() - rot_vp.x(), vpos.y() - rot_vp.y()) <= HANDLE_HIT + 2:
                    return "rotate"
        # обычные маркеры
        for key, sp in positions.items():
            hv = self.mapFromScene(sp)
            if math.hypot(vpos.x() - hv.x(), vpos.y() - hv.y()) <= HANDLE_HIT:
                return key
        return None

    def _edges_at(self, vpos: QPointF) -> int:
        """Битовая маска краёв окна для ресайза (0 — не у края)."""
        r = self.viewport().rect()
        x, y = vpos.x(), vpos.y()
        edges = 0
        if x <= WINDOW_RESIZE_MARGIN:
            edges |= EDGE_LEFT
        elif x >= r.width() - WINDOW_RESIZE_MARGIN:
            edges |= EDGE_RIGHT
        if y <= WINDOW_RESIZE_MARGIN:
            edges |= EDGE_TOP
        elif y >= r.height() - WINDOW_RESIZE_MARGIN:
            edges |= EDGE_BOTTOM
        return edges

    # ------------------------------------------------------------------ #
    #   Курсоры при наведении
    # ------------------------------------------------------------------ #

    def _update_hover_cursor(self, vpos: QPointF) -> None:
        handle = self._handle_at(vpos)
        if handle != getattr(self, "_hover_handle", None):
            self._hover_handle = handle
            self.viewport().update()
        if handle == "rotate":
            self.setCursor(Qt.OpenHandCursor)
            return
        if handle is not None:
            self.setCursor(self._cursor_for_handle(handle))
            return
        edges = self._edges_at(vpos)
        if edges:
            self.setCursor(self._cursor_for_edges(edges))
            return
        scene_pt = self.mapToScene(vpos.toPoint())
        if self._item.has_image() and self._item.contains_scene_point(scene_pt):
            self.setCursor(Qt.SizeAllCursor)
            return
        self.setCursor(Qt.ArrowCursor)

    @staticmethod
    def _cursor_for_handle(handle: str):
        if handle in ("tl", "br"):
            return Qt.SizeFDiagCursor
        if handle in ("tr", "bl"):
            return Qt.SizeBDiagCursor
        if handle in ("t", "b"):
            return Qt.SizeVerCursor
        return Qt.SizeHorCursor

    @staticmethod
    def _cursor_for_edges(edges: int):
        left = edges & EDGE_LEFT
        right = edges & EDGE_RIGHT
        top = edges & EDGE_TOP
        bottom = edges & EDGE_BOTTOM
        if (left and top) or (right and bottom):
            return Qt.SizeFDiagCursor
        if (right and top) or (left and bottom):
            return Qt.SizeBDiagCursor
        if left or right:
            return Qt.SizeHorCursor
        return Qt.SizeVerCursor

    # ------------------------------------------------------------------ #
    #   Отрисовка рамки выделения и маркеров (постоянный размер на экране)
    # ------------------------------------------------------------------ #

    def drawForeground(self, painter: QPainter, rect: QRectF) -> None:
        if self._placement_mode:
            self._draw_placement(painter)
            return
        # Полупрозрачная «зона рисования» поверх всего (видна и без выделения).
        if self._mask_img is not None and (self._mask_mode or self._mask_has_paint):
            painter.save()
            painter.resetTransform()
            painter.drawImage(0, 0, self._mask_img)
            painter.restore()
        editing = getattr(self, "_edit_hint", False)
        painter.save()
        painter.resetTransform()  # рисуем в координатах viewport (постоянный размер)
        painter.setRenderHint(QPainter.Antialiasing, True)
        self._draw_window_frame(painter, editing)
        if self._selected and self._item.has_image():
            self._draw_selection(painter)
        painter.restore()
        if editing:
            self._draw_edit_hint(painter)

    def _draw_window_frame(self, painter: QPainter, editing: bool) -> None:
        """Окно трафарета = область рисования. В правке — жёлтая рамка, как у
        выделенного элемента основного окна; в пассиве с «Рамкой» — тонкая
        полупрозрачная, чтобы не заслонять рисунок."""
        state = getattr(self.overlay, "_state", None)
        show_border = bool(state is not None and state.window.show_border)
        if not (editing or show_border):
            return
        frame = QRectF(self.viewport().rect()).adjusted(1, 1, -1, -1)
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(ACCENT, 2) if editing else QPen(QColor(255, 210, 30, 110), 1))
        painter.drawRoundedRect(frame, 6, 6)

    def _draw_selection(self, painter: QPainter) -> None:
        positions = self._item.handle_positions()
        corners = QPolygonF([QPointF(self.mapFromScene(p)) for p in self._item.corners()])

        # рамка: тёмный ореол + акцент — читается и на светлой, и на жёлтой картинке
        painter.setBrush(Qt.NoBrush)
        painter.setPen(QPen(QColor(0, 0, 0, 90), 3.2))
        painter.drawPolygon(corners)
        painter.setPen(QPen(ACCENT, 1.4))
        painter.drawPolygon(corners)

        # «антенна» поворота (скрыта в режиме геометрии)
        if getattr(self, "_rotate_enabled", True):
            top_vp = self.mapFromScene(positions["t"])
            rot_vp = QPointF(top_vp.x(), top_vp.y() - ROTATE_OFFSET)
            painter.setPen(QPen(ACCENT, 1.4))
            painter.drawLine(QPointF(top_vp), rot_vp)
            painter.setBrush(QBrush(QColor(255, 255, 255)))
            painter.drawEllipse(rot_vp, HANDLE_SIZE / 2.0, HANDLE_SIZE / 2.0)

        hover = getattr(self, "_hover_handle", None)
        for key, sp in positions.items():
            self._draw_handle(painter, QPointF(self.mapFromScene(sp)), key, key == hover)

        # живой размер видимой части в физических пикселях (что уйдёт в рисование)
        vp = self.viewport().rect()
        bounds = corners.boundingRect().intersected(QRectF(vp))
        if bounds.width() >= 1 and bounds.height() >= 1:
            ratio = self.devicePixelRatioF() or 1.0
            text = f"{round(bounds.width() * ratio)} × {round(bounds.height() * ratio)} px"
            # под рамкой; нет места — над ней; иначе внутри у верхнего края
            # (низ окна занят подсказкой правки)
            top = bounds.bottom() + 12
            if top + 26 > vp.bottom() - 48:
                top = bounds.top() - 34
                if top < vp.top() + 4:
                    top = bounds.top() + 14
            draw_chip(painter, bounds.center().x(), top, text, vp)

    @staticmethod
    def _draw_handle(painter: QPainter, center: QPointF, key: str, hot: bool) -> None:
        """Угловые маркеры — скруглённые квадраты, боковые — «таблетки» вдоль стороны
        (как в Figma): сразу видно, что угол масштабирует, а сторона тянет."""
        if key in ("t", "b"):
            w, h = HANDLE_SIZE * 2.0, HANDLE_SIZE * 0.72
        elif key in ("l", "r"):
            w, h = HANDLE_SIZE * 0.72, HANDLE_SIZE * 2.0
        else:
            w = h = HANDLE_SIZE + 2.0
        if hot:
            w, h = w + 3, h + 3
        rect = QRectF(center.x() - w / 2, center.y() - h / 2, w, h)
        radius = min(w, h) / 2 if key in ("t", "b", "l", "r") else 3.0
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(0, 0, 0, 70))
        painter.drawRoundedRect(rect.translated(0, 1.2).adjusted(-1, -1, 1, 1), radius + 1, radius + 1)
        painter.setBrush(ACCENT if hot else QColor(255, 255, 255))
        painter.setPen(QPen(QColor(0, 0, 0, 60) if hot else ACCENT, 1.4))
        painter.drawRoundedRect(rect, radius, radius)

    # ------------------------------------------------------------------ #
    #   Сдвиг содержимого при ресайзе окна (фиксация неподвижного угла)
    # ------------------------------------------------------------------ #

    def shift_content(self, d_left: int, d_top: int) -> None:
        """При сдвиге верхнего/левого края окна компенсируем прокрутку так,
        чтобы содержимое оставалось «приклеенным» к неподвижному углу."""
        if d_left == 0 and d_top == 0:
            return
        h = self.horizontalScrollBar()
        v = self.verticalScrollBar()
        h.setValue(h.value() + d_left)
        v.setValue(v.value() + d_top)

    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    #   Геометрия трафарета (вариант A): crop_norm + экранный прямоугольник
    # ------------------------------------------------------------------ #

    def stencil_geometry(self):
        """Для геометрического трафарета. Возвращает ``(crop_lrtb, global_logical_rect)``
        или ``None``.

        ``crop_lrtb`` = (left, top, right, bottom), нормированная (0..1) часть ИСХОДНИКА,
        видимая через окно; ``global_logical_rect`` = (x, y, w, h) на экране (ЛОГИЧЕСКИЕ px),
        куда эта часть проецируется. Поворот считается нулевым (в этом режиме он отключён),
        поэтому всё осенаправленное и пересечение точное."""
        item = self._item
        if not item.has_image():
            return None
        pw, ph = item.pixmap_size()
        if pw <= 0 or ph <= 0:
            return None
        sx = abs(item.scale_x) or 1.0
        sy = abs(item.scale_y) or 1.0
        c = item.center()
        img = QRectF(c.x() - pw * sx / 2.0, c.y() - ph * sy / 2.0, pw * sx, ph * sy)
        visible_scene = self.mapToScene(self.viewport().rect()).boundingRect()
        vis = img.intersected(visible_scene)
        if vis.width() <= 1.0 or vis.height() <= 1.0:
            return None
        iw, ih = img.width(), img.height()
        crop = (
            clamp((vis.left() - img.left()) / iw, 0.0, 1.0),
            clamp((vis.top() - img.top()) / ih, 0.0, 1.0),
            clamp((vis.right() - img.left()) / iw, 0.0, 1.0),
            clamp((vis.bottom() - img.top()) / ih, 0.0, 1.0),
        )
        # видимый прямоугольник: сцена -> viewport -> глобальные (логические) px
        tl = self.mapFromScene(vis.topLeft())
        br = self.mapFromScene(vis.bottomRight())
        g_tl = self.viewport().mapToGlobal(tl)
        g_br = self.viewport().mapToGlobal(br)
        gx, gy = min(g_tl.x(), g_br.x()), min(g_tl.y(), g_br.y())
        gw, gh = abs(g_br.x() - g_tl.x()), abs(g_br.y() - g_tl.y())
        if gw < 1 or gh < 1:
            return None
        return crop, (gx, gy, gw, gh)

    # ------------------------------------------------------------------ #
    #   Превью-наложение (пассивный трафарет = как нарисуется)
    # ------------------------------------------------------------------ #

    # ------------------------------------------------------------------ #
    #   Кисть-зона: произвольная маска области рисования
    # ------------------------------------------------------------------ #
    MASK_FILL = QColor(64, 220, 140, 150)   # полупрозрачный зелёный = «рисовать здесь»

    def set_mask_mode(self, on: bool) -> None:
        self._mask_mode = bool(on)
        if on:
            self._ensure_mask_img()
            self.deselect()                       # прячем маркеры картинки на время рисования
            self.setCursor(Qt.CrossCursor)
        else:
            self._mask_painting = False
            self._update_hover_cursor(QPointF(self.viewport().rect().center()))
        self.viewport().update()

    def set_mask_brush(self, px: int) -> None:
        self._mask_brush = max(2, int(px))

    def set_mask_erase(self, on: bool) -> None:
        self._mask_erase = bool(on)

    def has_mask(self) -> bool:
        return bool(self._mask_has_paint)

    def clear_mask(self) -> None:
        if self._mask_img is not None:
            self._mask_img.fill(Qt.transparent)
        self._mask_has_paint = False
        self.viewport().update()

    def _ensure_mask_img(self) -> QImage:
        vp = self.viewport().rect()
        w = max(1, vp.width()); h = max(1, vp.height())
        img = self._mask_img
        if img is None or img.width() != w or img.height() != h:
            new = QImage(w, h, QImage.Format_ARGB32_Premultiplied)
            new.fill(Qt.transparent)
            if img is not None and img.width() > 0 and img.height() > 0:
                # при ресайзе окна сохраняем нарисованную форму (вписываем в новый размер)
                p = QPainter(new)
                p.setRenderHint(QPainter.SmoothPixmapTransform, True)
                p.drawImage(QRectF(0, 0, w, h), img, QRectF(img.rect()))
                p.end()
            self._mask_img = new
        return self._mask_img

    def _paint_mask_segment(self, a: QPointF, b: QPointF) -> None:
        img = self._ensure_mask_img()
        p = QPainter(img)
        p.setRenderHint(QPainter.Antialiasing, True)
        if self._mask_erase:
            p.setCompositionMode(QPainter.CompositionMode_Clear)
            col = QColor(0, 0, 0, 0)
        else:
            p.setCompositionMode(QPainter.CompositionMode_Source)
            col = self.MASK_FILL
        d = max(2, int(self._mask_brush))
        p.setPen(QPen(col, d, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin))
        p.setBrush(QBrush(col))
        if a == b:
            p.drawEllipse(a, d / 2.0, d / 2.0)
        else:
            p.drawLine(a, b)
            p.drawEllipse(b, d / 2.0, d / 2.0)
        p.end()
        if not self._mask_erase:
            self._mask_has_paint = True
        self.viewport().update()

    def _visible_image_vp_rect(self):
        """Видимый прямоугольник картинки (= draw_region на экране) в координатах
        ВЬЮПОРТА, или None. Повторяет вычисление vis из stencil_geometry()."""
        item = self._item
        if not item.has_image():
            return None
        pw, ph = item.pixmap_size()
        if pw <= 0 or ph <= 0:
            return None
        sx = abs(item.scale_x) or 1.0
        sy = abs(item.scale_y) or 1.0
        c = item.center()
        img = QRectF(c.x() - pw * sx / 2.0, c.y() - ph * sy / 2.0, pw * sx, ph * sy)
        visible_scene = self.mapToScene(self.viewport().rect()).boundingRect()
        vis = img.intersected(visible_scene)
        if vis.width() <= 1.0 or vis.height() <= 1.0:
            return None
        tl = self.mapFromScene(vis.topLeft())
        br = self.mapFromScene(vis.bottomRight())
        return QRectF(QPointF(tl), QPointF(br)).normalized()

    @staticmethod
    def _qimage_alpha_to_np(qimg):
        """Owned alpha array, with explicit byte order and scanline stride."""
        if qimg is None or qimg.isNull():
            return None
        import numpy as np
        rgba = qimg.convertToFormat(QImage.Format_RGBA8888)
        pixels = np.ndarray((rgba.height(), rgba.width(), 4), dtype=np.uint8,
                            buffer=rgba.constBits(), strides=(rgba.bytesPerLine(), 4, 1))
        return pixels[:, :, 3].copy()

    def _mask_crop_rect(self):
        if self._mask_img is None:
            return None
        # Окно могло измениться в размере после последнего рисования: _mask_img хранится
        # в пикселях ВЬЮПОРТА, поэтому пере-сэмплируем его под текущий вьюпорт ДО кропа,
        # иначе маска рассинхронизирована с видимым прямоугольником (стар. геометрия).
        self._ensure_mask_img()
        rect = self._visible_image_vp_rect()
        if rect is None:
            rect = QRectF(self.viewport().rect())
        crop = rect.toRect().intersected(self._mask_img.rect())
        if crop.width() < 2 or crop.height() < 2:
            return None
        return crop

    def mask_region_array(self):
        """Нарисованная зона как boolean numpy (H, W) в ориентации draw_region
        (обрезана до видимого прямоугольника картинки), или None если пусто."""
        if self._mask_img is None or not self._mask_has_paint:
            return None
        crop = self._mask_crop_rect()
        if crop is None:
            return None
        arr = self._qimage_alpha_to_np(self._mask_img.copy(crop))
        if arr is None:
            return None
        mask = arr > 0
        try:
            if not bool(mask.any()):
                return None
        except Exception:
            return None
        return mask

    def export_mask_qimage(self):
        """Нарисованная зона, обрезанная до draw_region, как QImage для сохранения;
        None если ничего не нарисовано."""
        if self._mask_img is None or not self._mask_has_paint:
            return None
        crop = self._mask_crop_rect()
        if crop is None:
            return None
        return self._mask_img.copy(crop)

    def import_mask_qimage(self, qimg) -> bool:
        """Наложить сохранённую зону (PNG) на текущий видимый прямоугольник."""
        if qimg is None or qimg.isNull():
            return False
        img = self._ensure_mask_img()
        img.fill(Qt.transparent)
        rect = self._visible_image_vp_rect() or QRectF(self.viewport().rect())
        src = qimg.convertToFormat(QImage.Format_ARGB32_Premultiplied)
        p = QPainter(img)
        p.setRenderHint(QPainter.SmoothPixmapTransform, True)
        p.drawImage(rect, src, QRectF(src.rect()))
        p.end()
        self._mask_has_paint = True
        self.viewport().update()
        return True

    def _ensure_preview_item(self):
        pi = getattr(self, "_preview_item", None)
        if pi is None:
            from PySide6.QtWidgets import QGraphicsPixmapItem
            pi = QGraphicsPixmapItem()
            pi.setZValue(1)                                  # над исходником (z=0)
            pi.setTransformationMode(Qt.SmoothTransformation)
            pi.setVisible(False)
            self._scene.addItem(pi)
            self._preview_item = pi
        return pi

    def set_preview(self, qimg, crop_lrtb, opacity: float = 0.45) -> None:
        """Пассивный трафарет: наложить КВАНТОВАННОЕ превью (что реально нарисуется) на
        обрезанную область исходника и скрыть сырой исходник — пользователь видит результат."""
        item = self._item
        if qimg is None or qimg.isNull() or not item.has_image():
            self.hide_preview()
            return
        from PySide6.QtGui import QTransform
        pw, ph = item.pixmap_size()
        sx = abs(item.scale_x) or 1.0
        sy = abs(item.scale_y) or 1.0
        c = item.center()
        ix, iy = c.x() - pw * sx / 2.0, c.y() - ph * sy / 2.0
        iw, ih = pw * sx, ph * sy
        l, t, r, b = crop_lrtb
        cs_x, cs_y = ix + l * iw, iy + t * ih
        cs_w, cs_h = max(1e-6, (r - l) * iw), max(1e-6, (b - t) * ih)
        pix = QPixmap.fromImage(qimg)
        if pix.width() <= 0 or pix.height() <= 0:
            self.hide_preview()
            return
        pi = self._ensure_preview_item()
        pi.setPixmap(pix)
        tr = QTransform()
        tr.translate(cs_x, cs_y)
        tr.scale(cs_w / pix.width(), cs_h / pix.height())
        pi.setTransform(tr)
        pi.setOpacity(clamp(opacity, 0.0, 1.0))
        pi.setVisible(True)
        item.setVisible(False)
        self.viewport().update()

    def hide_preview(self) -> None:
        pi = getattr(self, "_preview_item", None)
        if pi is not None:
            pi.setVisible(False)
        if self._item.has_image():
            self._item.setVisible(True)
        self.viewport().update()

    # ------------------------------------------------------------------ #
    #   Зоны ПОВЕРХ исходника (полупрозрачные семантические планы)
    # ------------------------------------------------------------------ #

    def set_zone_overlay(self, qimg, crop_lrtb) -> None:
        """Полупрозрачный слой ПОВЕРХ исходника, НЕ скрывая его (в отличие от
        set_preview): дочерний элемент картинки, живёт в её локальных координатах
        и сам следует за перемещением/масштабом. Используется превью семантических
        планов (Alt+F2 → «Планы»): синий фон / оранжевые объекты / маджента-детали."""
        item = self._item
        if qimg is None or qimg.isNull() or not item.has_image():
            self.hide_zone_overlay()
            return
        from PySide6.QtGui import QTransform
        from PySide6.QtWidgets import QGraphicsPixmapItem
        pix = QPixmap.fromImage(qimg)
        if pix.width() <= 0 or pix.height() <= 0:
            self.hide_zone_overlay()
            return
        zi = getattr(self, "_zone_item", None)
        if zi is None:
            zi = QGraphicsPixmapItem(item)               # ребёнок: наследует трансформацию
            zi.setZValue(2)                              # над исходником и превью
            zi.setTransformationMode(Qt.SmoothTransformation)
            self._zone_item = zi
        pw, ph = item.pixmap_size()
        l, t, r, b = crop_lrtb
        tr = QTransform()
        tr.translate(-pw / 2.0 + l * pw, -ph / 2.0 + t * ph)
        tr.scale(max(1e-6, (r - l)) * pw / pix.width(),
                 max(1e-6, (b - t)) * ph / pix.height())
        zi.setPixmap(pix)
        zi.setTransform(tr)
        zi.setVisible(True)
        self.viewport().update()

    def hide_zone_overlay(self) -> None:
        zi = getattr(self, "_zone_item", None)
        if zi is not None:
            zi.setVisible(False)
        self.viewport().update()

    # ------------------------------------------------------------------ #
    #   Режим размещения области (F1): рамка поверх затемнения
    # ------------------------------------------------------------------ #

    def set_placement_mode(self, on: bool) -> None:
        self._placement_mode = bool(on)
        if on:
            self._placement_coordinates = DesktopCoordinates()
        self._rb_start = None
        self._rb_rect = None
        if on:
            self._item.setVisible(False)        # видим рабочий стол, чтобы обвести цель
            pi = getattr(self, "_preview_item", None)
            if pi is not None:
                pi.setVisible(False)
            self.setCursor(Qt.CrossCursor)
        else:
            self.setCursor(Qt.ArrowCursor)
            if self._item.has_image():
                self._item.setVisible(True)
        self.viewport().update()

    def cancel_placement(self) -> None:
        if self._placement_mode:
            self._finish_placement(None)

    def _finish_placement(self, global_rect) -> None:
        self._placement_mode = False
        self._rb_start = None
        self._rb_rect = None
        self.setCursor(Qt.ArrowCursor)
        if self._item.has_image():
            self._item.setVisible(True)
        self.viewport().update()
        self.areaRubberBandFinished.emit(global_rect)

    def _draw_placement(self, painter: QPainter) -> None:
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QPainter.Antialiasing, True)
        vp = self.viewport().rect()
        rb = self._rb_rect
        if rb is not None and rb.width() > 0 and rb.height() > 0:
            outside = QRegion(vp).subtracted(QRegion(rb))
            painter.setClipRegion(outside)
            painter.fillRect(vp, DIM)
            painter.setClipping(False)
            painter.setBrush(Qt.NoBrush)
            painter.setPen(QPen(QColor(0, 0, 0, 90), 4))
            painter.drawRect(rb)
            painter.setPen(QPen(ACCENT, 2))
            painter.drawRect(rb)
            top = self.viewport().mapToGlobal(rb.topLeft())
            end = self.viewport().mapToGlobal(rb.topLeft() + QPoint(rb.width(), rb.height()))
            x, y = self._placement_coordinates.to_physical(top.x(), top.y())
            ex, ey = self._placement_coordinates.to_physical(end.x(), end.y())
            draw_label(painter, vp, rb.bottomLeft() + QPoint(0, 14),
                       f"X {x}   Y {y}  ·  {abs(ex-x)} × {abs(ey-y)} px")
        else:
            painter.fillRect(vp, DIM)
        draw_banner(painter, vp, tr("stencil_place_title"), tr("stencil_place_hint"))
        painter.restore()

    def _draw_edit_hint(self, painter: QPainter) -> None:
        painter.save()
        painter.resetTransform()
        painter.setRenderHint(QPainter.Antialiasing, True)
        vp = self.viewport().rect()
        draw_label(painter, vp, QPoint(12, vp.bottom() - 42),
                   tr("stencil_edit_hint"))
        painter.restore()

    def fit_image_to_viewport(self, margin: float = 1.0) -> None:
        """Center the image and scale it to CONTAIN within the current window (no crop).
        Used after F1 placement so the freshly placed area shows the whole image."""
        item = self._item
        if not item.has_image():
            return
        pw, ph = item.pixmap_size()
        vp = self.viewport().rect()
        if pw <= 0 or ph <= 0 or vp.width() <= 0 or vp.height() <= 0:
            return
        self._zoom = 1.0
        self.resetTransform()
        fit = min(vp.width() / pw, vp.height() / ph) * clamp(margin, 0.1, 1.0)
        item.setVisible(True)
        item.set_geometry(QPointF(0.0, 0.0), fit, fit)
        self._center_on_exact(item.center())
        self.viewport().update()

    def _center_on_exact(self, point: QPointF) -> None:
        # centerOn uses integer scroll bars. On odd-sized viewports it can shift
        # the image half a pixel, clipping a row/column from the saved crop.
        self.centerOn(point)
        mapped = self.viewportTransform().map(point)
        self.translate(
            (self.viewport().width() / 2.0 - mapped.x()) / self._zoom,
            (self.viewport().height() / 2.0 - mapped.y()) / self._zoom,
        )

    def apply_crop(self, crop_lrtb, margin: float = 1.0) -> bool:
        """Inverse of ``stencil_geometry``'s crop: zoom/pan the view so the window shows
        exactly ``crop_lrtb`` (left, top, right, bottom; normalized 0..1) of the source.

        Used on restart to restore the saved ``engine.crop_norm`` into the stencil instead of
        a blind ``fit_image_to_viewport`` (which would reset the crop to the whole image and
        then get written back to the engine by the first ``capture_from_kalka``)."""
        item = self._item
        if not item.has_image():
            return False
        try:
            l, t, r, b = (float(v) for v in crop_lrtb)
        except Exception:
            return False
        l = clamp(l, 0.0, 1.0); t = clamp(t, 0.0, 1.0)
        r = clamp(r, 0.0, 1.0); b = clamp(b, 0.0, 1.0)
        fw, fh = (r - l), (b - t)
        # Degenerate or whole-image crop → plain fit (no zoom).
        if fw <= 1e-3 or fh <= 1e-3 or (l <= 1e-3 and t <= 1e-3 and r >= 1.0 - 1e-3 and b >= 1.0 - 1e-3):
            self.fit_image_to_viewport(margin)
            return True
        pw, ph = item.pixmap_size()
        vp = self.viewport().rect()
        if pw <= 0 or ph <= 0 or vp.width() <= 0 or vp.height() <= 0:
            return False
        # Base item scale = the same "contain" fit F1 placement uses (source fills the area at
        # view-zoom 1); the crop is then realized purely by zooming/panning the view.
        s = min(vp.width() / pw, vp.height() / ph) * clamp(margin, 0.1, 1.0)
        if s <= 0:
            return False
        item.setVisible(True)
        item.set_geometry(QPointF(0.0, 0.0), s, s)
        iw, ih = pw * s, ph * s
        img_left, img_top = -iw / 2.0, -ih / 2.0   # image rect centered on scene origin
        # View zoom that makes the crop region fill the viewport; min() so the whole crop
        # stays visible (aspect matches on restore → both dims agree).
        zoom = min(vp.width() / max(fw * iw, 1e-6), vp.height() / max(fh * ih, 1e-6))
        zoom = clamp(zoom, MIN_ZOOM, MAX_ZOOM)
        cx = img_left + (l + r) / 2.0 * iw
        cy = img_top + (t + b) / 2.0 * ih
        self._zoom = zoom
        self.resetTransform()
        self.scale(zoom, zoom)
        self._center_on_exact(QPointF(cx, cy))
        self.viewport().update()
        return True

    def _request_save(self) -> None:
        overlay = self.overlay
        if hasattr(overlay, "request_save"):
            overlay.request_save()


class _ZeroView:
    """Заглушка состояния камеры для сброса (zoom=1, центр в (0,0))."""
    zoom = 1.0
    center_x = 0.0
    center_y = 0.0
