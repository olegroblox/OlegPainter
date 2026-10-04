# -*- coding: utf-8 -*-
"""Unified live editor: one QGraphicsScene hosting the draw-area/stencil
(`DrawAreaItem`) AND the HUD blocks (`HudBlockItem`) together.

Replaces both the hand-rolled `TopOverlay` editor and the `HudOverlay` arrange
mode with a single professional, direct-manipulation surface (Qt Graphics View):
- move/resize the stencil, crop it (drag edges), flip, opacity — all LIVE, no Apply;
- drag HUD blocks in the same scene;
- click-through when idle, interactive in edit mode.

Coordinate spaces are handled by `BoardView.cover_monitor` (scene == physical
desktop px, window == logical px). HUD render logic is reused via `HudContentMixin`.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, QRectF, QRect, QPointF, Signal
from PySide6.QtGui import QPixmap, QGuiApplication
from PySide6.QtWidgets import QGraphicsItem, QGraphicsProxyWidget, QMenu

from ui.widgets.board_editor import BoardView, DrawAreaItem
from ui.widgets.hud_blocks import HudBlock, HudContentMixin, _BLOCK_ORDER, _BLOCK_TITLE_KEY
from ui.helpers.viewport_mapper import select_monitor_snapshot, collect_monitor_snapshots
from ui.i18n import tr

log = logging.getLogger("olegpainter.unified_board_overlay")

# Same look as the HUD blocks in the legacy overlay (applied per-widget since the
# blocks live in a scene, not under a styled parent).
_HUD_BLOCK_QSS = """
#HudBlock { background: rgba(18,20,27,150); border: 1px solid rgba(255,255,255,40); border-radius: 10px; }
#HudBlock[editing="true"] { border: 1px dashed rgba(120,180,255,220); background: rgba(28,34,48,200); }
#HudBlock[capturing="true"] { border: 2px solid rgba(255,140,76,235); background: rgba(46,32,22,210); }
#HudBlockTitle { color: rgba(150,170,200,255); font-weight:600; }
#HudBlockValue { color: rgba(238,242,250,255); }
#HudBlockBadge { color:#9fc0ff; background: rgba(120,150,210,45);
    border:1px solid rgba(120,150,210,110); border-radius:6px; padding:0px 6px; font-weight:700; }
