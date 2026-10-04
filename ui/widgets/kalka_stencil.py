# -*- coding: utf-8 -*-
"""KalkaStencilOverlay — olegpainter's on-screen stencil / draw-area editor, built on the
Kalka tracing-canvas LOGIC (not the Kalka standalone app).

We reuse Kalka's interactive canvas (``CanvasView`` + ``StencilImageItem``: move / scale /
crop-by-window) but strip its application shell — no system tray, no floating toolbar, no
standalone hotkeys / quit / state.json. olegpainter owns lifecycle, hotkeys and config.

Two modes:
  * PASSIVE (default, after F2): click-through, semi-transparent, no handles — a stencil
    ghost you can see through but not move. This is the "trafaret like before".
  * EDIT (Alt+F2): interactive — move / scale / crop the image; handles visible.

Variant A — GEOMETRY (not render-feedback): the overlay never feeds rendered blobs to the
engine. It only computes WHERE the visible image lands on screen (``draw_region``, physical
px) and WHICH part of the source is visible (``crop_norm`` = left,top,right,bottom). The
engine keeps its real source image and IMAGE_PATH pristine, so config / area-select / re-
prepare never break. Rotation is disabled (the bot draws axis-aligned anyway).
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Signal, QTimer, Qt, QRect, QPoint, QSize
from PySide6.QtGui import QImage, QPixmap, QAction, QIcon
from PySide6.QtWidgets import (
    QPushButton, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QSlider, QToolButton, QFrame,
    QComboBox, QGraphicsDropShadowEffect, QSizePolicy, QLayout,
)

from ui.kalka.overlay_window import OverlayWindow
from ui.overlays.capture_registration import register_screen_tool
from ui.kalka.state import AppState
from ui.overlays import theme as screen_theme
from ui.overlays.theme import ACCENT_INK, panel_styles
from ui.i18n import tr, i18n
from ui.overlays.panels import FloatingPopover, GlassCard
from ui.helpers.icon_tinter import load_svg_icon, themed_svg_icon
from ui.widgets.hud_overlay import _AppLogo
from ui.helpers.viewport_mapper import (
    collect_monitor_snapshots,
    desktop_rect_to_ui_rect,
    normalize_monitor_name,
    select_monitor_snapshot,
    ui_rect_to_desktop_rect,
)

log = logging.getLogger("olegpainter.kalka_stencil")


def _edit_bar_qss() -> str:
    return panel_styles("#KalkaEditBar", painted=True) + """
