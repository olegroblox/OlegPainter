"""Mixin extracted from ui/services/painter_service.py.

Stencil/overlay sync: schedule/apply/flush, edit session, deferred queue.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, QRect, QThread, Signal, Slot, QTimer, Qt  # noqa: F401
from PySide6.QtGui import QImage, QPixmap, QGuiApplication  # noqa: F401
from PySide6.QtWidgets import QWidget  # noqa: F401
from PIL import Image  # noqa: F401
from pathlib import Path  # noqa: F401
from ui.services.image_utils import pil_to_qimage  # noqa: F401

from engine.olegpainter.viewport_state import (  # noqa: F401
    VIEWPORT_UNSET,
    ViewportState,
    normalize_area_rect,
    normalize_desktop_rect,
)
from ui.helpers.viewport_mapper import monitor_binding_for_rect, qrect_from_tuple  # noqa: F401
from ui.widgets.top_overlay import TopOverlay  # noqa: F401
from ui.widgets.unified_board_overlay import UnifiedBoardOverlay  # noqa: F401
from ui.i18n import tr  # noqa: F401

log = logging.getLogger("olegpainter.painter_service")

# Feature flags for the stencil overlay implementation.
#   USE_KALKA_OVERLAY  — the PureRef-style Kalka tracing overlay drives the draw
#       region + source: the user positions the image in a movable/resizable/croppable
#       window; the bot draws exactly what is visible through it. HUD stays separate.
#   USE_UNIFIED_OVERLAY — (only when Kalka is OFF) merge HUD + stencil into one
#       Graphics-View scene.
# When USE_KALKA_OVERLAY is True the unified overlay is off (HUD = legacy HudOverlay)
# and the engine ghost stays hidden — Kalka is the visual stencil. Flip both to revert.
USE_KALKA_OVERLAY = True
USE_UNIFIED_OVERLAY = False


class StencilSyncMixin:
    """Stencil/overlay sync: schedule/apply/flush, edit session, deferred queue."""

    def _sync_requested_stencil_state_from_engine(self) -> bool:
        try:
            self._stencil_enabled_requested = bool(getattr(self.engine, "stencil_enabled", False))
        except Exception:
            self._stencil_enabled_requested = False
        return self._stencil_enabled_requested

    def _set_overlay_visible_state(self, visible: bool) -> None:
        visible = bool(visible)
        changed = visible != self._stencil_overlay_visible
        self._stencil_overlay_visible = visible
        if changed:
            try:
                self.overlayVisibleChanged.emit(visible)
            except Exception:
                log.debug('ignored exception in self.overlayVisibleChanged.emit(visible)', exc_info=True)

    def _overlay_is_visible(self, overlay: TopOverlay | None = None) -> bool:
        overlay = overlay or self._overlay
        if overlay is None:
            return False
        try:
            return bool(overlay.isVisible())
        except Exception:
            return False

    def _overlay_is_editing(self, overlay: TopOverlay | None = None) -> bool:
        overlay = overlay or self._overlay
        if overlay is None:
            return False
        checker = getattr(overlay, "is_editing", None)
        if not callable(checker):
            return False
        try:
            return bool(checker())
        except Exception:
            return False

    def schedule_stencil_sync(
        self,
        reason: str,
        revision: int | None = None,
        *,
        force_hide: bool = False,
        image=None,
        draw_region=None,
        allow_if_disabled: bool = False,
    ) -> None:
        if not QThread.isMainThread():
            self._invoke_on_qt(
                lambda: self.schedule_stencil_sync(
                    reason,
                    revision,
                    force_hide=force_hide,
                    image=image,
                    draw_region=draw_region,
                    allow_if_disabled=allow_if_disabled,
                )
            )
            return
        if self._is_shutting_down:
            return
        revision_value = int(revision or self._preview_display_revision or self._render_revision or 0)
        self._pending_stencil_reason = str(reason or "unspecified")
        self._pending_stencil_revision = max(self._pending_stencil_revision, revision_value)
        self._pending_stencil_force_hide = bool(self._pending_stencil_force_hide or force_hide)
        self._pending_stencil_allow_disabled = bool(self._pending_stencil_allow_disabled or allow_if_disabled)
        copied = self._copy_qimage(image)
        if copied is not None:
            self._pending_stencil_qimage = copied
        rect = normalize_desktop_rect(draw_region)
        if rect is not None:
            self._pending_stencil_area = rect
        self._stencil_sync_timer.start(0)

    def _flush_stencil_sync(self) -> None:
        if self._is_shutting_down:
            return
        reason = self._pending_stencil_reason or "queued"
        revision = self._pending_stencil_revision
        force_hide = self._pending_stencil_force_hide
        allow_if_disabled = self._pending_stencil_allow_disabled
        qimg = self._pending_stencil_qimage
        draw_region = self._pending_stencil_area
        self._pending_stencil_reason = None
        self._pending_stencil_revision = 0
        self._pending_stencil_force_hide = False
        self._pending_stencil_allow_disabled = False
        self._pending_stencil_qimage = None
        self._pending_stencil_area = None
        self.apply_stencil_sync_on_qt(
            reason,
            revision,
            force_hide=force_hide,
            image=qimg,
            draw_region=draw_region,
            allow_if_disabled=allow_if_disabled,
        )

    def _stash_deferred_stencil_sync(
        self,
        reason: str,
        revision: int,
        *,
        force_hide: bool,
        image: QImage | None,
        draw_region: tuple[int, int, int, int] | None,
        allow_if_disabled: bool,
    ) -> None:
        self._stencil_refresh_deferred = True
        self._deferred_stencil_reason = str(reason or "deferred")
        self._deferred_stencil_revision = max(self._deferred_stencil_revision, int(revision or 0))
        self._deferred_stencil_force_hide = bool(self._deferred_stencil_force_hide or force_hide)
        self._deferred_stencil_allow_disabled = bool(self._deferred_stencil_allow_disabled or allow_if_disabled)
        if image is not None and not image.isNull():
            self._deferred_stencil_qimage = image.copy()
        if draw_region is not None:
            self._deferred_stencil_area = draw_region

    def _consume_deferred_stencil_sync(self) -> None:
        if not self._stencil_refresh_deferred:
            return
        reason = self._deferred_stencil_reason or "edit-finished"
        revision = self._deferred_stencil_revision or self._render_revision
        force_hide = self._deferred_stencil_force_hide
        allow_if_disabled = self._deferred_stencil_allow_disabled
        qimg = self._deferred_stencil_qimage
        draw_region = self._deferred_stencil_area
        self._stencil_refresh_deferred = False
        self._deferred_stencil_reason = None
        self._deferred_stencil_revision = 0
        self._deferred_stencil_force_hide = False
        self._deferred_stencil_allow_disabled = False
        self._deferred_stencil_qimage = None
        self._deferred_stencil_area = None
        self.schedule_stencil_sync(
            reason,
            revision,
            force_hide=force_hide,
            image=qimg,
            draw_region=draw_region,
            allow_if_disabled=allow_if_disabled,
        )

    def _hide_overlay_on_qt(self, *, teardown: bool = False, cancel_edit: bool = False) -> None:
        if not QThread.isMainThread():
            self._invoke_on_qt(lambda: self._hide_overlay_on_qt(teardown=teardown, cancel_edit=cancel_edit))
            return
        overlay = self._overlay
        if overlay is None:
            self._set_overlay_visible_state(False)
            return
        if self._overlay_is_editing(overlay):
            try:
                overlay.cancel_edit_session(emit_signal=cancel_edit)
            except Exception:
                log.debug('ignored exception in overlay.cancel_edit_session(emit_signal=cancel_edit)', exc_info=True)
            self._stencil_edit_active = False
        try:
            overlay.hide()
        except Exception:
            log.debug('ignored exception in overlay.hide()', exc_info=True)
        self._set_overlay_visible_state(False)
        if teardown:
            try:
                self._disconnect_overlay_edit_signals()
            except Exception:
                log.debug('ignored exception in self._disconnect_overlay_edit_signals()', exc_info=True)
            try:
                overlay.close()
            except Exception:
                log.debug('ignored exception in overlay.close()', exc_info=True)
            self._overlay = None
            self._stencil_edit_active = False

    def apply_stencil_sync_on_qt(
        self,
        reason: str,
        revision: int | None,
        *,
        force_hide: bool = False,
        image: QImage | None = None,
        draw_region=None,
        allow_if_disabled: bool = False,
    ) -> None:
        if not QThread.isMainThread():
            self._invoke_on_qt(
                lambda: self.apply_stencil_sync_on_qt(
                    reason,
                    revision,
                    force_hide=force_hide,
                    image=image,
                    draw_region=draw_region,
                    allow_if_disabled=allow_if_disabled,
                )
            )
            return
        if self._is_shutting_down:
            return

        enabled = bool(self._stencil_enabled_requested)
        state = self.get_viewport_state()
        rect = normalize_desktop_rect(draw_region) or state.draw_region_desktop_px
        revision_value = int(revision or self._preview_display_revision or self._render_revision or 0)
        qimg = self._copy_qimage(image) or self._copy_qimage(self._last_qimg)

        if self._overlay_is_editing() and not force_hide:
            self._stash_deferred_stencil_sync(
                reason,
                revision_value,
                force_hide=force_hide,
                image=qimg,
                draw_region=rect,
                allow_if_disabled=allow_if_disabled,
            )
            return

        if force_hide or rect is None or state.stale or (not enabled and not allow_if_disabled):
            self._hide_overlay_on_qt(teardown=False, cancel_edit=force_hide or not enabled or state.stale)
            if not enabled and not allow_if_disabled:
                self._stencil_dirty = False
            return

        if qimg is None or qimg.isNull():
            self._hide_overlay_on_qt(teardown=False, cancel_edit=False)
            self._stencil_dirty = True
            return

        overlay = self._ensure_overlay_widget()
        if overlay is None:
            return
        try:
            overlay.set_area(qrect_from_tuple(rect))
            overlay.set_image(qimg)
            overlay.show_overlay()
        except Exception as exc:
            self.logger.debug("apply_stencil_sync_on_qt failed (%s): %s", reason, exc)
            return
        self._set_overlay_visible_state(True)
        self._stencil_dirty = False
        self._stencil_revision_applied = max(self._stencil_revision_applied, revision_value)

    def _sync_overlay_area(self, state: ViewportState | None = None) -> None:
        if not QThread.isMainThread():
            self._invoke_on_qt(lambda: self._sync_overlay_area(state))
            return
        overlay = getattr(self, "_overlay", None)
        if overlay is None:
            return
        if getattr(self, "_live_area_dragging", False):
            return  # during a live drag the overlay shows its own rect — don't fight it
        state = state or self.get_viewport_state()
        rect = state.draw_region_desktop_px
        if rect is None or state.stale:
            if self._overlay_is_visible(overlay):
                self._hide_overlay_on_qt(teardown=False, cancel_edit=bool(state.stale))
            return
        try:
            overlay.set_area(qrect_from_tuple(rect))
        except Exception as exc:
            self.logger.debug("sync overlay area failed: %s", exc)

    def toggle_stencil(self):
        if USE_KALKA_OVERLAY:
            # Kalka is the visual stencil — toggle its window instead of the engine ghost.
            try:
                self.kalkaToggleRequested.emit()
            except Exception:
                log.debug("ignored exception emitting kalkaToggleRequested", exc_info=True)
            return True
        enabled = bool(self.engine.toggle_stencil())
        self._sync_requested_stencil_state_from_engine()
        revision = max(self._preview_display_revision, self._render_revision)
        qimg = self._current_overlay_qimage()
        if enabled and qimg is not None and not qimg.isNull():
            self.schedule_stencil_sync("toggle-stencil-on", revision, image=qimg)
        else:
            self.schedule_stencil_sync("toggle-stencil-off", revision, force_hide=True)
        return enabled

    def _default_draw_region(self, qimg) -> tuple[int, int, int, int] | None:
        """A sensible starting draw region when none is set yet: the image aspect
        scaled to ~55% of the target monitor and centred on it (desktop pixels)."""
        mon = None
        getter = getattr(self.engine, "_get_app_monitor_bbox", None)
        if callable(getter):
            try:
                bbox = getter()
                if bbox and len(bbox) == 4:
                    l, t, r, b = (int(v) for v in bbox)
                    if r > l and b > t:
                        mon = (l, t, r - l, b - t)
            except Exception:
                mon = None
        if mon is None:
            screen = QGuiApplication.primaryScreen()
            if screen is None:
                return None
            g = screen.geometry()
            mon = (g.x(), g.y(), g.width(), g.height())
        mx, my, mw, mh = mon
        iw = max(1, int(qimg.width()))
        ih = max(1, int(qimg.height()))
        scale = min((mw * 0.55) / iw, (mh * 0.55) / ih)
        if scale <= 0:
            scale = 1.0
        w = min(mw, max(40, int(round(iw * scale))))
        h = min(mh, max(40, int(round(ih * scale))))
        x = mx + (mw - w) // 2
        y = my + (mh - h) // 2
        return (int(x), int(y), int(w), int(h))

    def edit_stencil(self):
        if USE_KALKA_OVERLAY:
            # Alt+F2 in Kalka mode = show + make the glass interactive (no legacy editor).
            try:
                self.kalkaEditRequested.emit()
            except Exception:
                log.debug("ignored exception emitting kalkaEditRequested", exc_info=True)
            return True
        overlay = getattr(self, "_overlay", None)
        if self._stencil_edit_active and overlay and callable(getattr(overlay, "is_editing", None)):
            try:
                if overlay.is_editing():
                    if hasattr(overlay, "request_finish"):
                        overlay.request_finish(True)   # legacy TopOverlay
                    else:
                        overlay.set_edit_mode(False)   # unified overlay
                    return
            except Exception:
                log.debug('ignored exception toggling overlay edit off', exc_info=True)
        if self._should_bounce_toggle("stencil_edit"):
            return
        qimg = self._current_overlay_qimage()
        if qimg is None or qimg.isNull():
            # Nothing to position without a prepared image.
            self.statusChanged.emit(tr("status_stencil_edit_requires_image"))
            return
        draw_region = getattr(self.engine, "draw_region", None)
        if not draw_region or len(draw_region) != 4:
            # No area selected yet — start from a sensible default centred on the
            # target monitor so the user can place it directly (no pre-selection needed).
            draw_region = self._default_draw_region(qimg)
            if draw_region is None:
                self.statusChanged.emit(tr("status_stencil_edit_requires_data"))
                return
        overlay = self._ensure_overlay_widget()
        if overlay is None:
            self.logger.warning("Stencil overlay unavailable for edit mode.")
            return
        x, y, w, h = map(int, draw_region)
        if w <= 0 or h <= 0:
            self.statusChanged.emit(tr("status_stencil_edit_requires_data"))
            return
        overlay.set_area(QRect(x, y, w, h))
        overlay.set_image(qimg)
        overlay.show_overlay()
        if hasattr(overlay, "begin_edit_session"):
            pixmap = QPixmap.fromImage(qimg)
            area_size = (max(1, int(w)), max(1, int(h)))
            try:
                overlay.cancel_edit_session()
            except Exception:
                log.debug('ignored exception in overlay.cancel_edit_session()', exc_info=True)
            ok = bool(overlay.begin_edit_session(area_size=area_size, pixmap=pixmap, rect=None, stretch=True))
        else:
            overlay.set_edit_mode(True)   # unified overlay: live edit, no Apply
            ok = True
        if ok:
            self._stencil_edit_active = True
            self.statusChanged.emit(tr("status_stencil_edit_enabled"))
        else:
            self.statusChanged.emit(tr("status_stencil_edit_requires_data"))

    def _on_overlay_edit_applied(self, payload: dict | None):
        self._stencil_edit_active = False
        data = payload or {}
        state = self.apply_viewport_state(
            draw_region_desktop_px=data.get("area"),
            stretch_to_area=True,
            manual_rect_area_px=VIEWPORT_UNSET,
            reset_manual=True,
            emit=True,
        )
        self._sync_overlay_area(state)
        self.statusChanged.emit(tr("status_stencil_edit_disabled"))
        self._invalidate_visuals(self._INVALIDATE_AREA, state=state)
        self._consume_deferred_stencil_sync()

    def _on_overlay_edit_cancelled(self):
        if self._stencil_edit_active:
            self._stencil_edit_active = False
            self.statusChanged.emit(tr("status_stencil_edit_disabled"))
        self._consume_deferred_stencil_sync()

    def _on_crop_applied(self, vl, vt, vr, vb):
        """Compose the view-relative crop (0..1 of the currently shown image) with
        the engine's existing source crop, then re-prepare so drawing + stencil use
        the newly cropped source."""
        engine = self.engine
        try:
            cur_l, cur_t, cur_r, cur_b = engine.get_crop_norm()
        except Exception:
            cur_l, cur_t, cur_r, cur_b = (0.0, 0.0, 1.0, 1.0)
        span_w = cur_r - cur_l
        span_h = cur_b - cur_t
        new = (
            cur_l + float(vl) * span_w,
            cur_t + float(vt) * span_h,
            cur_l + float(vr) * span_w,
            cur_t + float(vb) * span_h,
        )
        try:
            engine.set_crop_norm(new, reprocess=False)
        except Exception:
            self.logger.debug("set_crop_norm failed", exc_info=True)
            return
        # Re-quantize OFF the Qt thread (worker) so the UI never freezes on crop.
        self._rebuild_preview_strict()

    def _on_crop_reset(self):
        try:
            self.engine.reset_crop(reprocess=False)
        except Exception:
            self.logger.debug("reset_crop failed", exc_info=True)
            return
        self._rebuild_preview_strict()

    def _on_flip_changed(self, horizontal, vertical):
        """Live source flip — applied off the Qt thread (worker rebuild), like crop."""
        try:
            self.engine.set_source_flip(
                horizontal=bool(horizontal), vertical=bool(vertical), reprocess=False
            )
        except Exception:
            self.logger.debug("set_source_flip failed", exc_info=True)
            return
        self._rebuild_preview_strict()

    def capture_from_kalka(self, kalka) -> None:
        """Variant A (geometry): read the Kalka stencil's geometry → engine ``draw_region``
        (where) + ``crop_norm`` (which part of the source), WITHOUT touching the engine's
        real source image or IMAGE_PATH. The engine then draws its own source, cropped and
        scaled into draw_region — exactly the part of the image visible through the glass,
        where it sits on screen. Re-prepare runs off the Qt thread (no UI freeze).

        Called on Kalka ``contentChanged`` (debounced / on edit-exit)."""
        if kalka is None:
            return
        # A queued editor/restore signal may arrive after drawing has started.
        # Do not change the geometry, mask or prepared data owned by its worker.
        draw_thread = self.engine.drawing_thread
        if (self._is_drawing or self._stop_pending or self.brush_learning_active
                or self._thread_is_running(self._worker_thread)
                or (draw_thread is not None and draw_thread.is_alive())):
            return
        try:
            geom = kalka.get_stencil_geometry()
        except Exception:
            self.logger.debug("kalka geometry failed", exc_info=True)
            return
        if not geom:
            return
        region, crop = geom
        try:
            x, y, w, h = (int(round(v)) for v in region)
            if w <= 0 or h <= 0:
                return
            # Diagnostic: log the window→draw_region mapping when it changes, so a
            # "draws in the wrong place" mismatch (logical↔physical / monitor offset)
            # is visible in the session log.
            new_region = (x, y, w, h)
            if new_region != getattr(self, "_last_kalka_region", None):
                self._last_kalka_region = new_region
                try:
                    g = kalka.geometry()
                    scr = kalka.screen()
                    self.logger.info(
                        "Kalka geometry: window(logical)=(%d,%d,%d,%d) screen=%s dpr=%.2f "
                        "-> draw_region(phys)=(%d,%d,%d,%d) crop=%s",
                        g.x(), g.y(), g.width(), g.height(),
                        scr.name() if scr else "?",
                        scr.devicePixelRatio() if scr else 1.0,
                        x, y, w, h, tuple(round(c, 3) for c in crop),
                    )
                except Exception:
                    self.logger.debug("kalka geometry log failed", exc_info=True)
            # crop_norm comes from geometry; flips are not produced (rotation disabled).
            flipped = bool(getattr(self.engine, "flip_horizontal", False)
                           or getattr(self.engine, "flip_vertical", False))
            changed = bool(self.engine.set_crop_norm(tuple(crop), reprocess=False)) or flipped
            self.engine.set_source_flip(horizontal=False, vertical=False, reprocess=False)
            changed = bool(self.engine.apply_viewport_state(draw_region_desktop_px=new_region)) or changed
            # Stencil geometry/crop (and zone-mask, captured below) changed → mark the session
            # dirty so it autosaves. capture_from_kalka talks to the engine DIRECTLY, bypassing
            # the service apply_viewport_state wrapper that emits sessionStateChanged. Without
            # this, a moved/resized stencil only reached disk via the force-flush on close —
            # the intermittent "трафарет/область не сохранились после закрытия" bug.
            try:
                self._emit_session_state_changed("kalka_geometry", sections=("painter",))
            except Exception:
                self.logger.debug("kalka dirty-mark failed", exc_info=True)
        except Exception:
            self.logger.debug("kalka apply-to-engine failed", exc_info=True)
            return
        # Custom freeform draw-zone painted in Alt+F2: restrict drawing to the shape
        # (None = whole rectangle). Applied before re-prepare so the preview already
        # reflects the zone.
        try:
            getter = getattr(kalka, "get_region_mask_array", None)
            mask = getter() if callable(getter) else None
            if hasattr(self.engine, "set_region_mask"):
                changed = bool(self.engine.set_region_mask(mask)) or changed
        except Exception:
            self.logger.debug("kalka region-mask apply failed", exc_info=True)
            changed = True
        # Every pause while editing already re-prepares; "Done" then repeats the same
        # geometry. Rebuild only when something changed or nothing is prepared yet.
        if not changed and getattr(self.engine, "cluster_map", None) is not None:
            return
        self._rebuild_preview_strict()

    def _on_area_live_changed(self, x, y, w, h):
        """Live draw-region update while the user drags/resizes the area — applied in
        real time so no explicit Apply step is needed. The ``_live_area_dragging`` flag
        suppresses the overlay-area sync for this one call so it does not fight the drag
        (the overlay already shows the dragged rect)."""
        self._live_area_dragging = True
        try:
            self.apply_viewport_state(
                draw_region_desktop_px=(int(x), int(y), int(w), int(h)), emit=True
            )
        except Exception:
            self.logger.debug("live area apply failed", exc_info=True)
        finally:
            self._live_area_dragging = False

    def _connect_overlay_signals(self, overlay) -> None:
        """Connect whatever edit/crop/flip signals the overlay exposes (guarded, so it
        works for both the legacy TopOverlay and the new UnifiedBoardOverlay)."""
        routes = (
            ("editApplied", self._on_overlay_edit_applied),
            ("editCancelled", self._on_overlay_edit_cancelled),
            ("editFinished", self._on_overlay_edit_cancelled),
            ("cropApplied", self._on_crop_applied),
            ("cropResetRequested", self._on_crop_reset),
            ("areaLiveChanged", self._on_area_live_changed),
            ("flipChanged", self._on_flip_changed),
        )
        for sig_name, slot in routes:
            sig = getattr(overlay, sig_name, None)
            if sig is None:
                continue
            try:
                sig.connect(slot)
            except Exception:
                log.debug("ignored exception connecting overlay.%s", sig_name, exc_info=True)
        try:
            overlay.destroyed.connect(
                lambda *_args, _overlay=overlay: (
                    setattr(self, "_overlay", None) if getattr(self, "_overlay", None) is _overlay else None,
                    self._set_overlay_visible_state(False),
                )
            )
        except Exception:
            log.debug("ignored exception connecting overlay.destroyed", exc_info=True)

    def adopt_overlay(self, overlay) -> None:
        """Use an externally-owned overlay (e.g. main_window's unified board) as the
        service's stencil overlay, wiring its signals. Lets ONE object serve both the
        stencil and the HUD."""
        if overlay is None or self._overlay is overlay:
            return
        self._overlay = overlay
        self._connect_overlay_signals(overlay)

    def _ensure_overlay_widget(self):
        if self._overlay is not None:
            return self._overlay
        # In Kalka mode the Kalka glass IS the only visual stencil; the legacy
        # TopOverlay / engine-ghost must never be CONSTRUCTED (two frameless always-on-top
        # overlays plus the preview-rebuild worker racing on the engine image buffers
        # caused a hard crash). _overlay is never set in production, so all ghost-sync /
        # edit paths see None and become no-ops.
        if USE_KALKA_OVERLAY:
            return None
        parent = self.parent() if isinstance(self.parent(), QWidget) else None
        if USE_UNIFIED_OVERLAY:
            overlay = UnifiedBoardOverlay(parent)
        else:
            overlay = TopOverlay(parent)
            try:
                overlay.setAttribute(Qt.WA_DeleteOnClose, True)
            except Exception:
                log.debug('ignored exception in overlay.setAttribute(Qt.WA_DeleteOnClose, True)', exc_info=True)
        self._connect_overlay_signals(overlay)
        self._overlay = overlay
        return overlay

    def _engine_show_stencil(self, pil_img, draw_region, blocking=True, allow_if_disabled: bool = False):
        del blocking
        if getattr(self, "_is_shutting_down", False):
            return
        self.schedule_stencil_sync(
            "engine-show",
            self._preview_active_revision or self._preview_display_revision or self._render_revision,
            image=pil_img,
            draw_region=draw_region,
            allow_if_disabled=allow_if_disabled,
        )

    def _disconnect_overlay_edit_signals(self):
        overlay = getattr(self, "_overlay", None)
        if not overlay:
            return
        for signal, slot in (
            (getattr(overlay, "editApplied", None), self._on_overlay_edit_applied),
            (getattr(overlay, "editCancelled", None), self._on_overlay_edit_cancelled),
            (getattr(overlay, "cropApplied", None), self._on_crop_applied),
            (getattr(overlay, "cropResetRequested", None), self._on_crop_reset),
            (getattr(overlay, "areaLiveChanged", None), self._on_area_live_changed),
        ):
            if signal is None:
                continue
            try:
                signal.disconnect(slot)
            except Exception:
                log.debug('ignored exception in signal.disconnect(slot)', exc_info=True)

    def _engine_hide_stencil(self, blocking=True):
        del blocking
        if self._skipped_overlay_timer is not None:
            try:
                self._skipped_overlay_timer.stop()
            except Exception:
                log.debug('ignored exception in self._skipped_overlay_timer.stop()', exc_info=True)
        self.schedule_stencil_sync(
            "engine-hide",
            self._preview_active_revision or self._preview_display_revision or self._render_revision,
            force_hide=True,
        )

    def _schedule_skipped_overlay_hide(self, delay_ms: int = 1600):
        if self._overlay is None:
            return
        if getattr(self.engine, "stencil_enabled", False):
            return
        timer = self._skipped_overlay_timer
        if timer is None:
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._hide_skipped_overlay_if_needed)
            self._skipped_overlay_timer = timer
        else:
            try:
                timer.stop()
            except Exception:
                log.debug('ignored exception in timer.stop()', exc_info=True)
        timer.start(max(200, int(delay_ms)))

    def _hide_skipped_overlay_if_needed(self) -> None:
        if self._sync_requested_stencil_state_from_engine():
            return
        self._engine_hide_stencil(blocking=False)

    def _current_overlay_qimage(self) -> QImage | None:
        qimg = self._last_qimg
        if qimg is not None and not qimg.isNull():
            return qimg.copy()
        pil_img = getattr(self.engine, "quantized_preview_image", None)
        if isinstance(pil_img, Image.Image):
            try:
                return pil_to_qimage(pil_img)
            except Exception:
                return None
        return None

    def _apply_area_from_overlay(self, rect) -> bool:
        previous = normalize_desktop_rect(getattr(self.engine, "draw_region", None))
        state = self.apply_viewport_state(draw_region_desktop_px=rect, emit=True)
        return previous != state.draw_region_desktop_px

    def _refresh_stencil_if_visible(self):
        if self._is_shutting_down:
            return
        if self._overlay_is_visible() or self._stencil_enabled_requested:
            self.schedule_stencil_sync(
                "compat-refresh",
                self._preview_display_revision or self._render_revision,
                image=self._last_qimg,
            )
