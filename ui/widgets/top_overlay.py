from __future__ import annotations

import os
from typing import Optional, Dict, Any

from PySide6.QtCore import Qt, QRect, QRectF, QPoint, QPointF, QTimer, Signal, QSignalBlocker, QEvent
from PySide6.QtWidgets import (
    QWidget,
    QLabel,
    QPushButton,
    QToolButton,
    QFrame,
    QHBoxLayout,
    QButtonGroup,
    QSizePolicy,
    QSpacerItem,
    QGraphicsOpacityEffect,
    QMenu,
)
from PySide6.QtGui import QPixmap, QImage, QGuiApplication, QScreen, QPainter, QColor, QPen, QPainterPath

from ui.helpers.viewport_mapper import (
    MonitorSnapshot,
    desktop_rect_to_ui_rect,
    normalize_monitor_name,
    qrect_from_tuple,
    select_monitor_snapshot,
    ui_rect_to_desktop_rect,
)
from ui.widgets.manual_fit_canvas import ManualFitCanvas
from ui.widgets.overlay_base import ClickThroughOverlay
from ui.helpers.area_interaction import (
    AreaInteractionController,
    Rect as AreaRect,
    cursor_for_region,
    CURSOR_MOVE,
    CURSOR_SIZE_VER,
    CURSOR_SIZE_HOR,
    CURSOR_SIZE_FDIAG,
    CURSOR_SIZE_BDIAG,
)
from ui.i18n import tr
import logging

log = logging.getLogger("olegpainter.top_overlay")

if os.name == "nt":
    import ctypes
    from ctypes import wintypes

    _MONITOR_DEFAULTTONEAREST = 2
    _MDT_EFFECTIVE_DPI = 0

    class _POINT(ctypes.Structure):
        _fields_ = [("x", wintypes.LONG), ("y", wintypes.LONG)]

    class _RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class _MONITORINFOEX(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", _RECT),
            ("rcWork", _RECT),
            ("dwFlags", wintypes.DWORD),
            ("szDevice", wintypes.WCHAR * 32),
        ]

    try:
        _USER32 = ctypes.windll.user32
    except Exception:
        _USER32 = None  # type: ignore[assignment]
    try:
        _SHCORE = ctypes.windll.shcore
    except Exception:
        _SHCORE = None  # type: ignore[assignment]
else:
    _USER32 = None
    _SHCORE = None


_SCREEN_SIGNAL_NAMES = ("geometryChanged", "physicalDotsPerInchChanged", "logicalDotsPerInchChanged")


class _OverlayHandleLayer(QWidget):
    def __init__(self, owner: "TopOverlay"):
        super().__init__(owner)
        self._owner = owner
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setAttribute(Qt.WA_NoSystemBackground, True)
        self.setStyleSheet("background: transparent;")

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing, True)
        owner = self._owner
        if owner is not None:
            owner._paint_area_border(painter)


