"""Shared presentation of the authoritative application snapshot."""
from html import escape

from engine.olegpainter.drawing_events import DrawingPhase
from ui.i18n import tr
from ui.overlays.theme import color as theme_color


class ApplicationHudStateMixin:
    def set_application_state(self, state):
        if state == getattr(self, "_application_state", None):
            return
        self._application_state = state
        self._statuses["state"] = state.drawing.value
        self._capture = state.capture.to_payload()
        self._render_status()
        self._render_hotkeys()

    def _application_status_html(self):
        state = getattr(self, "_application_state", None)
        if state is None:
            return None
        if state.drawing_busy:
            key, token = self._STATE_KEY[state.drawing.value]
            return f"<span style='color:{theme_color(token)}'>● {tr(key)}</span>"
        if state.capture.active:
            return None  # detailed capture label/count in the existing renderer
        if state.desktop_mode:
            text = tr("prep_missing_finish_capture").lstrip("• ")
        elif state.drawing in (DrawingPhase.IDLE, DrawingPhase.STOPPED):
            if state.can_start:
                return f"<span style='color:{theme_color('ok')}'>● {tr('hud_state_idle')}</span>"
            text = state.preparation.next_requirement.lstrip("• ")
        else:
            return None
        return (f"<b>{tr('hud_state_preparing')}</b><br>"
                f"<span style='color:{theme_color('warn')}'>{escape(text)}</span>")
