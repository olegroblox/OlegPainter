# -*- coding: utf-8 -*-
"""Shared base for the app's desktop overlay windows.

Both the drawing *stencil* (:class:`ui.widgets.top_overlay.TopOverlay`) and the
information *HUD* (:class:`ui.widgets.hud_overlay.HudOverlay`) are the same species
of window: a frameless, always-on-top, translucent top-level widget that floats over
the target paint application. By default they are fully *click-through* so they never
steal input from the target app — which the engine drives via the ``interception``
driver — and they flip to an interactive mode only while the user edits them.

This base owns that shared, safety-critical boilerplate (window flags, translucent
background and the click-through toggle) so the two overlays cannot drift apart.
Subclasses keep only their own content: the stencil paints the image registered onto
the draw region; the HUD lays out draggable info blocks.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QWidget
from ui.overlays.capture_registration import register_screen_tool

log = logging.getLogger("olegpainter.ui.widgets.overlay_base")


class ClickThroughOverlay(QWidget):
    """Frameless, always-on-top, translucent overlay that is click-through by default.

    Parameters
    ----------
    parent:
        Optional Qt parent.
    window_kind:
        Window-type flag combined with ``FramelessWindowHint`` and
        ``WindowStaysOnTopHint``. Use ``Qt.Tool`` for an auxiliary window that should
        stay out of the taskbar (the HUD) or ``Qt.Window`` for a primary top-level
        overlay (the stencil).
    translucent:
        When ``True`` (default) the background is fully translucent
        (``WA_TranslucentBackground``) so only painted content shows.
    passthrough:
        Initial click-through state. When ``True`` (default) all mouse/keyboard input
        passes through to whatever sits underneath.

    Notes
    -----
    The click-through toggle sets both ``WA_TransparentForMouseEvents`` (Qt level) and
    the ``WindowTransparentForInput`` window flag (OS level). Changing the latter forces
    Qt to recreate the native window, so the toggle hides/shows the widget to apply it
    while carefully preserving the current visibility.
    """

    def __init__(
        self,
        parent=None,
        *,
        window_kind=Qt.Tool,
        translucent: bool = True,
        passthrough: bool = True,
    ) -> None:
        super().__init__(parent, Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | window_kind)
        register_screen_tool(self)
        if translucent:
            self.setAttribute(Qt.WA_TranslucentBackground, True)
        self._passthrough = None
        self._apply_passthrough(bool(passthrough))

    # ----- click-through (locked) vs interactive (edit) ------------------- #
    def _apply_passthrough(self, enabled: bool) -> None:
        """Enable/disable full click-through for the whole window.

        While enabled, the overlay never intercepts input destined for the target
        application; disable it only while the user is actively interacting with the
        overlay (dragging the stencil area, moving HUD blocks, etc.).
        """
        enabled = bool(enabled)
        if enabled == self._passthrough:
            return
        self.setAttribute(Qt.WA_TransparentForMouseEvents, enabled)
        was_visible = self.isVisible()
        try:
            if was_visible:
                super().hide()
            self.setWindowFlag(Qt.WindowTransparentForInput, enabled)
            if was_visible:
                super().show()
        except Exception:
            log.debug("ignored exception toggling overlay passthrough", exc_info=True)
        finally:
            if not was_visible:
                super().hide()
        self._passthrough = enabled

    def set_passthrough(self, enabled: bool) -> None:
        """Public setter for the click-through state."""
        self._apply_passthrough(bool(enabled))

    def is_passthrough(self) -> bool:
        """Return ``True`` while the overlay is currently click-through."""
        return bool(self._passthrough)