#KalkaEditBar QToolButton#iconBtn { padding: 6px 10px; }
"""


def _bar_icon(name: str, px: int = 18) -> QIcon:
    """SVG icon (assets/icons) tinted for the current screen-tool theme. Tries a few path
    roots so it resolves regardless of how assets are bundled; returns an empty icon on
    miss (the button keeps its text label)."""
    try:
        return themed_svg_icon(
            [f"icons/{name}.svg", f"{name}.svg", f"assets/icons/{name}.svg"],
            theme=screen_theme.icon_theme(), size=QSize(px, px),
        )
    except Exception:
        return QIcon()


def _ink_icon(name: str, px: int = 16) -> QIcon:
    """Dark-ink glyph for the yellow primary button (readable in both themes)."""
    try:
        return load_svg_icon([f"icons/{name}.svg", f"assets/icons/{name}.svg"],
                             color=ACCENT_INK, size=QSize(px, px))
    except Exception:
        return QIcon()


_SEMANTIC_MODES = ("off", "bg_first", "objects_first")


class _EditToolbar(QWidget):
    """Floating panel shown over the stencil while configuring it (Alt+F2). Modern flat
    look: a solid dark surface + soft shadow (NOT see-through — it floats over an arbitrary
    desktop and must stay legible), quiet borderless icon/label buttons, secondary params
    behind popovers, and a contextual "zone" mode that swaps the middle group for brush
    tools — instead of cramming ~18 controls into one row."""

    def __init__(self, overlay: "KalkaStencilOverlay") -> None:
        # A floating top-level bar (owned by the overlay) positioned ABOVE the stencil —
        # never clipped by / obscuring a small draw area. Qt.Tool keeps it off the taskbar
        # and tied to the overlay's lifetime; WA_QuitOnClose so it never keeps the app alive.
        super().__init__(overlay, Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint)
        register_screen_tool(self)
        self._overlay = overlay
        self.setAttribute(Qt.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WA_QuitOnClose, False)

        # Outer transparent shell with margins → room for the card's drop shadow (the
        # "floating card" cue that reads over any background).
        outer = QVBoxLayout(self)
        outer.setContentsMargins(16, 14, 16, 18)
        # The bar window hugs its content (recomputed on the zone-mode morph) — without this
        # an oversized window lets the "Готово" button (default Minimum policy) balloon to
        # fill the slack.
        outer.setSizeConstraint(QLayout.SetFixedSize)
        self._card = GlassCard(self, radius=16)
        self._card.setObjectName("KalkaEditBar")
        self._shadow = QGraphicsDropShadowEffect(self._card)
        self._shadow.setBlurRadius(36); self._shadow.setOffset(0, 10)
        self._card.setGraphicsEffect(self._shadow)
        outer.addWidget(self._card)

        lay = QHBoxLayout(self._card)
        lay.setContentsMargins(14, 10, 10, 10)
        lay.setSpacing(6)
        lay.addWidget(_AppLogo(20, self._card))
        lay.addSpacing(2)
        self._caption = QLabel()
        self._caption.setObjectName("ScreenPanelTitle")
        lay.addWidget(self._caption)
        lay.addSpacing(12)

        # --- opacity (behind a ☀ popover, not a raw slider in the bar) -------
        self.opacity_btn = QToolButton(); self.opacity_btn.setObjectName("iconBtn")
        self.opacity_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.opacity_btn.setCursor(Qt.PointingHandCursor)
        self.opacity_btn.clicked.connect(self._show_opacity_popover)

        self.opacity = QSlider(Qt.Horizontal)
        self.opacity.setRange(15, 100)
        self.opacity.setFixedWidth(150)
        self.opacity.valueChanged.connect(lambda v: overlay.set_stencil_opacity(v / 100.0))

        self.reset = QToolButton()
        self.reset.setCursor(Qt.PointingHandCursor)
        self.reset.clicked.connect(overlay.reset_stencil)

        # --- show toggles ----------------------------------------------------
        self.border = QToolButton()
        self.border.setCheckable(True); self.border.setCursor(Qt.PointingHandCursor)
        self.border.toggled.connect(overlay.set_border_visible)

        self.planes = QToolButton()
        self.planes.setCheckable(True); self.planes.setCursor(Qt.PointingHandCursor)
        self.planes.toggled.connect(overlay.set_planes_preview)

        # --- semantic order (behind a "Порядок" popover) --------------------
        self.order_btn = QToolButton()
        self.order_btn.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.order_btn.setCursor(Qt.PointingHandCursor)
        self.order_btn.clicked.connect(self._show_order_popover)

        self.semantic_mode = QComboBox()
        for val in _SEMANTIC_MODES:
            self.semantic_mode.addItem("", val)
        self.semantic_mode.currentIndexChanged.connect(overlay._on_toolbar_semantic_mode)

        self.threshold = QSlider(Qt.Horizontal)
        self.threshold.setRange(5, 95)
        self.threshold.setValue(35)
        self.threshold.setFixedWidth(150)
        self.threshold.valueChanged.connect(overlay._on_toolbar_semantic_threshold)
        self.thr_value = QLabel("0.35")
        self.thr_value.setObjectName("thrValue")

        # --- zone brush (contextual mode that swaps the middle group) -------
        self.mask = QToolButton()
        self.mask.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.mask.setCheckable(True); self.mask.setCursor(Qt.PointingHandCursor)
        self.mask.toggled.connect(self._on_zone_toggled)

        self._brush_lbl = QLabel()
        self.mask_brush = QSlider(Qt.Horizontal)
        self.mask_brush.setRange(6, 220)
        self.mask_brush.setValue(48)
        self.mask_brush.setFixedWidth(84)
        self.mask_brush.valueChanged.connect(overlay.set_mask_brush)

        self.mask_erase = QToolButton()
        self.mask_erase.setCheckable(True); self.mask_erase.setCursor(Qt.PointingHandCursor)
        self.mask_erase.toggled.connect(overlay.set_mask_erase)

        self.mask_clear = QToolButton()
        self.mask_clear.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.mask_clear.setCursor(Qt.PointingHandCursor)
        self.mask_clear.clicked.connect(overlay.clear_mask)

        self.mask_save = QToolButton()
        self.mask_save.setCursor(Qt.PointingHandCursor)
        self.mask_save.clicked.connect(overlay.save_mask_dialog)

        self.mask_load = QToolButton()
        self.mask_load.setToolButtonStyle(Qt.ToolButtonTextBesideIcon)
        self.mask_load.setCursor(Qt.PointingHandCursor)
        self.mask_load.clicked.connect(overlay.load_mask_dialog)

        # --- primary (the single accent action) ------------------------------
        self.done_btn = QPushButton()
        self.done_btn.setObjectName("doneBtn")
        self.done_btn.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.done_btn.setCursor(Qt.PointingHandCursor)
        self.done_btn.setIconSize(QSize(15, 15))
        self.done_btn.clicked.connect(lambda: overlay.set_edit_mode(False))

        # Opacity popover: slider + "fit again".
        self._opacity_pop = FloatingPopover(self)
        oprow = QHBoxLayout(); oprow.setSpacing(10)
        self._opacity_lbl = QLabel()
        oprow.addWidget(self._opacity_lbl); oprow.addWidget(self.opacity)
        self._opacity_pop.body.addLayout(oprow)
        self._opacity_pop.body.addWidget(self.border)
        self._opacity_pop.body.addWidget(self.planes)
        self._opacity_pop.body.addWidget(self.reset)

        # Order popover: semantic mode + object threshold.
        self._order_pop = FloatingPopover(self)
        self._order_pop.body.addWidget(self.semantic_mode)
        thr_row = QHBoxLayout(); thr_row.setSpacing(10)
        self._threshold_lbl = QLabel()
        thr_row.addWidget(self._threshold_lbl); thr_row.addWidget(self.threshold); thr_row.addWidget(self.thr_value)
        self._order_pop.body.addLayout(thr_row)

        # Separators kept as fields so whole groups can be shown/hidden on mode switch.
        self._sep2 = self._sep()
        self._sep_zone1 = self._sep(); self._sep_zone2 = self._sep(); self._sep_done = self._sep()

        for w in (self.opacity_btn, self.order_btn, self._sep2,
                  self.mask, self._sep_zone1, self._brush_lbl, self.mask_brush, self.mask_erase,
                  self.mask_clear, self._sep_zone2, self.mask_save, self.mask_load,
                  self._sep_done, self.done_btn):
            lay.addWidget(w)

        # Groups that swap when the zone-brush mode toggles.
        self._normal_widgets = [self.opacity_btn, self.order_btn, self._sep2]
        self._zone_widgets = [self._sep_zone1, self._brush_lbl, self.mask_brush, self.mask_erase,
                              self.mask_clear, self._sep_zone2, self.mask_save, self.mask_load]
        self._restyle()
        self._retranslate()
        self._apply_zone_ui(False)
        screen_theme.watch(self._restyle)
        i18n.languageChanged.connect(self._retranslate)

    def _restyle(self, *_) -> None:
        self.setStyleSheet(_edit_bar_qss())
        self._shadow.setColor(screen_theme.shadow_color())
        self.opacity_btn.setIcon(_bar_icon("sun"))
        self.mask.setIcon(_bar_icon("brush"))
        self.mask_clear.setIcon(_bar_icon("trash"))
        self.mask_load.setIcon(_bar_icon("folder-open"))
        self.order_btn.setIcon(_bar_icon("puzzle"))
        self.done_btn.setIcon(_ink_icon("check"))

    def _retranslate(self, *_) -> None:
        for widget, key in (
            (self.opacity_btn, "view"), (self.reset, "refit"), (self.border, "border"),
            (self.planes, "planes"), (self.order_btn, "order"), (self.mask, "mask"),
            (self.mask_erase, "erase"), (self.mask_clear, "clear"), (self.mask_save, "save"),
            (self.mask_load, "open"), (self.done_btn, "done"),
        ):
            widget.setText(tr(f"stencil_bar_{key}"))
            widget.setToolTip(tr(f"stencil_bar_{key}_tip"))
        self._caption.setText(tr("stencil_bar_title"))
        self._brush_lbl.setText(tr("stencil_bar_brush"))
        self._opacity_lbl.setText(tr("stencil_bar_opacity"))
        self._threshold_lbl.setText(tr("stencil_bar_threshold"))
        self.semantic_mode.setToolTip(tr("stencil_bar_mode_tip"))
        self.threshold.setToolTip(tr("stencil_bar_threshold_tip"))
        self.mask_brush.setToolTip(tr("stencil_bar_brush_tip"))
        for index, mode in enumerate(_SEMANTIC_MODES):
            self.semantic_mode.setItemText(index, tr(f"stencil_bar_mode_{mode}"))
        self.adjustSize()
        if getattr(self._overlay, "_edit_toolbar", None) is self and self.isVisible():
            self._overlay._position_toolbar()

    def hideEvent(self, event):
        self._opacity_pop.hide()
        self._order_pop.hide()
        super().hideEvent(event)

    @staticmethod
    def _sep() -> QFrame:
        s = QFrame(); s.setObjectName("sep"); s.setFrameShape(QFrame.VLine)
        s.setFixedHeight(20)
        return s

    def _show_opacity_popover(self) -> None:
        self._opacity_pop.open_below(self.opacity_btn)

    def _show_order_popover(self) -> None:
        try:
            self._overlay._sync_semantic_toolbar()
        except Exception:
            log.debug("sync semantic before popover failed", exc_info=True)
        self._order_pop.open_below(self.order_btn)

    def _on_zone_toggled(self, on: bool) -> None:
        try:
            self._overlay.set_mask_mode(bool(on))
        except Exception:
            log.debug("set_mask_mode failed", exc_info=True)
        self._apply_zone_ui(bool(on))

    def _apply_zone_ui(self, on: bool) -> None:
        """Zone mode swaps the normal middle group (opacity/show/order) for the brush tools."""
        on = bool(on)
        self._opacity_pop.hide()
        self._order_pop.hide()
        for w in self._normal_widgets:
            w.setVisible(not on)
        for w in self._zone_widgets:
            w.setVisible(on)
        self.adjustSize()
        # Reposition only once the overlay actually owns us (not during construction).
        if getattr(self._overlay, "_edit_toolbar", None) is self:
            try:
                self._overlay._position_toolbar()
            except Exception:
                log.debug("zone-ui reposition failed", exc_info=True)

    def sync(self, *, opacity: float, border: bool) -> None:
        self.opacity.blockSignals(True)
        self.opacity.setValue(int(round(opacity * 100)))
        self.opacity.blockSignals(False)
        self.border.blockSignals(True)
        self.border.setChecked(bool(border))
        self.border.blockSignals(False)


class KalkaStencilOverlay(OverlayWindow):
    contentChanged = Signal()       # window/image changed → service re-syncs geometry
    editModeChanged = Signal(bool)  # edit mode toggled (for UI button sync)
    placementModeChanged = Signal(bool)  # F1 owns input before edit mode begins

    DEFAULT_OPACITY = 0.5           # stencil transparency (user-adjustable via the slider)

    def __init__(self, parent=None) -> None:
        super().__init__(AppState())
        # The selected drawing area is data, not a minimum-size editor panel.
        # Controls live in a separate toolbar; the canvas must fit small regions.
        self._view.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)
        self.setMinimumSize(8, 8)
        self._pending_view_crop = None
        self._edit_mode = False
        self._stencil_opacity = self.DEFAULT_OPACITY
        # The prepared picture the passive stencil shows over the source. Kept while
        # hidden or editing: the preview often arrives then (snapshot, F1 placement)
        # and the passive stencil showed the raw source instead (owner, 2026-10-01).
        self._result_preview = None
        # Restored Alt+F2 zone-mask (base64 PNG), applied lazily once an image is loaded and
        # geometry restored (see restore_state / apply_pending_mask).
        self._pending_mask_b64 = ""
        # This always-on-top tool window must never keep the app alive after the main
        # window closes (otherwise the stencil lingers on screen with no app).
        self.setAttribute(Qt.WA_QuitOnClose, False)
        # Geometry mode: no free rotation (keeps draw_region/crop axis-aligned & exact).
        self._view._rotate_enabled = False
        # Kill the standalone app-shell leftovers.
        try:
            self._toolbar_timer.stop()
        except Exception:
            log.debug("ignored exception stopping toolbar timer", exc_info=True)
        try:
            self._toolbar.hide()
        except Exception:
            log.debug("ignored exception hiding toolbar", exc_info=True)
        # Debounced "content changed" notification (replaces Kalka's JSON-save trigger).
        self._content_timer = QTimer(self)
        self._content_timer.setSingleShot(True)
        self._content_timer.setInterval(180)
        self._content_timer.timeout.connect(self.contentChanged)
        # F1 area placement (rubber-band drawn inside this fullscreen window).
        self._placement_active = False
        self._pre_placement_geom = None
        self._pre_placement_visible = False
        self._view.areaRubberBandFinished.connect(self._on_area_placed)
        # Edit panel (opacity / border / reset / Done) — shown only while configuring.
        self._edit_toolbar = _EditToolbar(self)
        self._edit_toolbar.hide()
        self._done_btn = self._edit_toolbar.done_btn   # alias used by tests
        # Semantic-planes zone preview («Планы» in the edit bar): the provider is
        # wired by main_window and returns a QImage of the translucent zones.
        self.planes_image_provider = None
        self._planes_preview_on = False
        self.contentChanged.connect(self._refresh_planes_preview)
        # Semantic mini-controls (mode combo + threshold slider in the edit bar):
        # main_window wires the service setters and an engine-state provider —
        # the SAME knobs as the draw page's order card, zones refresh live.
        self.semantic_mode_setter = None         # callable(str mode)
        self.semantic_threshold_setter = None    # callable(float 0.05..0.95)
        self.semantic_state_provider = None      # callable() -> {"mode","threshold"}
        self._semantic_syncing = False
        self._semantic_thr_timer = QTimer(self)
        self._semantic_thr_timer.setSingleShot(True)
        self._semantic_thr_timer.setInterval(200)
        self._semantic_thr_timer.timeout.connect(self._commit_toolbar_semantic_threshold)
        # Start passive (semi-transparent ghost).
        self._view.item().setOpacity(self._stencil_opacity)
        self.set_passthrough(True)

    # ----- strip the standalone app shell -------------------------------- #
    def _build_tray(self) -> None:
        # No system tray. Still create the checkable QActions that _sync_ui touches.
        from PySide6.QtWidgets import QSystemTrayIcon
        self._tray = QSystemTrayIcon(QIcon(), self)   # created, never shown
        self._act_top = QAction(self); self._act_top.setCheckable(True)
        self._act_click = QAction(self); self._act_click.setCheckable(True)
        self._act_border = QAction(self); self._act_border.setCheckable(True)
        self._act_toolbar = QAction(self); self._act_toolbar.setCheckable(True)

    def _build_shortcuts(self) -> None:
        # No standalone shortcuts — olegpainter owns global hotkeys. (Esc handled below.)
        pass

    def _tick_toolbar(self) -> None:
        pass

    def _post_show_native(self) -> None:
        # Tool-window + click-through, but DON'T register Kalka's global Ctrl+Alt+X hotkey.
        try:
            from ui.kalka import win_native
            hwnd = int(self.winId())
            win_native.set_tool_window(hwnd, True)
            win_native.set_click_through(hwnd, self._state.window.click_through)
        except Exception:
            log.debug("ignored exception in _post_show_native", exc_info=True)

    # ----- neutralise standalone lifecycle ------------------------------- #
    def quit_app(self, *_) -> None:
        self.hide()           # tray "Выход" must not kill olegpainter

    def _do_save(self) -> None:
        pass                  # olegpainter owns persistence (config), not state.json

    def request_save(self) -> None:
        # Showing/restoring a passive window can resize its viewport. This is not
        # a user edit and must not rebuild the image (especially during drawing).
        if not getattr(self, "_edit_mode", False) or getattr(self, "_placement_active", False):
            return
        self._content_timer.start()   # any window/image change → debounced contentChanged

    def flush_pending(self) -> None:
        """Force a debounced geometry/content change to reach the engine NOW. Call before
        saving the session on shutdown so the last move/resize/zone-paint isn't lost inside
        the 180 ms debounce window."""
        try:
            if self._content_timer.isActive():
                self._content_timer.stop()
                self.contentChanged.emit()
        except Exception:
            log.debug("flush_pending failed", exc_info=True)

    # ----- session persistence (opacity / border / Alt+F2 zone-mask) ----- #
    def export_state(self) -> dict:
        """Overlay state that must survive a restart: stencil opacity, border visibility and
        the painted Alt+F2 draw-zone (base64 PNG). Geometry (draw_region/crop) is persisted
        separately by the engine config, not here."""
        state = {"stencil_opacity": float(self._stencil_opacity)}
        try:
            state["show_border"] = bool(self._state.window.show_border)
        except Exception:
            log.debug("export_state: border read failed", exc_info=True)
        # If a restored mask hasn't been applied to the view yet (stencil never opened this
        # session), preserve the stashed bytes verbatim — re-reading the empty view would
        # silently drop the user's saved zone.
        pending = getattr(self, "_pending_mask_b64", "")
        if pending:
            state["mask_png_b64"] = pending
        else:
            try:
                b64 = self._export_mask_b64()
                if b64:
                    state["mask_png_b64"] = b64
            except Exception:
                log.debug("export_state: mask export failed", exc_info=True)
        return state

    def restore_state(self, state) -> None:
        """Apply opacity/border immediately; stash the zone-mask for apply_pending_mask()
        (the mask needs a loaded image + valid viewport, which only exist after the stencil
        is shown and its geometry restored)."""
        if not isinstance(state, dict):
            return
        op = state.get("stencil_opacity")
        if isinstance(op, (int, float)):
            try:
                self.set_stencil_opacity(float(op))
            except Exception:
                log.debug("restore_state: opacity failed", exc_info=True)
        bd = state.get("show_border")
        if isinstance(bd, bool):
            try:
                self.set_border_visible(bd)
            except Exception:
                log.debug("restore_state: border failed", exc_info=True)
        mb = state.get("mask_png_b64")
        self._pending_mask_b64 = mb if isinstance(mb, str) else ""

    def apply_pending_mask(self) -> bool:
        """Import the stashed Alt+F2 zone-mask onto the current viewport and push it to the
        engine. Call AFTER the source image is loaded and geometry restored. One-shot."""
        b64 = getattr(self, "_pending_mask_b64", "")
        if not b64:
            return False
        self._pending_mask_b64 = ""
        try:
            import base64
            from PySide6.QtCore import QByteArray
            raw = base64.b64decode(b64.encode("ascii"))
            img = QImage()
            if not img.loadFromData(QByteArray(raw), "PNG") or img.isNull():
                return False
            ok = bool(self._view.import_mask_qimage(img))
            if ok:
                self.contentChanged.emit()   # engine re-sync: set_region_mask(zone)
            return ok
        except Exception:
            log.debug("apply_pending_mask failed", exc_info=True)
            return False

    def _export_mask_b64(self) -> str:
        try:
            qimg = self._view.export_mask_qimage()
        except Exception:
            return ""
        if qimg is None or qimg.isNull():
            return ""
        try:
            import base64
            from PySide6.QtCore import QByteArray, QBuffer, QIODevice
            ba = QByteArray()
            buf = QBuffer(ba)
            buf.open(QIODevice.WriteOnly)
            qimg.save(buf, "PNG")
            buf.close()
            return base64.b64encode(bytes(ba)).decode("ascii")
        except Exception:
            log.debug("_export_mask_b64 failed", exc_info=True)
            return ""

    # ----- edit / passive mode ------------------------------------------- #
    def set_edit_mode(self, on: bool, *, capture_on_exit: bool = True) -> None:
        """EDIT: interactive, handles, brighter. PASSIVE: click-through ghost, no handles."""
        on = bool(on)
        if getattr(self, "_placement_active", False):
            self.cancel_area_placement()
        was = self._edit_mode
        self._edit_mode = on
        self.set_passthrough(not on)                      # passive == click-through
        self._view.item().setOpacity(self._stencil_opacity)
        self._view._edit_hint = on
        if on:
            try:
                self._view.hide_preview()                 # edit shows the raw source
                self.raise_(); self.activateWindow()      # stencil keeps keyboard focus
                self._edit_toolbar.sync(opacity=self._stencil_opacity,
                                        border=self._state.window.show_border)
                self._sync_semantic_toolbar()
                self._position_toolbar()
                self._edit_toolbar.show(); self._edit_toolbar.raise_()
            except Exception:
                log.debug("ignored exception activating overlay", exc_info=True)
        else:
            try:
                # leaving edit: stop zone painting (the button toggles set_mask_mode off)
                self._edit_toolbar.mask.setChecked(False)
            except Exception:
                log.debug("ignored exception unchecking mask button", exc_info=True)
            try:
                self._edit_toolbar.hide()
                self._view.deselect()                     # hide handles
            except Exception:
                log.debug("ignored exception deselecting", exc_info=True)
            try:
                self._view.hide_zone_overlay()            # planes preview is edit-only
                self._sync_planes_button(False)
            except Exception:
                log.debug("ignored exception hiding planes preview", exc_info=True)
            self._apply_result_preview()                  # passive shows what will be drawn
            if was and capture_on_exit:
                # The final sync covers a pending debounced change; stop it so the same
                # geometry is not sent twice. Without a final sync (close, cancel) the
                # pending change must survive for flush_pending().
                self._content_timer.stop()
                self.contentChanged.emit()                # sync final geometry immediately
        try:
            self._view.viewport().update()
        except Exception:
            log.debug("ignored exception updating viewport", exc_info=True)
        self.editModeChanged.emit(on)

    def _position_toolbar(self) -> None:
        """Float the bar centered just ABOVE the stencil window (global coords); if there is
        no room above (window at the top edge), tuck it just inside the top."""
        b = self._edit_toolbar
        b.adjustSize()
        g = self.frameGeometry()                          # stencil window, global coords
        x = g.center().x() - b.width() // 2
        y = g.top() - b.height() - 8
        screen = self.screen()
        if screen is not None:
            bounds = screen.availableGeometry()
            if y < bounds.top():
                y = g.top() + 8
            x = max(bounds.left() + 4, min(x, bounds.right() - b.width() - 4))
            y = max(bounds.top() + 4, min(y, bounds.bottom() - b.height() - 4))
        b.move(x, y)

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        if getattr(self, "_edit_mode", False):
            self._position_toolbar()

    def showEvent(self, event) -> None:
        super().showEvent(event)
        pending = getattr(self, "_pending_view_crop", None)
        if pending is not None and not getattr(self, "_placement_active", False):
            self._pending_view_crop = None
            self._view.apply_crop(pending)
        self._apply_result_preview()

    def moveEvent(self, event) -> None:
        super().moveEvent(event)
        if getattr(self, "_edit_mode", False):
            self._position_toolbar()

    def hideEvent(self, event) -> None:
        if getattr(self, "_placement_active", False):
            self.cancel_area_placement()
        if getattr(self, "_edit_mode", False):
            self.set_edit_mode(False, capture_on_exit=False)
        # The floating edit bar must never linger when the stencil is hidden.
        try:
            self._edit_toolbar.hide()
        except Exception:
            log.debug("ignored exception hiding edit toolbar", exc_info=True)
        super().hideEvent(event)

    def closeEvent(self, event) -> None:
        try:
            self._edit_toolbar.close()
        except Exception:
            log.debug("ignored exception closing edit toolbar", exc_info=True)
        super().closeEvent(event)

    # ----- stencil config (opacity / border / reset) -------------------- #
    def set_stencil_opacity(self, value: float) -> None:
        """Stencil transparency (0..1). Applies live to the editable image and the
        passive preview ghost."""
        v = max(0.1, min(1.0, float(value)))
        self._stencil_opacity = v
        try:
            self._view.item().setOpacity(v)
            pi = getattr(self._view, "_preview_item", None)
            if pi is not None:
                pi.setOpacity(v)
        except Exception:
            log.debug("ignored exception setting stencil opacity", exc_info=True)

    def set_border_visible(self, on: bool) -> None:
        """Show/hide the area frame (drawn over the image by the view's foreground)."""
        self._state.window.show_border = bool(on)
        self.update()
        self._view.viewport().update()

    def reset_stencil(self) -> None:
        """Re-fit the whole image into the current window (clears any crop/scale mess)."""
        self._view.fit_image_to_viewport()
        if self._edit_mode:
            self._view.viewport().update()
        self.contentChanged.emit()

    # ----- custom freeform draw-zone (brush mask) ------------------------ #
    def set_mask_mode(self, on: bool) -> None:
        try:
            self._view.set_mask_mode(bool(on))
        except Exception:
            log.debug("set_mask_mode failed", exc_info=True)

    def set_mask_brush(self, px: int) -> None:
        try:
            self._view.set_mask_brush(int(px))
        except Exception:
            log.debug("set_mask_brush failed", exc_info=True)

    def set_mask_erase(self, on: bool) -> None:
        try:
            self._view.set_mask_erase(bool(on))
        except Exception:
            log.debug("set_mask_erase failed", exc_info=True)

    def clear_mask(self) -> None:
        try:
            self._view.clear_mask()
            self.contentChanged.emit()        # engine re-sync: zone removed → full rectangle
        except Exception:
            log.debug("clear_mask failed", exc_info=True)

    def get_region_mask_array(self):
        """Bridge for the service: the painted draw-zone as a boolean numpy array
        (draw-region orientation), or None when nothing is painted."""
        try:
            return self._view.mask_region_array()
        except Exception:
            log.debug("get_region_mask_array failed", exc_info=True)
            return None

    def _masks_dir(self):
        from app_paths import get_app_paths
        d = get_app_paths().configs_root / "masks"
        d.mkdir(parents=True, exist_ok=True)
        return d

    def list_saved_masks(self):
        try:
            return sorted(p.stem for p in self._masks_dir().glob("*.png"))
        except Exception:
            return []

    def save_mask_dialog(self) -> None:
        from PySide6.QtWidgets import QInputDialog, QMessageBox
        try:
            qimg = self._view.export_mask_qimage()
        except Exception:
            log.debug("export_mask_qimage failed", exc_info=True)
            qimg = None
        if qimg is None or qimg.isNull():
            QMessageBox.information(self, tr("stencil_zone_title"), tr("stencil_zone_empty"))
            return
        name, ok = QInputDialog.getText(self, tr("stencil_zone_save_title"), tr("stencil_zone_name"))
        if not ok or not str(name).strip():
            return
        safe = "".join(c for c in str(name).strip() if c not in '<>:"/\\|?*').strip() or "zone"
        try:
            path = self._masks_dir() / f"{safe}.png"
            saved = qimg.save(str(path), "PNG")
        except Exception:
            log.warning("save mask failed", exc_info=True)
            saved = False
        if not saved:
            QMessageBox.warning(self, tr("stencil_zone_title"), tr("stencil_zone_save_failed"))

    def load_mask_dialog(self) -> None:
        from PySide6.QtWidgets import QMenu, QMessageBox
        from PySide6.QtGui import QCursor
        names = self.list_saved_masks()
        if not names:
            QMessageBox.information(self, tr("stencil_zone_title"), tr("stencil_zone_none"))
            return
        menu = QMenu(self)
        for n in names:
            menu.addAction(n)
        act = menu.exec(QCursor.pos())
        if act is None:
            return
        try:
            path = self._masks_dir() / f"{act.text()}.png"
            img = QImage(str(path))
            if img.isNull():
                return
            if self._view.import_mask_qimage(img):
                self.contentChanged.emit()    # engine re-sync with the loaded zone
        except Exception:
            log.debug("load mask failed", exc_info=True)

    def set_planes_preview(self, on: bool) -> None:
        """«Планы» toggle: translucent semantic zones (backdrop / objects /
        details) OVER the stencil image so the user previews what gets painted
        in which order. The zones ride the image item, so move/scale keeps them
        glued; geometry changes re-render via the debounced contentChanged."""
        self._planes_preview_on = bool(on)
        if not self._planes_preview_on:
            try:
                self._view.hide_zone_overlay()
            except Exception:
                log.debug("ignored exception hiding zone overlay", exc_info=True)
            return
        self._refresh_planes_preview()

    def _refresh_planes_preview(self) -> None:
        if not getattr(self, "_planes_preview_on", False):
            return
        try:
            provider = getattr(self, "planes_image_provider", None)
            qimg = provider() if callable(provider) else None
            geom = self._view.stencil_geometry()
            if qimg is None or qimg.isNull() or geom is None:
                self._view.hide_zone_overlay()
                self._sync_planes_button(False)
                return
            crop, _rect = geom
            self._view.set_zone_overlay(qimg, crop)
        except Exception:
            log.debug("ignored exception refreshing planes preview", exc_info=True)

    # ----- semantic mini-controls (mode + threshold in the edit bar) ------ #
    def _sync_semantic_toolbar(self) -> None:
        """Mirror the engine's semantic mode/threshold into the edit bar."""
        provider = getattr(self, "semantic_state_provider", None)
        state = provider() if callable(provider) else None
        if not isinstance(state, dict):
            return
        bar = self._edit_toolbar
        self._semantic_syncing = True
        try:
            combo = getattr(bar, "semantic_mode", None)
            if combo is not None:
                target = str(state.get("mode", "off") or "off").strip().lower()
                for idx in range(combo.count()):
                    if str(combo.itemData(idx) or "") == target:
                        combo.setCurrentIndex(idx)
                        break
            slider = getattr(bar, "threshold", None)
            if slider is not None:
                try:
                    thr = float(state.get("threshold", 0.35))
                except (TypeError, ValueError):
                    thr = 0.35
                value = int(round(max(0.05, min(0.95, thr)) * 100))
                slider.blockSignals(True)
                slider.setValue(value)
                slider.blockSignals(False)
                lbl = getattr(bar, "thr_value", None)
                if lbl is not None:
                    lbl.setText(f"{value / 100.0:.2f}")
        finally:
            self._semantic_syncing = False

    def _on_toolbar_semantic_mode(self, *_):
        if getattr(self, "_semantic_syncing", False):
            return
        combo = getattr(self._edit_toolbar, "semantic_mode", None)
        if combo is None:
            return
        setter = getattr(self, "semantic_mode_setter", None)
        if callable(setter):
            try:
                setter(str(combo.currentData() or "off"))
            except Exception:
                log.debug("ignored exception in semantic mode setter", exc_info=True)
        self._refresh_planes_preview()

    def _on_toolbar_semantic_threshold(self, value: int):
        lbl = getattr(self._edit_toolbar, "thr_value", None)
        if lbl is not None:
            lbl.setText(f"{int(value) / 100.0:.2f}")
        if getattr(self, "_semantic_syncing", False):
            return
        self._semantic_thr_timer.start()

    def _commit_toolbar_semantic_threshold(self):
        slider = getattr(self._edit_toolbar, "threshold", None)
        if slider is None:
            return
        setter = getattr(self, "semantic_threshold_setter", None)
        if callable(setter):
            try:
                setter(int(slider.value()) / 100.0)
            except Exception:
                log.debug("ignored exception in semantic threshold setter", exc_info=True)
        self._refresh_planes_preview()

    def _sync_planes_button(self, on: bool) -> None:
        self._planes_preview_on = bool(on)
        btn = getattr(self._edit_toolbar, "planes", None)
        if btn is not None:
            try:
                btn.blockSignals(True)
                btn.setChecked(bool(on))
            finally:
                btn.blockSignals(False)

    def show_quantized_preview(self, qimg) -> bool:
        """Passive ghost shows the prepared/quantized PREVIEW (what will actually be drawn
        with the current palette/params), positioned over the cropped source region.
        While editing it is only kept, and shown when the stencil turns passive."""
        if qimg is None or qimg.isNull():
            return False
        self._result_preview = qimg.copy()
        return self._apply_result_preview()

    def _apply_result_preview(self) -> bool:
        qimg = getattr(self, "_result_preview", None)
        if (qimg is None or self._edit_mode or getattr(self, "_placement_active", False)
                or not self._view.item().has_image()):
            return False
        geom = self._view.stencil_geometry()
        if geom is None:
            return False
        crop, _rect = geom
        self._view.set_preview(qimg, crop, opacity=self._stencil_opacity)
        return True

    def is_edit_mode(self) -> bool:
        return bool(self._edit_mode)

    def keyPressEvent(self, event) -> None:
        key = event.key()
        if key == Qt.Key_Escape:
            if self._placement_active:
                self._view.cancel_placement()
                event.accept()
                return
            if self._edit_mode:
                self.set_edit_mode(False)
                event.accept()
                return
        if key in (Qt.Key_Return, Qt.Key_Enter) and self._edit_mode:
            self.set_edit_mode(False)         # finish editing → drawable ghost
            event.accept()
            return
        super().keyPressEvent(event)

    # ----- F1 area placement (rubber-band) ------------------------------- #
    def begin_area_placement(self, snapshot=None) -> None:
        """F1: expand to cover the monitor and let the user draw the draw-area as a
        rubber-band rectangle. On release the window snaps to it and switches to edit."""
        if self._placement_active:
            return
        self._pre_placement_edit = self.is_edit_mode()
        self.flush_pending()
        self.set_edit_mode(False, capture_on_exit=False)
        self._pre_placement_geom = self.geometry()
        self._pre_placement_visible = self.isVisible()
        self._placement_active = True
        self._edit_mode = False
        self.placementModeChanged.emit(True)
        lr = getattr(snapshot, "logical_rect", None) if snapshot is not None else None
        if lr and len(lr) == 4:
            self.setGeometry(int(lr[0]), int(lr[1]), int(lr[2]), int(lr[3]))
        self.set_passthrough(False)               # interactive for the rubber-band
        self._view.set_placement_mode(True)
        self.show()
        try:
            self.raise_(); self.activateWindow()
        except Exception:
            log.debug("ignored exception activating overlay for placement", exc_info=True)

    def cancel_area_placement(self) -> None:
        if self._placement_active:
            self._view.cancel_placement()

    def is_placing_area(self) -> bool:
        return self._placement_active

    def _on_area_placed(self, global_rect) -> None:
        self._placement_active = False
        if global_rect is None or global_rect.width() < 8 or global_rect.height() < 8:
            # Cancelled — restore the previous window state.
            if self._pre_placement_geom is not None:
                self.setGeometry(self._pre_placement_geom)
            self.set_edit_mode(self._pre_placement_edit, capture_on_exit=False)
            if not self._pre_placement_visible:
                self.hide()
            self.placementModeChanged.emit(False)
            return
        self._pending_view_crop = None           # a new selection replaces the restored crop
        self.setGeometry(global_rect)             # window == the drawn area (logical px)
        self._view.fit_image_to_viewport()        # show the whole image inside it
        self.set_edit_mode(True)                  # fine-tune; raises/activates
        self.contentChanged.emit()                # sync draw_region + crop to the engine
        self.placementModeChanged.emit(False)

    # ----- olegpainter bridge: source image ------------------------------ #
    def set_source_pixmap(self, pix: QPixmap, source_path: str = "") -> bool:
        ok = self._view.load_pixmap(pix, source_path)
        if ok:
            self._result_preview = None                   # it belonged to the previous picture
            self._view.item().setOpacity(self._stencil_opacity)
        return ok

    def set_source_path(self, path: str) -> bool:
        ok = self._view.load_image(path)
        if ok:
            self._result_preview = None
            self._view.item().setOpacity(self._stencil_opacity)
        return ok

    def has_image(self) -> bool:
        return self._view.item().has_image()

    # ----- olegpainter bridge: draw-time click-through ------------------- #
    def set_passthrough(self, on: bool) -> None:
        """Click-through (or interactive) WITHOUT the tray popup. While the bot draws the
        window sits over draw_region and must be click-through; when editing it must be
        interactive."""
        on = bool(on)
        self._state.window.click_through = on
        # Apply before show()/winId(): post-show native styles alone are too late
        # to prevent Qt from activating the stencil over the drawing target.
        self.setAttribute(Qt.WA_ShowWithoutActivating, on)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, on)
        try:
            from ui.kalka import win_native
            win_native.set_click_through(int(self.winId()), on)
        except Exception:
            log.debug("set_passthrough failed", exc_info=True)

    # ----- olegpainter bridge: geometry (Variant A) ---------------------- #
    def _window_snapshot(self):
        scr = self.screen()
        if scr is None:
            return None
        name = normalize_monitor_name(scr.name())
        for s in collect_monitor_snapshots():
            cand = normalize_monitor_name(s.screen_name or s.device_name or s.monitor_id)
            if cand == name:
                return s
        return None

    @staticmethod
    def _clamp_rect_to_monitor(rect, snap):
        """Keep a LOGICAL-px window rect fully inside a monitor's logical bounds, so a restored
        stencil never opens off-screen or larger than the display (DPI / monitor-layout safety)."""
        try:
            lx, ly, lw, lh = (int(round(float(v))) for v in rect)
        except Exception:
            return rect
        if snap is None or not getattr(snap, "logical_rect", None):
            return (lx, ly, max(1, lw), max(1, lh))
        mx, my, mw, mh = (int(v) for v in snap.logical_rect)
        lw = max(1, min(lw, mw))
        lh = max(1, min(lh, mh))
        lx = max(mx, min(lx, mx + mw - lw))
        ly = max(my, min(ly, my + mh - lh))
        return (lx, ly, lw, lh)

    def get_stencil_geometry(self):
        """Returns ``(draw_region_physical_px, crop_lrtb)`` or ``None``.

        ``draw_region_physical_px`` = (x, y, w, h) on the desktop in PHYSICAL px (engine
        draw_region); ``crop_lrtb`` = (left, top, right, bottom) normalized portion of the
        source visible through the window (engine crop_norm). The engine draws its own real
        source, cropped to ``crop_lrtb``, scaled into ``draw_region`` — i.e. exactly the part
        of the image you see through the glass, where you see it."""
        geom = self._view.stencil_geometry()
        if geom is None:
            return None
        crop, (lx, ly, lw, lh) = geom
        snap = self._window_snapshot()
        if snap is not None:
            phys = ui_rect_to_desktop_rect((lx, ly, lw, lh), snap)
            if phys:
                return tuple(int(round(v)) for v in phys), crop
        scr = self.screen()
        dpr = scr.devicePixelRatio() if scr is not None else 1.0
        draw_region = (
            int(round(lx * dpr)), int(round(ly * dpr)),
            max(1, int(round(lw * dpr))), max(1, int(round(lh * dpr))),
        )
        return draw_region, crop

    def apply_draw_region(self, draw_region_phys, crop_lrtb=None) -> bool:
        """Inverse of get_stencil_geometry: position the stencil window from a restored
        engine ``draw_region`` (PHYSICAL desktop px) so the glass reopens where it was last
        left after a restart. Restores POSITION + SIZE; if ``crop_lrtb`` (the saved engine
        ``crop_norm``) is given, ALSO restores the zoom/crop so the SAME part of the source is
        shown — instead of resetting to the whole image. Returns True if applied."""
        try:
            if not draw_region_phys or len(draw_region_phys) != 4:
                return False
            x, y, w, h = (int(round(float(v))) for v in draw_region_phys)
            if w <= 0 or h <= 0:
                return False
            snaps = collect_monitor_snapshots()
            snap = select_monitor_snapshot((x, y, w, h), snaps) or self._window_snapshot()
            # Did the chosen monitor actually CONTAIN the saved rect's center? If not, its real
            # monitor was unplugged / the layout changed, so select_monitor_snapshot fell back
            # to primary and the physical->logical mapping is unreliable — the clamp below then
            # pulls the stencil onto a real screen instead of opening it off-screen.
            on_monitor = False
            if snap is not None and getattr(snap, "desktop_rect", None):
                mx, my, mw, mh = snap.desktop_rect
                cx, cy = x + w / 2.0, y + h / 2.0
                on_monitor = (mx <= cx < mx + mw and my <= cy < my + mh)
            logical = desktop_rect_to_ui_rect((x, y, w, h), snap) or (x, y, w, h)
            lx, ly, lw, lh = (int(round(float(v))) for v in logical)
            if lw <= 0 or lh <= 0:
                return False
            clamped = self._clamp_rect_to_monitor((lx, ly, lw, lh), snap)
            if not on_monitor and clamped != (lx, ly, lw, lh):
                log.info(
                    "apply_draw_region: saved monitor changed/gone — remapped %s -> %s on %s",
                    (x, y, w, h), clamped, getattr(snap, "monitor_id", "?"),
                )
            lx, ly, lw, lh = clamped
            # Keep the persisted WindowState in sync so a later _apply_state_to_ui can't
            # clobber the restored rect back to the hardcoded default (220,160,640,520).
            try:
                self._state.window.x = lx
                self._state.window.y = ly
                self._state.window.width = lw
                self._state.window.height = lh
            except Exception:
                log.debug("apply_draw_region: window-state sync failed", exc_info=True)
            self.setGeometry(QRect(lx, ly, lw, lh))
            try:
                # Restore the saved crop/zoom if we have it; otherwise show the whole image.
                if not self.isVisible():
                    # Hidden QWidget children receive their resize on show. Fitting now
                    # would use the previous viewport, and can move the source offscreen.
                    self._pending_view_crop = tuple(crop_lrtb) if crop_lrtb is not None else (0, 0, 1, 1)
                else:
                    applied_crop = bool(crop_lrtb is not None and self._view.apply_crop(crop_lrtb))
                    if not applied_crop:
                        self._view.fit_image_to_viewport()
            except Exception:
                log.debug("apply_draw_region: fit/crop failed", exc_info=True)
            return True
        except Exception:
            log.debug("apply_draw_region failed", exc_info=True)
            return False