"""


class HudBlockItem(QGraphicsProxyWidget):
    """A HUD block embedded as a scene item; reports moves as fractional positions."""

    moved = Signal(str, float, float)

    def __init__(self, key: str, block: HudBlock) -> None:
        super().__init__()
        self._key = key
        self.setWidget(block)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged:
            scene = self.scene()
            if scene is not None:
                sr = scene.sceneRect()
                if sr.width() > 0 and sr.height() > 0:
                    fx = (self.scenePos().x() - sr.x()) / sr.width()
                    fy = (self.scenePos().y() - sr.y()) / sr.height()
                    self.moved.emit(self._key, float(fx), float(fy))
        return super().itemChange(change, value)


class UnifiedBoardOverlay(BoardView, HudContentMixin):
    # --- signals consumed by the service (StencilSyncMixin) ---
    areaLiveChanged = Signal(int, int, int, int)
    cropApplied = Signal(float, float, float, float)
    cropResetRequested = Signal()
    flipChanged = Signal(bool, bool)
    editFinished = Signal()
    # --- signals consumed by main_window ---
    stencilOpacityChanged = Signal(int)
    editModeChanged = Signal(bool)
    editAreaRequested = Signal()  # compat (entry point); maps to set_edit_mode(True)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        # HUD content state (HudContentMixin)
        self._statuses = {"state": "idle", "layers": (0, 0), "palette": (0, 0), "hex": False}
        self._hotkeys_map: dict = {}
        self._capture = {"active": False, "kind": None, "count": 0, "max": 0, "slot": None}
        self._last_stats: dict = {}
        self._scale = 1.0
        self._edit_mode = False
        self._stencil_view_opacity = 0.55
        self._flip_h = False
        self._flip_v = False
        self._lock_aspect = False
        self._applying_external = False  # suppress live emit during programmatic set_area
        self._block_fracs: dict[str, tuple[float, float]] = {}

        # draw-area / stencil item
        self._area_item = DrawAreaItem(QRectF(0, 0, 10, 10))
        self._area_item.setZValue(10.0)
        self._area_item.setGhostOpacity(self._stencil_view_opacity)
        self._area_item.geometryChanged.connect(self._on_area_geometry)
        self._area_item.cropApplied.connect(self.cropApplied)  # relay item -> service
        self.scene().addItem(self._area_item)

        # HUD blocks as scene items
        self._blocks: dict[str, HudBlock] = {}
        self._hud_items: dict[str, HudBlockItem] = {}
        from ui.widgets.hud_blocks import _DEFAULT_POS
        for key in _BLOCK_ORDER:
            block = HudBlock(key)
            block.setStyleSheet(_HUD_BLOCK_QSS)
            item = HudBlockItem(key, block)
            item.setZValue(20.0)
            item.moved.connect(self._on_block_moved)
            self.scene().addItem(item)
            self._blocks[key] = block
            self._hud_items[key] = item
            self._block_fracs[key] = _DEFAULT_POS.get(key, (0.83, 0.05))

        self._retranslate_blocks()
        self._set_items_editable(False)
        self._bind_screen_signals()

    # ----- HUD titles / scale --------------------------------------------- #
    def _retranslate_blocks(self) -> None:
        for key, block in self._blocks.items():
            block.set_title(tr(_BLOCK_TITLE_KEY[key]))
            block.set_scale(self._scale)
        self._update_badges()
        self._render_status()
        self.update_stats(self._last_stats)
        self._render_layers()
        self._render_palette()
        self._render_hotkeys()

    # ----- block positions ------------------------------------------------ #
    def _reposition_blocks(self) -> None:
        sr = self.scene().sceneRect()
        if sr.width() <= 0 or sr.height() <= 0:
            return
        for key, item in self._hud_items.items():
            fx, fy = self._block_fracs.get(key, (0.83, 0.05))
            block = self._blocks[key]
            block.adjustSize()
            x = sr.x() + max(0.0, min(fx, 1.0)) * sr.width()
            y = sr.y() + max(0.0, min(fy, 1.0)) * sr.height()
            item.setPos(x, y)

    def _on_block_moved(self, key: str, fx: float, fy: float) -> None:
        self._block_fracs[key] = (fx, fy)

    def reset_layout(self) -> None:
        from ui.widgets.hud_blocks import _DEFAULT_POS
        for key in self._hud_items:
            self._block_fracs[key] = _DEFAULT_POS.get(key, (0.83, 0.05))
        self._reposition_blocks()

    # ----- area geometry → engine (live) ---------------------------------- #
    def _on_area_geometry(self, rect: QRectF) -> None:
        if self._applying_external:
            return
        self.areaLiveChanged.emit(
            int(round(rect.x())), int(round(rect.y())),
            int(round(rect.width())), int(round(rect.height())),
        )

    # ----- facade: methods StencilSyncMixin calls ------------------------- #
    def set_area(self, rect: QRect) -> None:
        snap = select_monitor_snapshot((rect.x(), rect.y(), rect.width(), rect.height())) or self._primary_snapshot()
        if snap is not None:
            self.cover_monitor(snap)
        self._applying_external = True
        try:
            self._area_item.setRect(QRectF(rect.x(), rect.y(), rect.width(), rect.height()))
        finally:
            self._applying_external = False
        self._reposition_blocks()

    def set_image(self, qimg) -> None:
        if qimg is not None and not qimg.isNull():
            self._area_item.setGhostPixmap(QPixmap.fromImage(qimg))

    def set_view_opacity(self, opacity: float) -> None:
        self._stencil_view_opacity = max(0.05, min(1.0, float(opacity)))
        self._area_item.setGhostOpacity(self._stencil_view_opacity)

    def is_editing(self) -> bool:
        return self._edit_mode

    def is_edit_mode(self) -> bool:
        return self._edit_mode

    def cancel_edit_session(self, emit_signal: bool = False) -> None:
        if self._edit_mode:
            self.set_edit_mode(False)
        if emit_signal:
            self.editFinished.emit()

    def force_fullscreen_on_next_show(self) -> None:  # compat no-op
        pass

    # ----- visibility ----------------------------------------------------- #
    def show_overlay(self) -> None:
        if self._snapshot is None:
            snap = self._primary_snapshot()
            if snap is not None:
                self.cover_monitor(snap)
        self.show()
        self.raise_()
        self._reposition_blocks()

    def hide_overlay(self) -> None:
        if self._edit_mode:
            self.set_edit_mode(False)
        self.hide()

    def toggle_visible(self) -> None:
        if self.isVisible():
            self.hide_overlay()
        else:
            self.show_overlay()

    # ----- edit mode ------------------------------------------------------ #
    def set_edit_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._edit_mode:
            if enabled and not self.isVisible():
                self.show_overlay()
            return
        self._edit_mode = enabled
        if enabled and not self.isVisible():
            self.show_overlay()
        self.set_passthrough(not enabled)
        self._set_items_editable(enabled)
        if enabled:
            self.raise_()
        self.editModeChanged.emit(enabled)
        if not enabled:
            self._area_item.set_crop_mode(False)
            self.editFinished.emit()

    def _set_items_editable(self, on: bool) -> None:
        buttons = (Qt.LeftButton | Qt.RightButton) if on else Qt.NoButton
        self._area_item.setAcceptedMouseButtons(buttons)
        self._area_item.setAcceptHoverEvents(on)
        for key, item in self._hud_items.items():
            item.setFlag(QGraphicsItem.ItemIsMovable, on)
            item.setAcceptedMouseButtons(buttons)
            self._blocks[key].set_edit(on)

    # ----- context menu (all live) ---------------------------------------- #
    def contextMenuEvent(self, event) -> None:
        if not self._edit_mode:
            return super().contextMenuEvent(event)
        menu = QMenu(self)
        menu.setStyleSheet(
            "QMenu{background:rgba(18,20,27,245);color:#eef2fa;border:1px solid rgba(120,150,200,160);"
            "border-radius:8px;padding:6px;} QMenu::item{padding:6px 22px;border-radius:6px;}"
            "QMenu::item:selected{background:rgba(70,120,220,200);}"
            "QMenu::separator{height:1px;background:rgba(255,255,255,40);margin:5px 8px;}"
        )
        a_center = menu.addAction(tr("stencil_ctx_center"))
        a_fit = menu.addAction(tr("stencil_ctx_fit"))
        a_fill = menu.addAction(tr("stencil_ctx_fill"))
        a_reset = menu.addAction(tr("stencil_ctx_reset"))
        menu.addSeparator()
        a_crop = menu.addAction(tr("stencil_ctx_crop"))
        a_crop_reset = menu.addAction(tr("stencil_ctx_crop_reset"))
        menu.addSeparator()
        a_flip_h = menu.addAction(tr("stencil_ctx_flip_h"))
        a_flip_v = menu.addAction(tr("stencil_ctx_flip_v"))
        chosen = menu.exec(event.globalPos())
        if chosen is None:
            return
        if chosen is a_center:
            self._area_center()
        elif chosen is a_fit:
            self._area_aspect(0.8)
        elif chosen is a_fill:
            self._area_fill()
        elif chosen is a_reset:
            self._area_aspect(0.55)
        elif chosen is a_crop:
            self._area_item.set_crop_mode(not self._area_item.is_cropping())
        elif chosen is a_crop_reset:
            self.cropResetRequested.emit()
        elif chosen is a_flip_h:
            self._flip_h = not self._flip_h
            self.flipChanged.emit(self._flip_h, self._flip_v)
        elif chosen is a_flip_v:
            self._flip_v = not self._flip_v
            self.flipChanged.emit(self._flip_h, self._flip_v)

    # geometry actions operate on the area item in scene (physical) coords
    def _monitor_rect(self) -> QRectF:
        sr = self.scene().sceneRect()
        return sr if not sr.isNull() else QRectF(0, 0, 1920, 1080)

    def _area_center(self) -> None:
        m = self._monitor_rect()
        r = self._area_item.rect()
        self._area_item.setRect(QRectF(
            m.x() + (m.width() - r.width()) / 2.0, m.y() + (m.height() - r.height()) / 2.0,
            r.width(), r.height(),
        ))

    def _area_aspect(self, fraction: float) -> None:
        m = self._monitor_rect()
        pm = self._area_item._pixmap
        aspect = (pm.width() / pm.height()) if (pm and pm.height() > 0) else (
            self._area_item.rect().width() / max(1.0, self._area_item.rect().height()))
        max_w, max_h = m.width() * fraction, m.height() * fraction
        if aspect >= (max_w / max_h):
            w = max_w; h = w / aspect
        else:
            h = max_h; w = h * aspect
        self._area_item.setRect(QRectF(m.x() + (m.width() - w) / 2.0, m.y() + (m.height() - h) / 2.0, w, h))

    def _area_fill(self) -> None:
        self._area_item.setRect(QRectF(self._monitor_rect()))

    # ----- update_area_size (HUD readout compat — no-op without toolbar) -- #
    def update_area_size(self, rect) -> None:  # compat; readout lives elsewhere now
        return

    # ----- monitor snapshots / watchers ----------------------------------- #
    @staticmethod
    def _primary_snapshot():
        snaps = collect_monitor_snapshots()
        for s in snaps:
            if s.is_primary:
                return s
        return snaps[0] if snaps else None

    def _bind_screen_signals(self) -> None:
        app = QGuiApplication.instance()
        if app is None:
            return
        try:
            app.primaryScreenChanged.connect(lambda *_: self._rebind_monitor())
            app.screenAdded.connect(lambda *_: self._rebind_monitor())
            app.screenRemoved.connect(lambda *_: self._rebind_monitor())
        except Exception:
            log.debug("ignored exception binding screen signals", exc_info=True)

    def _rebind_monitor(self) -> None:
        if not self.isVisible():
            return
        r = self._area_item.rect()
        snap = select_monitor_snapshot((int(r.x()), int(r.y()), int(r.width()), int(r.height()))) or self._primary_snapshot()
        if snap is not None:
            self.cover_monitor(snap)
            self._reposition_blocks()

    # ----- persistence ---------------------------------------------------- #
    def export_state(self) -> dict:
        return {
            "visible": self.isVisible(),
            "scale": self._scale,
            "stencil_opacity": round(self._stencil_view_opacity, 3),
            "positions": {k: list(v) for k, v in self._block_fracs.items()},
            "hidden_blocks": [k for k, b in self._blocks.items() if not b.isVisible()],
        }

    def restore_state(self, state) -> None:
        if not isinstance(state, dict):
            return
        try:
            self._scale = max(0.7, min(1.6, float(state.get("scale", self._scale))))
            op = state.get("stencil_opacity")
            if op is not None:
                self.set_view_opacity(float(op))
            positions = state.get("positions") or {}
            for key, frac in positions.items():
                if key in self._block_fracs and isinstance(frac, (list, tuple)) and len(frac) == 2:
                    self._block_fracs[key] = (float(frac[0]), float(frac[1]))
            hidden = set(state.get("hidden_blocks") or [])
            for key, block in self._blocks.items():
                block.setVisible(key not in hidden)
            for block in self._blocks.values():
                block.set_scale(self._scale)
            self._reposition_blocks()
            if state.get("visible"):
                self.show_overlay()
        except Exception:
            log.debug("ignored exception restoring unified overlay state", exc_info=True)
