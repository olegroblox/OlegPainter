"""Desktop tools shared by application shells; no MainWindow dependency.

The existing Qt overlay renderers remain in use during the QML migration.
This workspace owns their service bindings, state and lifecycle.
"""
from __future__ import annotations

import logging
from PySide6.QtCore import QObject, Signal, Qt, QTimer
from PySide6.QtWidgets import QApplication
from ui.i18n import tr
from ui.widgets.hud_overlay import HudOverlay
from ui.widgets.unified_board_overlay import UnifiedBoardOverlay
from ui.widgets.kalka_stencil import KalkaStencilOverlay
from ui.services.stencil_sync import USE_UNIFIED_OVERLAY, USE_KALKA_OVERLAY
from ui.overlays.controller import DesktopOverlayController

log = logging.getLogger("olegpainter.desktop_workspace")


class DesktopWorkspace(QObject):
    hudVisibilityChanged = Signal(bool)
    stateChanged = Signal()

    def __init__(self, service, parent=None):
        if not isinstance(QApplication.instance(), QApplication):
            raise RuntimeError("Desktop tools require QApplication")
        super().__init__(parent)
        self.service = service
        self._closed = False
        self._bindings = []
        self._brush_dialog = None
        self.brush_learning_handler = None
        self._kalka_source_key = None
        self._kalka_geometry_restored = False
        self.hud_overlay = UnifiedBoardOverlay() if USE_UNIFIED_OVERLAY else HudOverlay()
        self.kalka_overlay = KalkaStencilOverlay() if USE_KALKA_OVERLAY else None
        self.hud_overlay.setAttribute(Qt.WA_QuitOnClose, False)
        self.hud_overlay.editModeChanged.connect(self._hud_edit_changed)
        self.hud_overlay.editAreaRequested.connect(self.edit_draw_area)
        k = self.kalka_overlay
        if k is not None:
            k.setAttribute(Qt.WA_QuitOnClose, False)
            k.planes_image_provider = self._kalka_planes_image
            k.semantic_mode_setter = service.set_semantic_order_mode
            k.semantic_threshold_setter = service.set_semantic_threshold
            k.semantic_state_provider = self._kalka_semantic_state
            k.contentChanged.connect(self._on_kalka_changed)
            k.contentChanged.connect(self.stateChanged)
            for name, slot in (
                ("kalkaToggleRequested", self.toggle_stencil),
                ("kalkaEditRequested", self.edit_stencil),
                ("kalkaSelectAreaRequested", self.select_area),
                ("previewReady", self._on_engine_preview_ready),
            ):
                self._bind(name, slot)
        self.desktop_overlays = DesktopOverlayController(self)
        self.desktop_overlays.bind_service(service)
        if USE_UNIFIED_OVERLAY:
            service.adopt_overlay(self.hud_overlay)
        overlay = self.hud_overlay
        for name, slot in (
            ("drawingStatsChanged", overlay.update_stats),
            ("appLayersChanged", self._on_hud_layers),
            ("manualPaletteChanged", self._on_hud_palette),
            ("captureStateChanged", self._on_hud_capture),
            ("toggleOverlayRequested", self.toggle_hud),
            ("layoutEditRequested", self.toggle_hud_edit),
            ("areaChanged", overlay.update_area_size),
            ("hotkeysChanged", self._on_hud_hotkeys),
            ("dynamicBrushLearningRequested", self._open_brush_learning),
        ):
            self._bind(name, slot)
        for name in ("previewReady", "configLoaded", "areaChanged", "drawingStateChanged", "sessionStateChanged"):
            self._bind(name, self._feed_hud_engine_info)
        self._on_hud_hotkeys(service.get_hotkeys())
        self._on_hud_layers(len(service.engine.target_app_layer_coords or []))
        self._on_hud_palette(len(service.engine.manual_palette_coords or []))
        self._feed_hud_engine_info()

    def _bind(self, name, slot):
        signal = getattr(self.service, name)
        signal.connect(slot)
        self._bindings.append((signal, slot))

    def _open_brush_learning(self, payload=None):
        if self._closed:
            return
        if self.brush_learning_handler is not None:
            self.brush_learning_handler(payload)
            return
        if self._brush_dialog is not None:
            self._brush_dialog.raise_()
            self._brush_dialog.activateWindow()
            return
        from ui.widgets.dynamic_brush_learning_dialog import DynamicBrushLearningDialog
        dialog = DynamicBrushLearningDialog(self.service)
        self._brush_dialog = dialog
        try:
            accepted = dialog.exec()
        finally:
            self._brush_dialog = None
            dialog.deleteLater()
        if accepted and isinstance(payload, dict) and payload.get("auto_start"):
            QTimer.singleShot(0, self._start_after_learning)

    def _start_after_learning(self):
        if not self._closed:
            self.service.start_pause()

    def _hud_edit_changed(self, *_):
        self.hudVisibilityChanged.emit(self.hud_overlay.isVisible())
        self.stateChanged.emit()

    def export_state(self):
        state = {"overlay": self.hud_overlay.export_state()}
        if self.kalka_overlay is not None:
            state["kalka"] = self.kalka_overlay.export_state()
        return state

    def restore_state(self, state):
        if isinstance(state.get("overlay"), dict):
            self.hud_overlay.restore_state(state["overlay"])
        if self.kalka_overlay is not None and isinstance(state.get("kalka"), dict):
            self.kalka_overlay.restore_state(state["kalka"])
        self.hudVisibilityChanged.emit(self.hud_overlay.isVisible())

    def prepare_save(self):
        # Cancel temporary F1 geometry before persisting the user's real region.
        self.desktop_overlays.prepare("save")
        if self.kalka_overlay is not None and self.kalka_overlay.isVisible():
            self.kalka_overlay.flush_pending()

    def close(self):
        if self._closed:
            return
        if self._brush_dialog is not None:
            self._brush_dialog.reject()
        self.desktop_overlays.shutdown()
        self._closed = True
        for signal, slot in self._bindings:
            signal.disconnect(slot)
        self._bindings.clear()
        if self.kalka_overlay is not None:
            self.kalka_overlay.contentChanged.disconnect(self._on_kalka_changed)
            self.kalka_overlay.close()
            self.kalka_overlay.deleteLater()
        self.hud_overlay.close()
        self.hud_overlay.deleteLater()

    def set_hud_visible(self, checked: bool) -> None:
        overlay = getattr(self, "hud_overlay", None)
        if overlay is None:
            return
        if checked:
            overlay.show_overlay()
        else:
            overlay.hide_overlay()
        self.hudVisibilityChanged.emit(overlay.isVisible())
        self.stateChanged.emit()


    def edit_hud(self) -> None:
        overlay = getattr(self, "hud_overlay", None)
        if overlay is None:
            return
        overlay.set_edit_mode(True)
        self.hudVisibilityChanged.emit(self.hud_overlay.isVisible())


    def edit_draw_area(self) -> None:
        """'Edit on screen' (HUD toolbar / control-bar) → on-screen stencil/area editor."""
        service = getattr(self, "service", None)
        if service is None:
            return
        try:
            service.edit_stencil()
        except Exception:
            log.debug("ignored exception in service.edit_stencil()", exc_info=True)


    def toggle_hud_edit(self) -> None:
        """Alt+F2 / unified entry: toggle the layout edit mode (HUD blocks + draw area)."""
        overlay = getattr(self, "hud_overlay", None)
        if overlay is None:
            return
        if overlay.is_edit_mode():
            overlay.set_edit_mode(False)
        else:
            if not overlay.isVisible():
                overlay.show_overlay()
            overlay.set_edit_mode(True)
        self.hudVisibilityChanged.emit(self.hud_overlay.isVisible())


    def toggle_stencil(self) -> None:
        """Stencil toggle (F2) → show/hide the stencil. On show it is PASSIVE: a
        semi-transparent, click-through ghost you can see through but not move (configure
        it with Alt+F2)."""
        k = getattr(self, "kalka_overlay", None)
        if k is None:
            return
        if k.isVisible():
            k.hide()
            return
        self._load_current_image_into_kalka()
        if not k.has_image():
            # Nothing to trace — don't pop an empty glass pane the user has to dismiss.
            svc = getattr(self, "service", None)
            if svc is not None:
                try:
                    svc.statusChanged.emit(tr("status_open_image_first"))
                except Exception:
                    log.debug("ignored exception emitting no-image status", exc_info=True)
            return
        k.set_edit_mode(False, capture_on_exit=False)   # passive ghost
        k.show()
        try:
            k.raise_()
        except Exception:
            log.debug("ignored exception raising kalka overlay", exc_info=True)
        # F2 changes visibility only. Geometry is committed by editor events,
        # never by a delayed capture of the passive window after showing it.


    def select_area(self) -> None:
        """F1 → modern rubber-band area placement: the stencil expands to the monitor under
        the cursor, the user drags the draw area, and the window snaps to it (then edit)."""
        k = getattr(self, "kalka_overlay", None)
        if k is None:
            return
        self._load_current_image_into_kalka()
        if not k.has_image():
            svc = getattr(self, "service", None)
            if svc is not None:
                try:
                    svc.statusChanged.emit(tr("status_open_image_first"))
                except Exception:
                    log.debug("ignored exception emitting no-image status", exc_info=True)
            return
        snap = self._monitor_snapshot_under_cursor()
        if not self.desktop_overlays.prepare("stencil"):
            return
        k.begin_area_placement(snap)


    def _monitor_snapshot_under_cursor(self):
        """The MonitorSnapshot for the screen currently under the mouse cursor (so F1
        places the area on the monitor the user is working on)."""
        try:
            from PySide6.QtGui import QCursor
            from PySide6.QtWidgets import QApplication
            from ui.helpers.viewport_mapper import (
                collect_monitor_snapshots, normalize_monitor_name,
            )
            gp = QCursor.pos()
            scr = QApplication.screenAt(gp) or QApplication.primaryScreen()
            if scr is None:
                return None
            name = normalize_monitor_name(scr.name())
            for s in collect_monitor_snapshots():
                cand = normalize_monitor_name(s.screen_name or s.device_name or s.monitor_id)
                if cand == name:
                    return s
        except Exception:
            log.debug("ignored exception resolving monitor under cursor", exc_info=True)
        return None


    def edit_stencil(self) -> None:
        """Alt+F2 / 'Edit stencil' → toggle EDIT mode: interactive move / scale / crop.
        Exiting edit (Alt+F2 again or Esc) returns to the passive ghost and syncs geometry."""
        k = getattr(self, "kalka_overlay", None)
        if k is None:
            return
        # Always pull the current source BEFORE editing so Alt+F2 never opens onto a stale
        # image (e.g. after a clipboard paste while the ghost was already visible). With the
        # content-based key this is a no-op when the source hasn't actually changed, so it
        # does not disturb the user's position/scale/crop.
        self._load_current_image_into_kalka()
        if not k.isVisible():
            if not k.has_image():
                svc = getattr(self, "service", None)
                if svc is not None:
                    try:
                        svc.statusChanged.emit(tr("status_open_image_first"))
                    except Exception:
                        log.debug("ignored exception emitting no-image status", exc_info=True)
                return
            k.set_edit_mode(False, capture_on_exit=False)
            k.show()
        k.set_edit_mode(not k.is_edit_mode())
        self.hudVisibilityChanged.emit(self.hud_overlay.isVisible())


    def edit_stencil_zone(self) -> None:
        """«Обработка» → «Нарисовать зону»: the stencil opens for editing with the
        zone brush on — paint what to draw (the manual way to drop a background)."""
        k = getattr(self, "kalka_overlay", None)
        if k is None:
            return
        if not (k.isVisible() and k.is_edit_mode()):
            self.edit_stencil()
        if k.is_edit_mode():
            try:
                k._edit_toolbar.mask.setChecked(True)
            except Exception:
                log.debug("zone brush not switched on", exc_info=True)


    def _kalka_planes_image(self):
        """QImage of the translucent semantic zones (backdrop / objects /
        details) for the stencil «Планы» toggle, or None when no prepared
        picture / no recognisable planes exist."""
        svc = getattr(self, "service", None)
        engine = getattr(svc, "engine", None) if svc is not None else None
        if engine is None:
            return None
        try:
            pil = engine.build_semantic_planes_preview()
            if pil is None:
                return None
            from PIL import ImageQt
            from PySide6.QtGui import QImage as _QImage
            return _QImage(ImageQt.ImageQt(pil)).copy()
        except Exception:
            log.debug("ignored exception building planes preview", exc_info=True)
            return None


    def _kalka_semantic_state(self):
        """Engine semantic mode/threshold for the stencil toolbar mini-controls."""
        engine = getattr(getattr(self, "service", None), "engine", None)
        if engine is None:
            return None
        try:
            thr = float(getattr(engine, "semantic_threshold", 0.35))
        except (TypeError, ValueError):
            thr = 0.35
        return {
            "mode": str(getattr(engine, "semantic_order_mode", "off") or "off"),
            "threshold": thr,
        }


    def _on_engine_preview_ready(self, qimg=None) -> None:
        """A new quantized preview was prepared. Refresh the Kalka source if it is a new
        image, and (in passive mode) show that preview as the stencil ghost — so the
        passive trafaret looks like what will actually be drawn (palette/params applied)."""
        k = getattr(self, "kalka_overlay", None)
        if k is None:
            return
        self._load_current_image_into_kalka()
        if qimg is not None:
            try:
                # Kept even while hidden or editing: shown once the stencil is passive.
                k.show_quantized_preview(qimg)
            except Exception:
                log.debug("ignored exception showing quantized preview in kalka", exc_info=True)


    def _on_kalka_changed(self) -> None:
        """Kalka content/geometry changed → re-sync engine source + draw_region."""
        k = getattr(self, "kalka_overlay", None)
        service = getattr(self, "service", None)
        if self._closed or k is None or service is None or not k.isVisible() or k._placement_active:
            return
        try:
            service.capture_from_kalka(k)
        except Exception:
            log.debug("ignored exception in capture_from_kalka", exc_info=True)


    def _load_current_image_into_kalka(self, *, force: bool = False) -> bool:
        """Load the ORIGINAL source image into the Kalka canvas — but only when it is
        genuinely new. Kalka owns the full image; the window crops it non-destructively
        and the user's transform must survive show/hide.

        Legacy sessions may contain a rendered source named ``<Калька>``. Do not feed
        that cropped render back into the editor. The current capture_from_kalka path
        synchronizes geometry/crop/mask and preserves the original engine source."""
        k = getattr(self, "kalka_overlay", None)
        service = getattr(self, "service", None)
        if k is None or service is None:
            return False
        engine = getattr(service, "engine", None)
        if engine is None:
            return False
        src_name = str(getattr(engine, "IMAGE_PATH", "") or "")
        if src_name == "<Калька>":
            # The engine source is our own rendered crop — keep Kalka's original intact.
            return False
        src = getattr(engine, "source_pil_image", None)
        # CONTENT-based key, not just IMAGE_PATH: for clipboard images IMAGE_PATH is a
        # CONSTANT label ("<Изображение из буфера>"), so two different pasted images share
        # it — keying on it alone left the FIRST pasted image stuck in the stencil. The
        # source object identity + size distinguishes successive clipboard images.
        src_key = (src_name, id(src) if src is not None else 0, tuple(getattr(src, "size", ()) or ()))
        if not force and k.has_image() and src_key == self._kalka_source_key:
            # Genuinely the same image already in Kalka — preserve position/scale/crop.
            return False
        try:
            from ui.services.image_utils import pil_to_qimage
            from PySide6.QtGui import QPixmap
            loaded = False
            if src is not None:
                qimg = pil_to_qimage(src)
                if qimg is not None and not qimg.isNull():
                    loaded = bool(k.set_source_pixmap(QPixmap.fromImage(qimg)))
            if not loaded and src_name and not src_name.startswith("<"):
                loaded = bool(k.set_source_path(src_name))
            if loaded:
                self._kalka_source_key = src_key
                if not self._kalka_geometry_restored:
                    region = getattr(engine, "draw_region", None)
                    crop = None
                    try:
                        if callable(getattr(engine, "get_crop_norm", None)):
                            crop = tuple(engine.get_crop_norm())
                    except Exception:
                        crop = None
                    if region and len(region) == 4:
                        # Reopen the stencil where it was last left, with the SAME crop/zoom
                        # (engine.draw_region + crop_norm were restored from the session).
                        # Deferred so it runs AFTER the caller's k.show(), when the viewport
                        # size is valid for the image fit/crop.
                        QTimer.singleShot(0, lambda r=tuple(region), c=crop: self._restore_kalka_geometry_once(r, c))
            return loaded
        except Exception:
            log.debug("ignored exception loading image into kalka", exc_info=True)
            return False


    def _restore_kalka_geometry_once(self, region, crop=None) -> None:
        """One-shot after a restart: position the Kalka stencil at the saved draw_region and
        restore its crop/zoom. Consumed only once the overlay is actually visible (so the
        image fit/crop has a real viewport); thereafter the user's live drags own the geometry."""
        if self._closed or self._kalka_geometry_restored:
            return
        k = getattr(self, "kalka_overlay", None)
        if k is None:
            return
        if k.is_placing_area():
            # A queued session restore must not shrink F1's full-screen surface.
            return
        try:
            applied = bool(k.apply_draw_region(region, crop))
        except Exception:
            log.debug("kalka apply_draw_region failed", exc_info=True)
            return
        if applied and k.isVisible():
            self._kalka_geometry_restored = True
            # Geometry + crop are in place → now re-apply the saved Alt+F2 zone-mask onto the
            # (correctly cropped) viewport and push it to the engine (one-shot).
            try:
                if callable(getattr(k, "apply_pending_mask", None)):
                    k.apply_pending_mask()
            except Exception:
                log.debug("kalka apply_pending_mask failed", exc_info=True)


    def toggle_hud(self) -> None:
        """Invoked by the global hotkey via service.toggleOverlayRequested."""
        overlay = getattr(self, "hud_overlay", None)
        if overlay is None:
            return
        overlay.toggle_visible()
        self.hudVisibilityChanged.emit(self.hud_overlay.isVisible())


    def _on_hud_layers(self, count) -> None:
        overlay = getattr(self, "hud_overlay", None)
        if overlay is None:
            return
        engine = getattr(getattr(self, "service", None), "engine", None)
        maximum = int(getattr(engine, "max_definable_app_layers", 0) or 0)
        overlay.set_layers(int(count or 0), maximum)


    def _on_hud_palette(self, count) -> None:
        overlay = getattr(self, "hud_overlay", None)
        if overlay is None:
            return
        engine = getattr(getattr(self, "service", None), "engine", None)
        maximum = int(getattr(engine, "max_manual_palette_colors", 0) or 0)
        overlay.set_palette(int(count or 0), maximum)


    def _on_hud_hotkeys(self, payload) -> None:
        overlay = getattr(self, "hud_overlay", None)
        if overlay is not None and isinstance(payload, dict):
            overlay.set_hotkeys(payload.get("global", {}))


    def _feed_hud_engine_info(self, *_) -> None:
        """Push image/area info + active-settings summary into the HUD context blocks."""
        overlay = getattr(self, "hud_overlay", None)
        service = getattr(self, "service", None)
        if overlay is None or service is None or not hasattr(overlay, "set_image_info"):
            return
        engine = getattr(service, "engine", None)
        if engine is None:
            return
        try:
            import os
            path = str(getattr(engine, "IMAGE_PATH", "") or "")
            name = os.path.basename(path) if (path and not path.startswith("<")) else (path or None)
            src = getattr(engine, "source_pil_image", None)
            w = h = None
            if src is not None:
                try:
                    w, h = src.size
                except Exception:
                    w = h = None
            palette = len(getattr(engine, "color_palette", []) or []) or None
            overlay.set_image_info(
                name=name, width=w, height=h, palette=palette,
                area=getattr(engine, "draw_region", None),
            )
        except Exception:
            log.debug("ignored exception feeding HUD image info", exc_info=True)
        try:
            overlay.set_settings_summary({
                "mode": getattr(engine, "mode", "color"),
                "brush": getattr(engine, "brush_size", None),
                "dither": bool(getattr(engine, "prep_dither_enabled", False)),
                "dynamic_brush": bool(getattr(engine, "dynamic_brush_enabled", False)),
                "background_removal": bool(getattr(engine, "background_removal_enabled", False)),
                "algorithm": getattr(engine, "drawing_algorithm", None),
            })
        except Exception:
            log.debug("ignored exception feeding HUD settings", exc_info=True)


    def _on_hud_capture(self, payload) -> None:
        overlay = getattr(self, "hud_overlay", None)
        if overlay is None:
            return
        # Make the capture status unmissable: auto-show the overlay when a
        # layer/palette/extra-actions capture begins.
        if isinstance(payload, dict) and payload.get("active") and not overlay.isVisible():
            overlay.show_overlay()
            self.hudVisibilityChanged.emit(self.hud_overlay.isVisible())
