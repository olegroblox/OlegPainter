from __future__ import annotations

from PySide6.QtCore import QObject, Signal


class ThemeManager(QObject):
    """Centralizes the current UI theme and notifies listeners about updates."""

    themeChanged = Signal(str)

    def __init__(self) -> None:
        super().__init__()
        self._theme: str = "dark"

    def theme(self) -> str:
        return self._theme

    def set_theme(self, theme: str | None) -> None:
        normalized = (theme or "dark").strip().lower()
        if normalized not in ("dark", "light"):
            normalized = "dark"
        if normalized == self._theme:
            return
        self._theme = normalized
        self.themeChanged.emit(self._theme)


theme_manager = ThemeManager()
