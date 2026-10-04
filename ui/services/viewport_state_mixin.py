"""Mixin extracted from ui/services/painter_service.py.

Viewport state: get/apply/emit + invalidation of dependent visuals.
"""
from __future__ import annotations

import logging
import time  # noqa: F401

from PySide6.QtCore import QObject, QRect, QThread, Signal, Slot, QTimer  # noqa: F401

from engine.olegpainter.viewport_state import (  # noqa: F401
    VIEWPORT_UNSET,
    ViewportState,
    normalize_area_rect,
    normalize_desktop_rect,
)
from ui.helpers.viewport_mapper import monitor_binding_for_rect, qrect_from_tuple  # noqa: F401
from ui.services._sentinel import _UNSET  # noqa: F401
from ui.i18n import tr  # noqa: F401

log = logging.getLogger("olegpainter.painter_service")


class ViewportStateMixin:
    """Viewport state: get/apply/emit + invalidation of dependent visuals."""

    def get_viewport_state(self) -> ViewportState:
        draw_region = normalize_desktop_rect(getattr(self.engine, "draw_region", None))
        manual_rect = normalize_area_rect(self.get_manual_image_rect())
        monitor_id, monitor_rect = monitor_binding_for_rect(draw_region)
        return ViewportState(
            draw_region_desktop_px=draw_region,
            stretch_to_area=self.get_stretch_to_area(),
            manual_rect_area_px=manual_rect,
            target_monitor_id=monitor_id,
            target_monitor_rect_desktop_px=monitor_rect,
            stale=bool(draw_region and monitor_id is None),
        )

    def _emit_viewport_state(self, state: ViewportState | None = None) -> ViewportState:
        state = state or self.get_viewport_state()
        if state.draw_region_desktop_px is not None:
            self.areaChanged.emit(qrect_from_tuple(state.draw_region_desktop_px))
        payload = {
            "rect": state.manual_rect_area_px,
            "stretch": state.stretch_to_area,
            "draw_region": state.draw_region_desktop_px,
            "target_monitor_id": state.target_monitor_id,
            "target_monitor_rect": state.target_monitor_rect_desktop_px,
            "stale": state.stale,
        }
        try:
            self.manualPlacementChanged.emit(payload)
        except Exception:
            log.debug('ignored exception in self.manualPlacementChanged.emit(payload)', exc_info=True)
        try:
            self.viewportStateChanged.emit(state)
        except Exception:
            log.debug('ignored exception in self.viewportStateChanged.emit(state)', exc_info=True)
        return state

    def _invalidate_visuals(self, kind: str, *, strict: bool = False, state: ViewportState | None = None) -> None:
        state = state or self.get_viewport_state()
        if kind in (self._INVALIDATE_AREA, self._INVALIDATE_PLACEMENT):
            self._sync_overlay_area(state)
        if kind == self._INVALIDATE_PALETTE_ORDER:
            return
        self._mark_render_dirty(kind)
        if strict:
            self._rebuild_preview_strict()
        else:
            self._rebuild_preview_if_possible()

    def apply_viewport_state(
        self,
        *,
        draw_region_desktop_px=VIEWPORT_UNSET,
        stretch_to_area=VIEWPORT_UNSET,
        manual_rect_area_px=VIEWPORT_UNSET,
        reset_manual: bool = False,
        invalidate: str | None = None,
        strict: bool = False,
        emit: bool = True,
    ) -> ViewportState:
        changed = False
        setter = getattr(self.engine, "apply_viewport_state", None)
        if callable(setter):
            try:
                normalized_stretch = True
                normalized_manual_rect = VIEWPORT_UNSET
                changed = bool(
                    setter(
                        draw_region_desktop_px=draw_region_desktop_px,
                        stretch_to_area=normalized_stretch,
                        manual_rect_area_px=normalized_manual_rect,
                        reset_manual=True,
                    )
                )
            except Exception as exc:
                self.logger.warning("apply_viewport_state failed: %s", exc)
        state = self.get_viewport_state()
        if emit:
            state = self._emit_viewport_state(state)
        if changed and invalidate:
            self._invalidate_visuals(invalidate, strict=strict, state=state)
        if changed:
            self._emit_session_state_changed("viewport", sections=("painter",))
        return state