class TopOverlay(ClickThroughOverlay):
    editApplied = Signal(dict)
    editCancelled = Signal()
    cropApplied = Signal(float, float, float, float)  # view-normalized (l,t,r,b) crop
    cropResetRequested = Signal()
    areaLiveChanged = Signal(int, int, int, int)  # live draw-region (x,y,w,h) during drag

    _AREA_MIN_SIZE = 1.0
    _AREA_HANDLE_SIZE = 14.0
    _EDITOR_CANVAS_PADDING_MIN = 120
    _EDITOR_CANVAS_PADDING_MAX = 360
    def __init__(self, parent=None):
        super().__init__(parent, window_kind=Qt.Window)
        self._view_opacity = 0.55
        self._edit_opacity = 0.9
        self._area_ctrl = AreaInteractionController(
            handle_size=self._AREA_HANDLE_SIZE, min_size=self._AREA_MIN_SIZE, snap=8.0
        )

        self._img: QImage | None = None
        self._physical_area: Optional[QRect] = None
        self._logical_area: Optional[QRect] = None
        self._current_dpr: float = 1.0
        self._target_screen: Optional[QScreen] = None
        self._watched_screens: dict[int, QScreen] = {}
        self._global_watchers_bound = False

        self._label = QLabel(self)
        self._label.setStyleSheet("background: transparent;")
        self._label.setScaledContents(True)  # TODO: заменить на кастомный viewport в духе PureRef canvas
        self._label.move(0, 0)
        self._label_effect = QGraphicsOpacityEffect(self)
        self._label_effect.setOpacity(self._view_opacity)
        self._label.setGraphicsEffect(self._label_effect)
        self._handle_layer = _OverlayHandleLayer(self)
        self._handle_layer.setVisible(False)

        self._area_size = (0, 0)
        self._area_rect_display = QRectF()
        self._edit_mode = False
        self._editor_canvas = None
        self._editor_toolbar = None
        self._mode_group = None
        self._btn_mode_fill = None
        self._btn_mode_manual = None
        self._btn_fit = None
        self._btn_apply = None
        self._btn_cancel = None
        self._edit_state = {
            "stretch": True,
            "rect": None,
            "area": (0.0, 0.0),
            "image_size": None,
            "area_physical_rect": None,
            "_rect_is_physical": False,
        }
        self._edit_source_pixmap = None
        self._area_drag_mode = ""
        self._area_lock_aspect = False
        self._area_drag_button = Qt.MouseButton.NoButton
        self._area_drag_start = QPointF()
        self._area_drag_rect_start = QRectF()
        self._crop_mode = False
        self._crop_rect_display = QRectF()
        self._crop_ctrl = AreaInteractionController(
            handle_size=self._AREA_HANDLE_SIZE, min_size=8.0, snap=0.0
        )
        self._crop_drag_mode = ""
        self._crop_drag_start = QPointF()
        self._crop_drag_rect_start = QRectF()
        self._space_pan_active = False
        self._space_pan_cursor = False

        self._last_monitor_info: Optional[Dict[str, Any]] = None
        self._last_monitor_snapshot: Optional[MonitorSnapshot] = None
        self._last_screen_geometry: Optional[QRect] = None
        self._force_fullscreen_on_show = False

        self._window_handle = None
        self._apply_timer = QTimer(self)
        self._apply_timer.setSingleShot(True)
        self._apply_timer.timeout.connect(self._apply_safe)
        self._binding_timer = QTimer(self)
        self._binding_timer.setSingleShot(True)
        self._binding_timer.timeout.connect(self._ensure_window_handle_binding)
        self._install_screen_watchers()
        self._binding_timer.start(0)

    # click-through toggle (_apply_passthrough / set_passthrough) is inherited
    # from ClickThroughOverlay.

    def _activate_space_pan(self) -> None:
        if not self._edit_mode:
            return
        if not self._space_pan_active:
            self._space_pan_active = True
            if not self._space_pan_cursor:
                try:
                    QGuiApplication.setOverrideCursor(Qt.SizeAllCursor)
                    self._space_pan_cursor = True
                except Exception:
                    self._space_pan_cursor = False

    def _deactivate_space_pan(self) -> None:
        if self._space_pan_active:
            self._space_pan_active = False
        if self._space_pan_cursor:
            try:
                QGuiApplication.restoreOverrideCursor()
            except Exception:
                log.debug('ignored exception in QGuiApplication.restoreOverrideCursor()', exc_info=True)
            self._space_pan_cursor = False

    def set_area(self, rect: QRect):
        self._physical_area = QRect(rect)
        self._area_size = (float(max(0, rect.width())), float(max(0, rect.height())))
        self._edit_state["area"] = tuple(self._area_size)
        if self._editor_canvas:
            try:
                self._editor_canvas.setAreaSize(*self._area_size)
            except Exception:
                log.debug('ignored exception in self._editor_canvas.setAreaSize(*self._area_size)', exc_info=True)
        if self._edit_mode:
            self._edit_state["area_physical_rect"] = (
                float(rect.x()),
                float(rect.y()),
                float(max(1, rect.width())),
                float(max(1, rect.height())),
            )
            self._update_area_rect_from_physical()
            self._update_area_widgets()
        else:
            self._ensure_geometry()
            self._apply()

    def set_image(self, qimg: QImage):
        self._img = qimg.copy()
        self._apply()

    def _apply(self):
        if not self._physical_area or self._physical_area.isNull() or self._img is None:
            self._label.clear()
            return
        scale = self._ensure_geometry()
        pm = QPixmap.fromImage(self._img)
        try:
            if scale and scale > 0:
                pm.setDevicePixelRatio(scale)
        except Exception:
            log.debug('ignored exception in if scale and scale > 0: pm.setDevicePixelRatio(scale)', exc_info=True)
        self._label.resize(self.size())
        self._label.setPixmap(pm)

    def force_fullscreen_on_next_show(self) -> None:
        self._force_fullscreen_on_show = True

    def show_overlay(self):
        if self._force_fullscreen_on_show:
            self._enter_fullscreen_geometry()
            self._force_fullscreen_on_show = False
        else:
            self._ensure_geometry()
        self.show()
        handle = self.windowHandle()
        if handle and self._target_screen is not None:
            try:
                handle.setScreen(self._target_screen)
            except Exception:
                log.debug('ignored exception in handle.setScreen(self._target_screen)', exc_info=True)
        self.raise_()
        self._apply()

    def hide_overlay(self):
        if self._edit_mode:
            try:
                self.request_finish(True)
            except Exception:
                self.cancel_edit_session()
        else:
            self.cancel_edit_session()
        self.hide()

    # ------- Edit mode helpers -------

    def is_editing(self) -> bool:
        return bool(self._edit_mode)

    def set_view_opacity(self, opacity: float) -> None:
        """Opacity (0..1) of the stencil ghost in view mode."""
        try:
            v = max(0.05, min(1.0, float(opacity)))
        except Exception:
            return
        self._view_opacity = v
        if self._label_effect is not None and not self._edit_mode:
            self._label_effect.setOpacity(v)

    def begin_edit_session(
        self,
        *,
        area_size: tuple[int, int],
        pixmap: Optional[QPixmap],
        rect: Optional[tuple[float, float, float, float]],
        stretch: bool,
    ) -> bool:
        area_w = max(1.0, float(area_size[0]))
        area_h = max(1.0, float(area_size[1]))
        if area_w <= 0 or area_h <= 0:
            return False
        if not self._physical_area or self._physical_area.isNull():
            self._physical_area = QRect(0, 0, int(area_w), int(area_h))
        self._ensure_editor_widgets()
        self._edit_source_pixmap = pixmap if isinstance(pixmap, QPixmap) and not pixmap.isNull() else None
        image_size = None
        if self._edit_source_pixmap is not None:
            image_size = (float(self._edit_source_pixmap.width()), float(self._edit_source_pixmap.height()))
        self._edit_state["area"] = (area_w, area_h)
        self._edit_state["image_size"] = image_size
        self._edit_state["stretch"] = True
        self._edit_state["rect"] = None
        self._edit_state["_rect_is_physical"] = False
        phys_area = (
            float(self._physical_area.x()),
            float(self._physical_area.y()),
            float(max(1, self._physical_area.width())),
            float(max(1, self._physical_area.height())),
        )
        self._edit_state["area_physical_rect"] = phys_area
        if self._editor_canvas:
            self._editor_canvas.setAreaSize(area_w, area_h)
            self._editor_canvas.setPixmap(self._edit_source_pixmap)
            self._update_manual_canvas_constraints()
        self._enter_fullscreen_geometry()
        self._update_area_rect_from_physical()
        self._edit_mode = True
        self._label.setVisible(False)
        self._apply_passthrough(False)
        self._set_mode_buttons(True)
        self._apply_mode_to_canvas()
        self._update_toolbar_state()
        self._update_area_widgets()
        self._update_editor_geometry()
        if self._editor_canvas:
            self._editor_canvas.setVisible(True)
            self._editor_canvas.raise_()
        if self._handle_layer:
            self._handle_layer.setGeometry(self.rect())
            self._handle_layer.setVisible(True)
            self._handle_layer.raise_()
        if self._editor_toolbar:
            self._editor_toolbar.setVisible(True)
            self._editor_toolbar.raise_()
        self.raise_()
        self.activateWindow()
        if self._editor_canvas:
            self._editor_canvas.setFocus()
        return True

    def request_finish(self, commit: bool = True) -> None:
        if not self._edit_mode:
            return
        payload = self._collect_edit_payload()
        self._leave_edit_mode()
        if commit:
            self.editApplied.emit(payload)
        else:
            self.editCancelled.emit()

    def cancel_edit_session(self, emit_signal: bool = False) -> None:
        if not self._edit_mode:
            return
        self._leave_edit_mode()
        if emit_signal:
            self.editCancelled.emit()

    def _leave_edit_mode(self) -> None:
        if not self._edit_mode:
            return
        self._edit_mode = False
        if self._editor_canvas:
            self._editor_canvas.setVisible(False)
            self._editor_canvas.setAttribute(Qt.WA_TransparentForMouseEvents, False)
            self._editor_canvas.setEnabled(True)
        if self._editor_toolbar:
            self._editor_toolbar.setVisible(False)
        self._label.setVisible(True)
        if getattr(self, '_label_effect', None) is not None:
            self._label_effect.setOpacity(self._view_opacity)
        self._apply_passthrough(True)
        self._area_drag_mode = ""
        self._area_drag_button = Qt.MouseButton.NoButton
        self._area_rect_display = QRectF()
        self._area_drag_rect_start = QRectF()
        self._deactivate_space_pan()
        self._set_mode_buttons(bool(self._edit_state.get("stretch", True)))
        if self._handle_layer:
            self._handle_layer.setVisible(False)
        self._ensure_geometry()
        self._apply()

    def _ensure_editor_widgets(self) -> None:
        if self._editor_canvas is None:
            canvas = ManualFitCanvas(self)
            canvas._MARGIN = 0.0  # full-bleed editing area
            canvas.setAutoFit(False)
            canvas.setVisible(False)
            canvas.installEventFilter(self)
            canvas.rectChanged.connect(self._on_canvas_rect_changed)
            canvas.rectChangeFinished.connect(self._on_canvas_rect_finished)
            self._editor_canvas = canvas
        if self._editor_toolbar is None:
            toolbar = QFrame(self, objectName="OverlayEditToolbar")
            toolbar.setVisible(False)
            layout = QHBoxLayout(toolbar)
            layout.setContentsMargins(12, 8, 12, 8)
            layout.setSpacing(8)
            toolbar.setStyleSheet(
                "#OverlayEditToolbar {"
                "background: rgba(12, 12, 12, 210);"
                "border-radius: 10px;"
                "color: white;"
                "}"
                "#OverlayEditToolbar QPushButton, #OverlayEditToolbar QToolButton {"
                "color: white;"
                "padding: 4px 12px;"
                "border: none;"
                "background: rgba(255, 255, 255, 20);"
                "border-radius: 6px;"
                "}"
                "#OverlayEditToolbar QToolButton:checked {"
                "background: rgba(255, 255, 255, 50);"
                "}"
            )
            layout.addItem(QSpacerItem(16, 10, QSizePolicy.Expanding, QSizePolicy.Minimum))
            self._btn_apply = QPushButton(tr("overlay_edit_button_apply"), toolbar)
            self._btn_apply.clicked.connect(lambda: self.request_finish(True))
            self._btn_cancel = QPushButton(tr("overlay_edit_button_cancel"), toolbar)
            self._btn_cancel.clicked.connect(lambda: self.request_finish(False))
            layout.addWidget(self._btn_apply)
            layout.addWidget(self._btn_cancel)
            self._editor_toolbar = toolbar
        pass

    def _update_editor_geometry(self) -> None:
        self._update_area_widgets()
        if self._editor_toolbar:
            margin = 12
            width = max(200, min(self.width() - margin * 2, 620))
            height = self._editor_toolbar.sizeHint().height()
            self._editor_toolbar.setGeometry(margin, margin, width, height)

    def _update_area_widgets(self) -> None:
        if not self._editor_canvas:
            return
        rect = self._area_rect_display.toRect()
        if rect.isNull():
            return
        self._apply_editor_canvas_layout(rect)
        area_w, area_h = self._edit_state.get("area", self._area_size)
        self._editor_canvas.setAreaSize(max(1.0, float(area_w)), max(1.0, float(area_h)))
        self._update_manual_canvas_constraints()

    def _set_mode_buttons(self, stretch: bool) -> None:
        if not self._mode_group:
            return
        blocker = QSignalBlocker(self._mode_group)
        if self._btn_mode_fill:
            self._btn_mode_fill.setChecked(stretch)
        if self._btn_mode_manual:
            self._btn_mode_manual.setChecked(not stretch)
        del blocker

    def _apply_mode_to_canvas(self) -> None:
        if not self._editor_canvas:
            return
        stretch = bool(self._edit_state.get("stretch", True))
        if stretch:
            self._editor_canvas.setAutoFit(True)
        else:
            self._editor_canvas.setAutoFit(False)
            rect = self._edit_state.get("rect")
            if rect is None:
                rect = self._default_rect()
                self._edit_state["rect"] = rect
                self._edit_state["_rect_is_physical"] = False
            self._editor_canvas.setRect(rect)

    def _update_toolbar_state(self) -> None:
        stretch = bool(self._edit_state.get("stretch", True))
        if self._btn_fit:
            self._btn_fit.setEnabled(not stretch)

    def _on_mode_toggled(self, button_id: int, checked: bool) -> None:
        if not checked:
            return
        stretch = button_id == 0
        self._edit_state["stretch"] = stretch
        if stretch:
            self._edit_state["rect"] = None
        else:
            if self._edit_state.get("rect") is None:
                self._edit_state["rect"] = self._default_rect()
        self._apply_mode_to_canvas()
        self._update_toolbar_state()

    def _apply_aspect_fit_rect(self) -> None:
        if bool(self._edit_state.get("stretch", True)):
            if self._btn_mode_manual:
                self._btn_mode_manual.setChecked(True)
            return
        rect = self._compute_aspect_fit_rect()
        if rect is None:
            return
        self._edit_state["rect"] = rect
        self._edit_state["_rect_is_physical"] = False
        if self._editor_canvas:
            self._editor_canvas.setRect(rect)

    def _reset_manual_rect_full(self) -> None:
        if bool(self._edit_state.get("stretch", True)):
            return
        rect = self._default_rect()
        self._edit_state["rect"] = rect
        self._edit_state["_rect_is_physical"] = False
        if self._editor_canvas:
            manual_rect = self._editor_canvas.resetRectToFit()
            if manual_rect:
                self._edit_state["rect"] = tuple(float(v) for v in manual_rect)
                self._edit_state["_rect_is_physical"] = False
                return
        self._apply_mode_to_canvas()

    def _apply_actual_size_rect(self) -> None:
        if bool(self._edit_state.get("stretch", True)):
            return
        rect = self._actual_size_rect()
        if rect is None:
            return
        self._edit_state["rect"] = rect
        self._edit_state["_rect_is_physical"] = False
        if self._editor_canvas:
            manual_rect = self._editor_canvas.resetRectToActualSize()
            if manual_rect:
                self._edit_state["rect"] = tuple(float(v) for v in manual_rect)
                self._edit_state["_rect_is_physical"] = False
                return
            self._editor_canvas.setRect(rect)

    def _actual_size_rect(self) -> Optional[tuple[float, float, float, float]]:
        area_w, area_h = self._edit_state.get("area", (0.0, 0.0))
        if area_w <= 0 or area_h <= 0:
            return None
        img_size = self._edit_state.get("image_size")
        if not img_size:
            return None
        img_w, img_h = float(img_size[0]), float(img_size[1])
        width = min(area_w, max(1.0, img_w))
        height = min(area_h, max(1.0, img_h))
        x = max(0.0, (area_w - width) / 2.0)
        y = max(0.0, (area_h - height) / 2.0)
        return (x, y, width, height)

    def _compute_aspect_fit_rect(self) -> Optional[tuple[float, float, float, float]]:
        area_w, area_h = self._edit_state.get("area", (0.0, 0.0))
        if area_w <= 0 or area_h <= 0:
            return None
        img_size = self._edit_state.get("image_size")
        if not img_size:
            return self._default_rect()
        img_w, img_h = img_size
        if img_w <= 0 or img_h <= 0:
            return self._default_rect()
        scale = min(area_w / img_w, area_h / img_h)
        width = max(1.0, img_w * scale)
        height = max(1.0, img_h * scale)
        left = (area_w - width) / 2.0
        top = (area_h - height) / 2.0
        return (left, top, width, height)

    def _enter_fullscreen_geometry(self) -> None:
        target = self._target_screen
        geo = None
        if target is not None:
            try:
                geo = target.geometry()
            except Exception:
                geo = None
        if geo is None:
            screen = QGuiApplication.primaryScreen()
            if screen is not None:
                geo = screen.geometry()
        if geo is None:
            geo = QRect(0, 0, 800, 600)
        if self.geometry() != geo:
            self.setGeometry(geo)
        self._label.resize(self.size())
        self._last_screen_geometry = QRect(geo)

    def _update_area_rect_from_physical(self) -> None:
        rect = self._physical_area if isinstance(self._physical_area, QRect) else None
        logical = self._logical_rect_from_physical(rect)
        if logical is None or logical.isNull():
            width = max(100.0, self.width() * 0.5)
            height = max(100.0, self.height() * 0.5)
            logical = QRectF(
                (self.width() - width) / 2.0,
                (self.height() - height) / 2.0,
                width,
                height,
            )
        self._area_rect_display = logical
        self._apply_area_rect(logical, update_state=True)

    def _logical_rect_from_physical(self, rect: Optional[QRect]) -> Optional[QRectF]:
        if rect is None:
            return None
        snapshot = self._last_monitor_snapshot
        if snapshot is None:
            return QRectF(rect)
        absolute_rect = desktop_rect_to_ui_rect((rect.x(), rect.y(), rect.width(), rect.height()), snapshot)
        if absolute_rect is None:
            return QRectF(rect)
        sx, sy, _, _ = snapshot.logical_rect
        ax, ay, aw, ah = absolute_rect
        return QRectF(float(ax - sx), float(ay - sy), float(aw), float(ah))

    def _physical_rect_from_logical(self, rect: QRectF) -> Optional[tuple[float, float, float, float]]:
        snapshot = self._last_monitor_snapshot
        if snapshot is None:
            if self._physical_area:
                return (
                    float(self._physical_area.x()),
                    float(self._physical_area.y()),
                    float(self._physical_area.width()),
                    float(self._physical_area.height()),
                )
            return None
        sx, sy, _, _ = snapshot.logical_rect
        desktop_rect = ui_rect_to_desktop_rect(
            (
                int(round(sx + rect.x())),
                int(round(sy + rect.y())),
                max(1, int(round(rect.width()))),
                max(1, int(round(rect.height()))),
            ),
            snapshot,
        )
        if desktop_rect is None:
            return None
        dx, dy, dw, dh = desktop_rect
        return (float(dx), float(dy), float(dw), float(dh))

    def _logical_rect_to_physical(self, rect: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        area = self._edit_state.get("area", (0.0, 0.0))
        area_phys = self._edit_state.get("area_physical_rect")
        if not area_phys:
            return rect
        logical_w = max(1e-6, float(area[0] or 0.0))
        logical_h = max(1e-6, float(area[1] or 0.0))
        physical_w = float(area_phys[2])
        physical_h = float(area_phys[3])
        scale_x = physical_w / logical_w if logical_w else 1.0
        scale_y = physical_h / logical_h if logical_h else 1.0
        offset_x = float(area_phys[0])
        offset_y = float(area_phys[1])
        x, y, w, h = rect
        phys_x = offset_x + x * scale_x
        phys_y = offset_y + y * scale_y
        return (phys_x, phys_y, w * scale_x, h * scale_y)

    def _physical_rect_to_logical(self, rect: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
        area = self._edit_state.get("area", (0.0, 0.0))
        area_phys = self._edit_state.get("area_physical_rect")
        if not area_phys:
            return rect
        physical_w = max(1e-6, float(area_phys[2]))
        physical_h = max(1e-6, float(area_phys[3]))
        logical_w = float(area[0]) if area and area[0] else physical_w
        logical_h = float(area[1]) if area and area[1] else physical_h
        scale_x = logical_w / physical_w if physical_w else 1.0
        scale_y = logical_h / physical_h if physical_h else 1.0
        offset_x = float(area_phys[0])
        offset_y = float(area_phys[1])
        x, y, w, h = rect
        return ((float(x) - offset_x) * scale_x, (float(y) - offset_y) * scale_y, w * scale_x, h * scale_y)

    def _convert_rect_to_logical_if_needed(self) -> None:
        if not self._edit_state.get("_rect_is_physical"):
            return
        rect = self._edit_state.get("rect")
        if rect is None:
            self._edit_state["_rect_is_physical"] = False
            return
        logical_rect = self._physical_rect_to_logical(rect)
        self._edit_state["rect"] = logical_rect
        self._edit_state["_rect_is_physical"] = False
        if self._editor_canvas:
            self._editor_canvas.setRect(logical_rect)

    def _apply_area_rect(self, rect: QRectF, *, update_state: bool = True) -> None:
        bounds = QRectF(0.0, 0.0, float(self.width()), float(self.height()))
        if bounds.isNull():
            return
        rect = QRectF(rect)
        min_size = self._AREA_MIN_SIZE
        rect.setWidth(max(min_size, rect.width()))
        rect.setHeight(max(min_size, rect.height()))
        if rect.right() > bounds.right():
            rect.moveRight(bounds.right())
        if rect.left() < bounds.left():
            rect.moveLeft(bounds.left())
        if rect.bottom() > bounds.bottom():
            rect.moveBottom(bounds.bottom())
        if rect.top() < bounds.top():
            rect.moveTop(bounds.top())
        previous_rect = QRectF(self._area_rect_display)
        self._area_rect_display = rect
        if not previous_rect.isNull():
            self._compensate_manual_rect_for_area_delta(previous_rect, rect)
        if self._editor_canvas:
            self._apply_editor_canvas_layout(rect.toRect())
        if update_state:
            physical = self._physical_rect_from_logical(rect)
            if physical:
                self._edit_state["area_physical_rect"] = physical
                phys_w = max(1.0, float(physical[2]))
                phys_h = max(1.0, float(physical[3]))
                if self._edit_mode and not self._crop_mode:
                    # Live-apply: push the draw region to the engine in real time so
                    # placement takes effect immediately (no "Apply" button needed).
                    try:
                        self.areaLiveChanged.emit(
                            int(round(physical[0])), int(round(physical[1])),
                            int(round(phys_w)), int(round(phys_h)),
                        )
                    except Exception:
                        log.debug("areaLiveChanged emit failed", exc_info=True)
            else:
                phys_w = max(1.0, float(rect.width()))
                phys_h = max(1.0, float(rect.height()))
            self._area_size = (phys_w, phys_h)
            self._edit_state["area"] = (phys_w, phys_h)
            if self._editor_canvas:
                self._editor_canvas.setAreaSize(phys_w, phys_h)
            self._convert_rect_to_logical_if_needed()
            self._apply_mode_to_canvas()
            self._update_manual_canvas_constraints()
        if self._handle_layer and self._handle_layer.isVisible():
            self._handle_layer.update()
        self.update()

    def _apply_editor_canvas_layout(self, area_rect: QRect) -> None:
        if not self._editor_canvas:
            return
        canvas_rect = self._expanded_canvas_rect(area_rect)
        viewport_rect = QRectF(area_rect)
        viewport_rect.translate(-canvas_rect.left(), -canvas_rect.top())
        self._editor_canvas.setGeometry(canvas_rect)
        setter = getattr(self._editor_canvas, "setViewportRect", None)
        if callable(setter):
            setter(viewport_rect)

    def _expanded_canvas_rect(self, area_rect: QRect) -> QRect:
        padding = self._suggest_canvas_padding(area_rect)
        expanded = area_rect.adjusted(-padding, -padding, padding, padding)
        expanded = expanded.intersected(self.rect())
        if expanded.isNull():
            return area_rect
        if not expanded.contains(area_rect):
            expanded = expanded.united(area_rect)
        return expanded

    def _suggest_canvas_padding(self, area_rect: QRect) -> int:
        shorter = max(1, min(area_rect.width(), area_rect.height()))
        dynamic = int(shorter * 0.4)
        padding = max(self._EDITOR_CANVAS_PADDING_MIN, dynamic)
        padding = min(self._EDITOR_CANVAS_PADDING_MAX, padding)
        return padding

    def _manual_fit_size_limit(self) -> Optional[float]:
        limit = 8192.0
        try:
            candidates: list[float] = []
            area_phys = self._edit_state.get("area_physical_rect")
            if area_phys:
                candidates.extend([abs(float(area_phys[2])), abs(float(area_phys[3]))])
            if self._edit_source_pixmap is not None:
                candidates.extend(
                    [float(self._edit_source_pixmap.width()), float(self._edit_source_pixmap.height())]
                )
            if candidates:
                limit = min(8192.0, max(candidates) * 4.0)
        except Exception:
            log.debug('ignored exception in candidates: list[float] = []', exc_info=True)
        if limit is None:
            return None
        return float(max(1.0, limit))

    def _update_manual_canvas_constraints(self) -> None:
        if not self._editor_canvas:
            return
        limit_physical = self._manual_fit_size_limit()
        limit_logical: Optional[float] = None
        if limit_physical is not None:
            area = self._edit_state.get("area", self._area_size)
            area_phys = self._edit_state.get("area_physical_rect")
            if (
                area
                and area_phys
                and len(area) >= 2
                and isinstance(area_phys, (tuple, list))
                and len(area_phys) >= 4
            ):
                logical_w = max(1e-6, float(area[0] or 0.0))
                physical_w = max(1e-6, float(area_phys[2] or 0.0))
                scale = logical_w / physical_w
                limit_logical = max(self._AREA_MIN_SIZE, limit_physical * scale)
        setter = getattr(self._editor_canvas, "setSizeLimit", None)
        if callable(setter):
            setter(limit_logical)

    def _compensate_manual_rect_for_area_delta(self, old_rect: QRectF, new_rect: QRectF) -> None:
        if not self._area_drag_mode:
            return
        if self._edit_state.get("stretch", True):
            return
        manual_rect = self._edit_state.get("rect")
        if manual_rect is None:
            return
        area = self._edit_state.get("area", self._area_size)
        if not area or len(area) != 2:
            return
        area_w = max(1e-6, float(area[0]))
        area_h = max(1e-6, float(area[1]))
        delta_left = new_rect.left() - old_rect.left()
        delta_top = new_rect.top() - old_rect.top()
        if abs(delta_left) < 0.01 and abs(delta_top) < 0.01:
            return
        scale_x = area_w / max(new_rect.width(), 1e-6)
        scale_y = area_h / max(new_rect.height(), 1e-6)
        x, y, w, h = manual_rect
        if abs(delta_left) >= 0.01:
            x -= delta_left * scale_x
        if abs(delta_top) >= 0.01:
            y -= delta_top * scale_y
        self._edit_state["rect"] = (x, y, w, h)
        if self._editor_canvas:
            self._editor_canvas.setRect(self._edit_state["rect"])

    def _attempt_begin_area_drag(self, pos: QPointF, button: Qt.MouseButton) -> bool:
        hit = self._hit_test_area_handle(pos)
        stretch = bool(self._edit_state.get("stretch", True))
        if self._space_pan_active and button == Qt.LeftButton:
            hit = "move"
            self._area_drag_button = Qt.LeftButton
        elif hit == "move" and button == Qt.LeftButton and stretch:
            # PureRef-style: left-drag on the body moves the area (in stretch mode,
            # where the image auto-fits and the left button is otherwise unused).
            self._area_drag_button = Qt.LeftButton
        elif hit and hit != "move" and button == Qt.LeftButton:
            self._area_drag_button = Qt.LeftButton
        else:
            return False
        self._area_drag_start = QPointF(pos)
        self._area_drag_rect_start = QRectF(self._area_rect_display)
        self._area_drag_mode = hit
        return True

    def _maybe_finish_area_drag(self, button: Qt.MouseButton) -> bool:
        if self._area_drag_mode and button == self._area_drag_button:
            self._area_drag_mode = ""
            self._area_drag_button = Qt.MouseButton.NoButton
            return True
        return False

    def eventFilter(self, obj, event):
        if obj is self._editor_canvas and self._edit_mode and self._editor_canvas is not None:
            etype = event.type()
            if etype == QEvent.MouseButtonPress:
                if event.button() == Qt.RightButton:
                    self._show_area_context_menu(event.globalPosition().toPoint())
                    event.accept()
                    return True
                pos = self._map_canvas_point_to_overlay(obj, event.position())
                if self._crop_mode:
                    if self._attempt_begin_crop_drag(pos, event.button()):
                        event.accept()
                        return True
                elif self._attempt_begin_area_drag(pos, event.button()):
                    event.accept()
                    return True
            elif etype == QEvent.MouseMove:
                pos = self._map_canvas_point_to_overlay(obj, event.position())
                if self._crop_drag_mode:
                    self._update_crop_drag(pos)
                    event.accept()
                    return True
                if self._area_drag_mode:
                    self._update_area_drag(pos, event.modifiers())
                    event.accept()
                    return True
                if self._crop_mode:
                    self._editor_canvas.setCursor(self._area_region_cursor(self._crop_hit_test(pos)))
                else:
                    self._update_hover_cursor(pos, widget=self._editor_canvas)
            elif etype == QEvent.MouseButtonRelease:
                if self._maybe_finish_crop_drag(event.button()):
                    event.accept()
                    return True
                if self._maybe_finish_area_drag(event.button()):
                    event.accept()
                    return True
        return super().eventFilter(obj, event)

    def _map_canvas_point_to_overlay(self, widget: QWidget, pos: QPointF) -> QPointF:
        mapped = widget.mapToParent(QPoint(int(round(pos.x())), int(round(pos.y()))))
        return QPointF(float(mapped.x()), float(mapped.y()))

    def _hit_test_area_handle(self, pos: QPointF) -> Optional[str]:
        rect = QRectF(self._area_rect_display)
        if rect.isNull():
            return None
        region = self._area_ctrl.hit_test(
            pos.x(), pos.y(),
            AreaRect(rect.x(), rect.y(), rect.width(), rect.height()),
        )
        return region or None

    def _update_area_drag(self, pos: QPointF, modifiers=Qt.NoModifier) -> None:
        if not self._area_drag_mode:
            return
        start = QRectF(self._area_drag_rect_start)
        bounds = AreaRect(0.0, 0.0, float(self.width()), float(self.height()))
        self._area_ctrl.begin(
            self._area_drag_start.x(), self._area_drag_start.y(),
            self._area_drag_mode,
            AreaRect(start.x(), start.y(), start.width(), start.height()),
        )
        # Shift (or locked aspect) = keep aspect ratio on corners; Alt = no snapping.
        keep_aspect = self._area_lock_aspect or bool(modifiers & Qt.ShiftModifier)
        snap = not bool(modifiers & Qt.AltModifier)
        new = self._area_ctrl.update(pos.x(), pos.y(), bounds, keep_aspect=keep_aspect, snap=snap)
        self._apply_area_rect(QRectF(new.x, new.y, new.w, new.h))

    def _area_region_cursor(self, region: Optional[str]):
        cid = cursor_for_region(region or "")
        return {
            CURSOR_MOVE: Qt.SizeAllCursor,
            CURSOR_SIZE_VER: Qt.SizeVerCursor,
            CURSOR_SIZE_HOR: Qt.SizeHorCursor,
            CURSOR_SIZE_FDIAG: Qt.SizeFDiagCursor,
            CURSOR_SIZE_BDIAG: Qt.SizeBDiagCursor,
        }.get(cid, Qt.ArrowCursor)

    def _update_hover_cursor(self, pos: QPointF, widget=None) -> None:
        if not self._edit_mode:
            return
        target = widget if widget is not None else self
        if self._space_pan_active:
            target.setCursor(Qt.SizeAllCursor)
            return
        region = self._hit_test_area_handle(pos)
        if region and region != "move":
            # On a resize handle: show the directional resize cursor.
            target.setCursor(self._area_region_cursor(region))
        elif widget is None:
            target.setCursor(Qt.ArrowCursor)
        # else: hovering the fit canvas body — leave its own cursor untouched.

    def _nudge_area_with_keys(self, key, modifiers) -> None:
        rect = QRectF(self._area_rect_display)
        if rect.isNull():
            return
        step = 10.0 if (modifiers & Qt.ShiftModifier) else 1.0
        dx = -step if key == Qt.Key_Left else step if key == Qt.Key_Right else 0.0
        dy = -step if key == Qt.Key_Up else step if key == Qt.Key_Down else 0.0
        bounds = AreaRect(0.0, 0.0, float(self.width()), float(self.height()))
        # Ctrl + arrows resize from the bottom-right instead of moving.
        resize = bool(modifiers & Qt.ControlModifier)
        new = self._area_ctrl.nudge(
            AreaRect(rect.x(), rect.y(), rect.width(), rect.height()),
            dx, dy, bounds, resize=resize,
        )
        self._apply_area_rect(QRectF(new.x, new.y, new.w, new.h))

    def _draw_area_size_badge(self, painter: QPainter, rect: QRectF) -> None:
        phys = self._edit_state.get("area_physical_rect")
        if phys and len(phys) >= 4:
            x, y, w, h = (int(round(float(v))) for v in phys[:4])
            text = f"{w} × {h} px   ·   {x}, {y}"
        else:
            text = f"{int(round(rect.width()))} × {int(round(rect.height()))} px"
        painter.save()
        font = painter.font()
        font.setPointSizeF(max(9.0, font.pointSizeF()))
        font.setBold(True)
        painter.setFont(font)
        metrics = painter.fontMetrics()
        pad_x, pad_y = 8.0, 4.0
        bw = metrics.horizontalAdvance(text) + pad_x * 2
        bh = metrics.height() + pad_y * 2
        bx = max(2.0, min(rect.left(), float(self.width()) - bw - 2.0))
        by = rect.top() - bh - 6.0
        if by < 2.0:
            by = rect.top() + 6.0  # no room above the area — draw just inside the top edge
        bg = QRectF(bx, by, bw, bh)
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(20, 22, 28, 200))
        painter.drawRoundedRect(bg, 5, 5)
        painter.setPen(QColor(255, 255, 255, 235))
        painter.drawText(bg, Qt.AlignCenter, text)
        painter.restore()

    # ----- PureRef-style area actions + context menu ---------------------- #
    def _image_aspect(self) -> float:
        pm = self._edit_source_pixmap
        if pm is not None and pm.height() > 0:
            return float(pm.width()) / float(pm.height())
        r = QRectF(self._area_rect_display)
        return (r.width() / r.height()) if r.height() > 0 else 1.0

    def _aspect_box(self, fraction: float) -> QRectF:
        """A rect with the image's aspect ratio, fitted into `fraction` of the
        monitor and centred (display coordinates)."""
        aspect = self._image_aspect()
        max_w = self.width() * fraction
        max_h = self.height() * fraction
        if max_h <= 0 or aspect <= 0:
            return QRectF(self._area_rect_display)
        if aspect >= (max_w / max_h):
            w = max_w
            h = w / aspect
        else:
            h = max_h
            w = h * aspect
        return QRectF((self.width() - w) / 2.0, (self.height() - h) / 2.0, w, h)

    def _area_center_on_monitor(self) -> None:
        r = QRectF(self._area_rect_display)
        if r.isNull():
            return
        r.moveLeft((self.width() - r.width()) / 2.0)
        r.moveTop((self.height() - r.height()) / 2.0)
        self._apply_area_rect(r)

    def _area_fit_image_aspect(self) -> None:
        self._apply_area_rect(self._aspect_box(0.8))

    def _area_fill_monitor(self) -> None:
        self._apply_area_rect(QRectF(0.0, 0.0, float(self.width()), float(self.height())))

    def _area_reset_default(self) -> None:
        self._apply_area_rect(self._aspect_box(0.55))

    def _show_area_context_menu(self, global_pos) -> None:
        if not self._edit_mode:
            return
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:rgba(18,20,27,245);color:#eef2fa;"
            "border:1px solid rgba(120,150,200,160);border-radius:8px;padding:6px;}"
            "QMenu::item{padding:6px 22px;border-radius:6px;}"
            "QMenu::item:selected{background:rgba(70,120,220,200);}"
            "QMenu::separator{height:1px;background:rgba(255,255,255,40);margin:5px 8px;}"
        )
        a_center = menu.addAction(tr("stencil_ctx_center"))
        a_fit = menu.addAction(tr("stencil_ctx_fit"))
        a_fill = menu.addAction(tr("stencil_ctx_fill"))
        a_reset = menu.addAction(tr("stencil_ctx_reset"))
        menu.addSeparator()
        a_lock = menu.addAction(tr("stencil_ctx_lock_aspect"))
        a_lock.setCheckable(True)
        a_lock.setChecked(self._area_lock_aspect)
        menu.addSeparator()
        a_crop = menu.addAction(tr("stencil_ctx_crop"))
        a_crop_reset = menu.addAction(tr("stencil_ctx_crop_reset"))
        menu.addSeparator()
        a_apply = menu.addAction(tr("stencil_ctx_apply"))
        a_cancel = menu.addAction(tr("stencil_ctx_cancel"))
        chosen = menu.exec(global_pos)
        if chosen is None:
            return
        if chosen is a_center:
            self._area_center_on_monitor()
        elif chosen is a_fit:
            self._area_fit_image_aspect()
        elif chosen is a_fill:
            self._area_fill_monitor()
        elif chosen is a_reset:
            self._area_reset_default()
        elif chosen is a_lock:
            self._area_lock_aspect = bool(a_lock.isChecked())
        elif chosen is a_crop:
            self._enter_crop_mode()
        elif chosen is a_crop_reset:
            self.cropResetRequested.emit()
        elif chosen is a_apply:
            self.request_finish(True)
        elif chosen is a_cancel:
            self.request_finish(False)

    # ----- Crop mode: drag the edges to crop the source image ------------- #
    def is_cropping(self) -> bool:
        return bool(self._crop_mode)

    def _enter_crop_mode(self) -> None:
        if not self._edit_mode:
            return
        area = QRectF(self._area_rect_display)
        if area.isNull():
            return
        self._crop_mode = True
        self._crop_rect_display = QRectF(area)
        self._crop_drag_mode = ""
        if self._handle_layer:
            self._handle_layer.update()
        self.update()

    def _exit_crop_mode(self, apply: bool) -> None:
        was = self._crop_mode
        self._crop_mode = False
        self._crop_drag_mode = ""
        if was and apply:
            self._emit_crop()
        if self._handle_layer:
            self._handle_layer.update()
        self.update()

    def _emit_crop(self) -> None:
        area = QRectF(self._area_rect_display)
        crop = QRectF(self._crop_rect_display)
        if area.width() <= 0 or area.height() <= 0 or crop.isNull():
            return
        vl = (crop.left() - area.left()) / area.width()
        vt = (crop.top() - area.top()) / area.height()
        vr = (crop.right() - area.left()) / area.width()
        vb = (crop.bottom() - area.top()) / area.height()
        vl = min(max(0.0, vl), 1.0); vr = min(max(0.0, vr), 1.0)
        vt = min(max(0.0, vt), 1.0); vb = min(max(0.0, vb), 1.0)
        if vr - vl < 1e-3 or vb - vt < 1e-3:
            return
        if (round(vl, 4), round(vt, 4), round(vr, 4), round(vb, 4)) == (0.0, 0.0, 1.0, 1.0):
            return  # whole image selected = no crop change
        self.cropApplied.emit(vl, vt, vr, vb)

    def _crop_hit_test(self, pos: QPointF) -> Optional[str]:
        rect = QRectF(self._crop_rect_display)
        if rect.isNull():
            return None
        region = self._crop_ctrl.hit_test(
            pos.x(), pos.y(), AreaRect(rect.x(), rect.y(), rect.width(), rect.height())
        )
        return region or None

    def _attempt_begin_crop_drag(self, pos: QPointF, button) -> bool:
        if button != Qt.LeftButton:
            return False
        hit = self._crop_hit_test(pos)
        if not hit:
            return False
        self._crop_drag_mode = hit
        self._crop_drag_start = QPointF(pos)
        self._crop_drag_rect_start = QRectF(self._crop_rect_display)
        return True

    def _update_crop_drag(self, pos: QPointF) -> None:
        if not self._crop_drag_mode:
            return
        start = QRectF(self._crop_drag_rect_start)
        area = QRectF(self._area_rect_display)
        bounds = AreaRect(area.x(), area.y(), area.width(), area.height())
        self._crop_ctrl.begin(
            self._crop_drag_start.x(), self._crop_drag_start.y(),
            self._crop_drag_mode,
            AreaRect(start.x(), start.y(), start.width(), start.height()),
        )
        new = self._crop_ctrl.update(pos.x(), pos.y(), bounds, keep_aspect=False, snap=False)
        self._crop_rect_display = QRectF(new.x, new.y, new.w, new.h)
        if self._handle_layer:
            self._handle_layer.update()
        self.update()

    def _maybe_finish_crop_drag(self, button) -> bool:
        if self._crop_drag_mode and button == Qt.LeftButton:
            self._crop_drag_mode = ""
            return True
        return False

    def _paint_crop_overlay(self, painter: QPainter) -> None:
        if not self._crop_mode:
            return
        area = QRectF(self._area_rect_display)
        crop = QRectF(self._crop_rect_display)
        if area.isNull() or crop.isNull():
            return
        outer = QPainterPath(); outer.addRect(area)
        hole = QPainterPath(); hole.addRect(crop)
        painter.fillPath(outer - hole, QColor(0, 0, 0, 140))
        pen = QPen(QColor(255, 200, 90, 240), 2)
        pen.setStyle(Qt.DashLine)
        painter.setPen(pen)
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(crop)
        self._draw_area_handles(painter, crop)

    def _draw_area_handles(self, painter: QPainter, rect: QRectF) -> None:
        handle = self._AREA_HANDLE_SIZE
        half = handle / 2.0
        painter.save()
        painter.setPen(Qt.NoPen)
        painter.setBrush(QColor(255, 255, 255, 220))
        points = [
            QPointF(rect.left(), rect.top()),
            QPointF(rect.center().x(), rect.top()),
            QPointF(rect.right(), rect.top()),
            QPointF(rect.right(), rect.center().y()),
            QPointF(rect.right(), rect.bottom()),
            QPointF(rect.center().x(), rect.bottom()),
            QPointF(rect.left(), rect.bottom()),
            QPointF(rect.left(), rect.center().y()),
        ]
        for point in points:
            painter.drawRoundedRect(
                QRectF(point.x() - half, point.y() - half, handle, handle), 3, 3
            )
        painter.restore()

    def paintEvent(self, event):
        super().paintEvent(event)
        # No full-screen blackout — modern direct manipulation happens over the live
        # target app. Only the area outline + handles (transparent handle layer) and,
        # in crop mode, the crop dim are drawn. This removes the old "huge black square".

    def _paint_area_border(self, painter: QPainter) -> None:
        if not self._edit_mode:
            return
        rect = QRectF(self._area_rect_display)
        if rect.isNull():
            return
        if self._crop_mode:
            painter.setPen(QPen(QColor(255, 255, 255, 90), 1))
            painter.setBrush(Qt.NoBrush)
            painter.drawRect(rect)
            self._paint_crop_overlay(painter)
            return
        painter.setPen(QPen(QColor(255, 255, 255, 220), 2))
        painter.setBrush(Qt.NoBrush)
        painter.drawRect(rect)
        self._draw_area_handles(painter, rect)
        self._draw_area_size_badge(painter, rect)

    def mousePressEvent(self, event):
        if self._edit_mode:
            if event.button() == Qt.RightButton:
                self._show_area_context_menu(event.globalPosition().toPoint())
                event.accept()
                return
            pos = event.position()
            if self._crop_mode:
                if self._attempt_begin_crop_drag(pos, event.button()):
                    event.accept()
                    return
            elif self._attempt_begin_area_drag(pos, event.button()):
                event.accept()
                return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._crop_drag_mode:
            self._update_crop_drag(event.position())
            event.accept()
            return
        if self._area_drag_mode:
            self._update_area_drag(event.position(), event.modifiers())
            event.accept()
            return
        if self._edit_mode and self._crop_mode:
            self.setCursor(self._area_region_cursor(self._crop_hit_test(event.position())))
        elif self._edit_mode:
            self._update_hover_cursor(event.position())
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._maybe_finish_crop_drag(event.button()):
            event.accept()
            return
        if self._maybe_finish_area_drag(event.button()):
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def _on_canvas_rect_changed(self, rect) -> None:
        if rect is None:
            return
        try:
            x, y, w, h = rect
        except Exception:
            return
        self._edit_state["rect"] = (float(x), float(y), float(w), float(h))
        self._edit_state["_rect_is_physical"] = False

    def _on_canvas_rect_finished(self, rect) -> None:
        self._on_canvas_rect_changed(rect)

    def _collect_edit_payload(self) -> dict:
        stretch = bool(self._edit_state.get("stretch", True))
        rect = None
        if not stretch:
            rect = self._edit_state.get("rect")
            if rect is None and self._editor_canvas:
                rect = self._editor_canvas.manualRect()
            if rect is None:
                rect = self._default_rect()
            else:
                rect = tuple(float(v) for v in rect)
            rect = self._logical_rect_to_physical(rect)
        area_rect = self._edit_state.get("area_physical_rect")
        if area_rect is None and self._physical_area:
            area_rect = (
                float(self._physical_area.x()),
                float(self._physical_area.y()),
                float(self._physical_area.width()),
                float(self._physical_area.height()),
            )
        return {"stretch": stretch, "rect": rect, "area": area_rect}

    def _default_rect(self) -> tuple[float, float, float, float]:
        area_w, area_h = self._edit_state.get("area", (0.0, 0.0))
        return (0.0, 0.0, max(1.0, area_w), max(1.0, area_h))

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._update_editor_geometry()
        if getattr(self, "_handle_layer", None):
            self._handle_layer.setGeometry(self.rect())
            self._handle_layer.setVisible(bool(self._edit_mode))

    def keyPressEvent(self, event):
        if self._edit_mode:
            if self._crop_mode:
                if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                    self._exit_crop_mode(True)
                    event.accept()
                    return
                if event.key() == Qt.Key_Escape:
                    self._exit_crop_mode(False)
                    event.accept()
                    return
            if event.key() in (Qt.Key_Return, Qt.Key_Enter):
                self.request_finish(True)
                event.accept()
                return
            if event.key() == Qt.Key_Escape:
                self.request_finish(False)
                event.accept()
                return
            if event.key() == Qt.Key_Space and not event.isAutoRepeat():
                self._activate_space_pan()
                event.accept()
                return
            if event.key() in (Qt.Key_Left, Qt.Key_Right, Qt.Key_Up, Qt.Key_Down):
                self._nudge_area_with_keys(event.key(), event.modifiers())
                event.accept()
                return
            if event.modifiers() & Qt.ControlModifier:
                if event.key() == Qt.Key_0:
                    self._reset_manual_rect_full()
                    event.accept()
                    return
                if event.key() == Qt.Key_1:
                    self._apply_actual_size_rect()
                    event.accept()
                    return
                if event.key() == Qt.Key_2:
                    self._apply_aspect_fit_rect()
                    event.accept()
                    return
        super().keyPressEvent(event)

    def keyReleaseEvent(self, event):
        if self._edit_mode and event.key() == Qt.Key_Space and not event.isAutoRepeat():
            self._deactivate_space_pan()
            event.accept()
            return
        super().keyReleaseEvent(event)

    # ------- Screen / DPI change handlers -------

    def _schedule_apply(self, *args):
        if self._apply_timer.isActive():
            return
        self._apply_timer.start(0)

    def _apply_safe(self):
        try:
            self._apply()
        except RuntimeError:
            log.debug('ignored exception in self._apply()', exc_info=True)

    def _install_screen_watchers(self):
        app = QGuiApplication.instance()
        if app is None:
            return
        try:
            app.screenAdded.connect(self._on_screen_added)
        except Exception:
            log.debug('ignored exception in app.screenAdded.connect(self._on_screen_added)', exc_info=True)
        try:
            app.screenRemoved.connect(self._on_screen_removed)
        except Exception:
            log.debug('ignored exception in app.screenRemoved.connect(self._on_screen_removed)', exc_info=True)
        try:
            app.primaryScreenChanged.connect(self._on_primary_screen_changed)
        except Exception:
            log.debug('ignored exception in app.primaryScreenChanged.connect(self._on_primary_screen_changed)', exc_info=True)
        self._global_watchers_bound = True
        for screen in app.screens():
            self._watch_screen(screen)

    def _ensure_window_handle_binding(self):
        handle = self.windowHandle()
        if handle is None:
            if not self._binding_timer.isActive():
                self._binding_timer.start(50)
            return
        if handle is self._window_handle:
            return
        self._window_handle = handle
        try:
            handle.screenChanged.connect(self._on_window_screen_changed)
        except Exception:
            log.debug('ignored exception in handle.screenChanged.connect(self._on_window_screen_changed)', exc_info=True)
        self._on_window_screen_changed(handle.screen())

    def closeEvent(self, event):
        if self._edit_mode:
            try:
                self.request_finish(True)
            except Exception:
                self.cancel_edit_session()
        try:
            if self._apply_timer.isActive():
                self._apply_timer.stop()
        except Exception:
            log.debug('ignored exception in if self._apply_timer.isActive(): self._apply_timer.stop()', exc_info=True)
        try:
            if self._binding_timer.isActive():
                self._binding_timer.stop()
        except Exception:
            log.debug('ignored exception in if self._binding_timer.isActive(): self._binding_timer.stop()', exc_info=True)
        self._cleanup_window_handle()
        self._remove_screen_watchers()
        self._remove_global_watchers()
        super().closeEvent(event)

    def _cleanup_window_handle(self):
        handle = self._window_handle
        if handle is None:
            return
        try:
            handle.screenChanged.disconnect(self._on_window_screen_changed)
        except Exception:
            log.debug('ignored exception in handle.screenChanged.disconnect(self._on_window_screen_changed)', exc_info=True)
        self._window_handle = None

    def _on_window_screen_changed(self, screen):
        self._watch_screen(screen)
        self._schedule_apply()

    def _on_screen_added(self, screen):
        self._watch_screen(screen)
        self._schedule_apply()

    def _on_screen_removed(self, screen):
        self._unwatch_screen(screen)
        self._schedule_apply()

    def _on_primary_screen_changed(self, screen):
        self._watch_screen(screen)
        self._schedule_apply()

    def _watch_screen(self, screen: Optional[QScreen]):
        if screen is None:
            return
        key = id(screen)
        if key in self._watched_screens:
            return
        self._watched_screens[key] = screen
        for signal_name in _SCREEN_SIGNAL_NAMES:
            signal = getattr(screen, signal_name, None)
            if signal is None:
                continue
            try:
                signal.connect(self._schedule_apply)
            except Exception:
                log.debug('ignored exception in signal.connect(self._schedule_apply)', exc_info=True)

    def _unwatch_screen(self, screen: Optional[QScreen]):
        if screen is None:
            return
        key = id(screen)
        if key not in self._watched_screens:
            return
        for signal_name in _SCREEN_SIGNAL_NAMES:
            signal = getattr(screen, signal_name, None)
            if signal is None:
                continue
            try:
                signal.disconnect(self._schedule_apply)
            except Exception:
                log.debug('ignored exception in signal.disconnect(self._schedule_apply)', exc_info=True)
        self._watched_screens.pop(key, None)

    def _remove_screen_watchers(self):
        for screen in list(self._watched_screens.values()):
            self._unwatch_screen(screen)

    def _remove_global_watchers(self):
        if not self._global_watchers_bound:
            return
        app = QGuiApplication.instance()
        if app is None:
            return
        try:
            app.screenAdded.disconnect(self._on_screen_added)
        except Exception:
            log.debug('ignored exception in app.screenAdded.disconnect(self._on_screen_added)', exc_info=True)
        try:
            app.screenRemoved.disconnect(self._on_screen_removed)
        except Exception:
            log.debug('ignored exception in app.screenRemoved.disconnect(self._on_screen_removed)', exc_info=True)
        try:
            app.primaryScreenChanged.disconnect(self._on_primary_screen_changed)
        except Exception:
            log.debug('ignored exception in app.primaryScreenChanged.disconnect(self._on_primary_screen_changed)', exc_info=True)
        self._global_watchers_bound = False

    def _monitor_info_for_rect(self, rect: Optional[QRect], screen: Optional[QScreen]) -> Optional[Dict[str, Any]]:
        if rect is None or rect.isNull():
            return None
        if os.name != "nt" or _USER32 is None:
            return None
        try:
            cx = int(rect.center().x())
            cy = int(rect.center().y())
        except Exception:
            return None

        try:
            point = _POINT(cx, cy)
            hmon = _USER32.MonitorFromPoint(point, _MONITOR_DEFAULTTONEAREST)
        except Exception:
            hmon = None
        if not hmon:
            return None

        info = _MONITORINFOEX()
        info.cbSize = ctypes.sizeof(info)
        if not _USER32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            return None

        left = int(info.rcMonitor.left)
        top = int(info.rcMonitor.top)
        right = int(info.rcMonitor.right)
        bottom = int(info.rcMonitor.bottom)
        width = max(1, right - left)
        height = max(1, bottom - top)

        dpi_x = dpi_y = 96.0
        if _SHCORE is not None:
            try:
                dpi_x_raw = ctypes.c_uint()
                dpi_y_raw = ctypes.c_uint()
                if _SHCORE.GetDpiForMonitor(
                    hmon,
                    _MDT_EFFECTIVE_DPI,
                    ctypes.byref(dpi_x_raw),
                    ctypes.byref(dpi_y_raw),
                ) == 0:
                    dpi_x = float(dpi_x_raw.value or 96.0)
                    dpi_y = float(dpi_y_raw.value or 96.0)
            except Exception:
                log.debug('ignored exception in dpi_x_raw = ctypes.c_uint()', exc_info=True)

        scale_x = dpi_x / 96.0 if dpi_x > 0 else 1.0
        scale_y = dpi_y / 96.0 if dpi_y > 0 else 1.0
        device = info.szDevice.strip("\x00") if info.szDevice else ""
        return {
            "handle": hmon,
            "rect": (left, top, right, bottom),
            "width": width,
            "height": height,
            "scale_x": scale_x,
            "scale_y": scale_y,
            "device": device,
            "screen": screen,
        }

    def _ensure_geometry(self) -> float:
        if not self._physical_area or self._physical_area.isNull():
            self._logical_area = None
            return 1.0

        physical_tuple = (
            self._physical_area.x(),
            self._physical_area.y(),
            self._physical_area.width(),
            self._physical_area.height(),
        )
        snapshot = select_monitor_snapshot(physical_tuple)
        self._last_monitor_snapshot = snapshot
        screen = self._screen_for_rect(self._physical_area)
        scale = self._effective_scale(screen)
        logical_rect = None
        if snapshot is not None:
            logical_tuple = desktop_rect_to_ui_rect(physical_tuple, snapshot)
            if logical_tuple is not None:
                logical_rect = qrect_from_tuple(logical_tuple)
                self._last_screen_geometry = qrect_from_tuple(snapshot.logical_rect)
                self._last_monitor_info = {
                    "rect": (
                        snapshot.desktop_rect[0],
                        snapshot.desktop_rect[1],
                        snapshot.desktop_rect[0] + snapshot.desktop_rect[2],
                        snapshot.desktop_rect[1] + snapshot.desktop_rect[3],
                    ),
                    "width": snapshot.desktop_rect[2],
                    "height": snapshot.desktop_rect[3],
                    "scale_x": snapshot.scale_x,
                    "scale_y": snapshot.scale_y,
                    "device": snapshot.device_name,
                    "screen": screen,
                }
                scale = max((snapshot.scale_x + snapshot.scale_y) * 0.5, 1e-6)
        if logical_rect is None:
            logical_rect = self._physical_to_logical(self._physical_area, screen, scale)
            if screen is not None:
                self._last_screen_geometry = screen.geometry()

        self._target_screen = screen
        self._current_dpr = scale
        self._logical_area = logical_rect
        self._watch_screen(screen)

        if logical_rect is not None:
            if not self._force_fullscreen_on_show and self.geometry() != logical_rect:
                self.setGeometry(logical_rect)
            if not self._force_fullscreen_on_show:
                self._label.resize(self.size())
        return scale

    def _screen_for_rect(self, rect: QRect) -> Optional[QScreen]:
        if rect is None:
            return QGuiApplication.primaryScreen()
        snapshot = select_monitor_snapshot((rect.x(), rect.y(), rect.width(), rect.height()))
        if snapshot is not None:
            target_name = normalize_monitor_name(snapshot.screen_name or snapshot.device_name or snapshot.monitor_id)
            for screen in QGuiApplication.screens():
                if normalize_monitor_name(screen.name()) == target_name:
                    return screen
        for screen in QGuiApplication.screens():
            if screen.geometry().contains(self.mapToGlobal(QPoint(0, 0))):
                return screen

        return QGuiApplication.primaryScreen()

    def _effective_scale(self, screen: Optional[QScreen]) -> float:
        if screen is None:
            return 1.0
        try:
            ratio = float(screen.devicePixelRatio())
            if ratio > 0:
                return ratio
        except Exception:
            log.debug('ignored exception in ratio = float(screen.devicePixelRatio())', exc_info=True)
        try:
            dpi = float(screen.logicalDotsPerInch())
            if dpi > 0:
                return max(1.0, dpi / 96.0)
        except Exception:
            log.debug('ignored exception in dpi = float(screen.logicalDotsPerInch())', exc_info=True)
        return 1.0

    def _physical_to_logical(self, rect: QRect, screen: Optional[QScreen], scale: float) -> QRect:
        if screen is None or scale <= 0:
            return QRect(rect)

        geo = screen.geometry()
        phy_left = round(geo.x() * scale)
        phy_top = round(geo.y() * scale)

        logical_x = geo.x() + int(round((rect.left() - phy_left) / scale))
        logical_y = geo.y() + int(round((rect.top() - phy_top) / scale))
        logical_w = max(1, int(round(rect.width() / scale)))
        logical_h = max(1, int(round(rect.height() / scale)))

        return QRect(logical_x, logical_y, logical_w, logical_h)
