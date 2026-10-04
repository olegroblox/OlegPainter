"""FloatingToolbar — компактная всплывающая панель управления.

Появляется при наведении курсора к верхнему краю окна и прячется, когда курсор
уходит. Дублирует команды из меню в системном трее. Все подписи — на русском.
"""

from __future__ import annotations

from PySide6.QtCore import Qt, QSize
from PySide6.QtWidgets import (
    QFrame, QHBoxLayout, QToolButton, QSlider, QLabel, QWidget,
)


_STYLE = """
FloatingToolbar {
    background: rgba(28, 30, 36, 220);
    border: 1px solid rgba(255, 255, 255, 30);
    border-radius: 10px;
}
QToolButton {
    color: #e8eaed;
    background: transparent;
    border: 1px solid transparent;
    border-radius: 6px;
    padding: 4px 9px;
    font-size: 12px;
}
QToolButton:hover {
    background: rgba(255, 255, 255, 26);
}
QToolButton:checked {
    background: rgba(60, 140, 240, 200);
    color: white;
}
QLabel {
    color: #b6bac2;
    font-size: 11px;
}
QFrame#sep {
    background: rgba(255, 255, 255, 36);
    max-width: 1px;
    min-width: 1px;
}
QSlider::groove:horizontal {
    height: 4px;
    background: rgba(255, 255, 255, 40);
    border-radius: 2px;
}
QSlider::handle:horizontal {
    width: 12px;
    margin: -5px 0;
    border-radius: 6px;
    background: #e8eaed;
}
QSlider::sub-page:horizontal {
    background: rgba(60, 140, 240, 220);
    border-radius: 2px;
}
"""


class FloatingToolbar(QFrame):
    def __init__(self, overlay) -> None:
        super().__init__(overlay)
        from ui.overlays.capture_registration import register_screen_tool
        register_screen_tool(self)
        self._overlay = overlay
        self.setObjectName("FloatingToolbar")
        self.setStyleSheet(_STYLE)
        self.setAttribute(Qt.WA_StyledBackground, True)
        # Отдельное верхнеуровневое окно: не обрезается границами окна-трафарета
        # и не перехватывает фокус у приложения под ним.
        self.setWindowFlags(
            Qt.FramelessWindowHint | Qt.Tool
            | Qt.WindowStaysOnTopHint | Qt.WindowDoesNotAcceptFocus
        )
        self.setAttribute(Qt.WA_TranslucentBackground, True)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        self._btn_open = self._button("Открыть", "Открыть изображение  (Ctrl+O)",
                                       overlay.open_image_dialog)
        self._btn_reset = self._button("Сброс", "Сбросить трансформацию и зум  (Ctrl+R)",
                                       overlay.reset_transform)
        self._btn_rotate = self._button("Повернуть", "Повернуть на 90°",
                                        lambda: overlay.rotate_image(90))
        layout.addWidget(self._btn_open)
        layout.addWidget(self._btn_reset)
        layout.addWidget(self._btn_rotate)
        layout.addWidget(self._separator())

        self._btn_top = self._button("Поверх", "Поверх всех окон  (Ctrl+T)",
                                     overlay.toggle_always_on_top, checkable=True)
        self._btn_click = self._button("Трафарет", "Сквозные клики — окно становится трафаретом\n"
                                       "(вернуть: Ctrl+Alt+X или меню в трее)",
                                       overlay.toggle_click_through, checkable=True)
        self._btn_border = self._button("Рамка", "Показывать рамку окна",
                                        overlay.toggle_show_border, checkable=True)
        layout.addWidget(self._btn_top)
        layout.addWidget(self._btn_click)
        layout.addWidget(self._btn_border)
        layout.addWidget(self._separator())

        layout.addWidget(QLabel("Окно"))
        self._sl_window = self._slider(15, 100, overlay.set_window_opacity_pct)
        layout.addWidget(self._sl_window)

        layout.addWidget(QLabel("Картинка"))
        self._sl_image = self._slider(0, 100, overlay.set_image_opacity_pct)
        layout.addWidget(self._sl_image)

        layout.addWidget(self._separator())
        self._btn_hide = self._button("✕", "Скрыть панель  (Ctrl+H)",
                                      overlay.hide_toolbar)
        layout.addWidget(self._btn_hide)

        self.adjustSize()

    # ------------------------------------------------------------------ #

    def _button(self, text, tip, slot, checkable=False) -> QToolButton:
        b = QToolButton(self)
        b.setText(text)
        b.setToolTip(tip)
        b.setCheckable(checkable)
        b.setCursor(Qt.PointingHandCursor)
        b.setFocusPolicy(Qt.NoFocus)
        b.clicked.connect(slot)
        return b

    def _slider(self, lo, hi, slot) -> QSlider:
        s = QSlider(Qt.Horizontal, self)
        s.setRange(lo, hi)
        s.setFixedWidth(62)
        s.setFocusPolicy(Qt.NoFocus)
        s.valueChanged.connect(slot)
        return s

    def _separator(self) -> QWidget:
        sep = QFrame(self)
        sep.setObjectName("sep")
        sep.setFrameShape(QFrame.VLine)
        sep.setFixedWidth(1)
        return sep

    # ------------------------------------------------------------------ #
    #   Синхронизация состояния (при изменении через трей/хоткей)
    # ------------------------------------------------------------------ #

    def sync(self, st) -> None:
        self._btn_top.setChecked(st.window.always_on_top)
        self._btn_click.setChecked(st.window.click_through)
        self._btn_border.setChecked(st.window.show_border)
        self._sl_window.blockSignals(True)
        self._sl_image.blockSignals(True)
        self._sl_window.setValue(int(round(st.window.opacity * 100)))
        self._sl_image.setValue(int(round(st.image.opacity * 100)))
        self._sl_window.blockSignals(False)
        self._sl_image.blockSignals(False)
