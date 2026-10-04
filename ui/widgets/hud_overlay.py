# -*- coding: utf-8 -*-
"""Click-through drawing HUD: one movable card, shared floating settings panel.

v3 follows the main window (Theme.qml): a Card.qml panel with the app logo,
the status as a tinted row like the active navigation item, metrics on
field-coloured tiles, a flat yellow progress bar, a per-colour strip and
keycap chips. The soft shadow is painted by the full-screen host. All
telemetry fields remain available through Appearance; opacity, scale and
explicit visibility are persisted exactly as in v2.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal, QRectF, QSize, QTimer
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPainter, QPen, QPixmap
from PySide6.QtWidgets import (
    QWidget,
    QFrame,
    QLabel,
    QVBoxLayout,
    QHBoxLayout,
    QGridLayout,
    QSlider,
    QCheckBox,
    QPushButton,
    QScrollArea,
)

from ui.i18n import tr, i18n
from ui.widgets.overlay_base import ClickThroughOverlay
from ui.overlays import theme
from ui.overlays.theme import ACCENT, TRACK, panel_styles
from ui.overlays.panels import FloatingPopover, GlassCard

_c = theme.color

log = logging.getLogger("olegpainter.ui.widgets.hud_overlay")


def _dash() -> str:
    return f"<span style='color:{_c('muted')}'>—</span>"


_DEFAULT_VISIBLE = {"status", "progress", "time", "colors"}

_BLOCK_ORDER = [
    "status", "progress", "colors", "layers", "palette", "hotkeys",
    "time", "speed", "image", "settings",
]

# i18n key for each block title.
_BLOCK_TITLE_KEY = {
    "status": "hud_title_status",
    "progress": "hud_title_progress",
    "colors": "hud_title_colors",
    "layers": "hud_title_layers",
    "palette": "hud_title_palette",
    "hotkeys": "hud_title_hotkeys",
    "time": "hud_title_time",
    "speed": "hud_title_speed",
    "image": "hud_title_image",
    "settings": "hud_title_settings",
}

_PANEL_RADIUS = 16


class _HudBar(QWidget):
    """Flat yellow progress bar on the slider track colour; green when complete."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._value = 0.0
        self._done = False
        self.setFixedHeight(8)
        self.setMinimumWidth(130)

    def set_value(self, percent, done: bool = False) -> None:
        try:
            value = max(0.0, min(100.0, float(percent)))
        except (ValueError, TypeError):
            value = 0.0
        complete = bool(done) or value >= 100.0
        if (value, complete) == (self._value, self._done):
            return
        self._value, self._done = value, complete
        self.update()

    def paintEvent(self, event):
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(0, 1, self.width(), self.height() - 2)
        rad = r.height() / 2
        p.setPen(Qt.NoPen)
        p.setBrush(TRACK)
        p.drawRoundedRect(r, rad, rad)
        fw = r.width() * self._value / 100.0
        if fw >= 1.0:
            p.setBrush(QColor(_c("ok")) if self._done else ACCENT)
            p.drawRoundedRect(QRectF(r.x(), r.y(), max(fw, r.height()), r.height()), rad, rad)
        p.end()


class _SegmentStrip(QWidget):
    """One segment per palette colour: finished, the one being drawn, still to go."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._state = (0, 0, None)
        self.setFixedHeight(6)
        self.setMinimumWidth(40)

    def set_segments(self, done: int, total: int, current) -> bool:
        state = (max(0, int(done)), max(0, int(total)), current)
        if state == self._state:
            return False
        self._state = state
        self.update()
        return True

    def paintEvent(self, event):
        done, total, current = self._state
        if total <= 0:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        p.setPen(Qt.NoPen)
        w, h = float(self.width()), float(self.height())
        finished = QColor(ACCENT)
        if total > 48:
            # Too many colours for readable segments: a continuous bar, same meaning.
            p.setBrush(TRACK)
            p.drawRoundedRect(QRectF(0, 0, w, h), h / 2, h / 2)
            p.setBrush(finished)
            p.drawRoundedRect(QRectF(0, 0, max(h, w * min(done, total) / total), h), h / 2, h / 2)
            p.end()
            return
        gap = 2.0 if w / total >= 6 else 1.0
        seg = max(1.0, (w - gap * (total - 1)) / total)
        for index in range(total):
            rect = QRectF(index * (seg + gap), 0, seg, h)
            if index < done:
                p.setBrush(finished)
            elif index == done and current:
                p.setBrush(QColor(current))
            else:
                p.setBrush(TRACK)
            p.drawRoundedRect(rect, min(2.0, seg / 2), min(2.0, seg / 2))
            if index == done and current:
                p.setBrush(Qt.NoBrush)
                p.setPen(QPen(QColor(255, 255, 255, 170), 1))
                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 2, 2)
                p.setPen(Qt.NoPen)
        p.end()


class _AppLogo(QLabel):
    """The same logo the main window shows in its title bar."""

    def __init__(self, size: int = 20, parent=None):
        super().__init__(parent)
        self.setFixedSize(size, size)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        ratio = self.devicePixelRatioF() or 1.0
        pixmap = QPixmap(theme.app_icon_path())
        if not pixmap.isNull():
            pixmap = pixmap.scaled(round(size * ratio), round(size * ratio),
                                   Qt.KeepAspectRatio, Qt.SmoothTransformation)
            pixmap.setDevicePixelRatio(ratio)
            self.setPixmap(pixmap)


class _KeyHints(QWidget):
    """Footer shortcuts drawn as keycaps: [F3] Start / Pause   [F4] Stop."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._items: list[tuple[str, str]] = []
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedHeight(24)

    def set_items(self, items) -> None:
        items = [(str(k), str(v)) for k, v in items]
        if items != self._items:
            self._items = items
            self.update()

    def text(self) -> str:
        return "   ".join(f"{k}  {v}" for k, v in self._items)

    def sizeHint(self):
        return QSize(120, 24)

    def minimumSizeHint(self):
        return QSize(40, 24)

    def paintEvent(self, event):
        if not self._items:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        key_font = QFont(theme.font_family())
        key_font.setPixelSize(10)
        label_font = QFont(theme.font_family())
        label_font.setPixelSize(12)
        x, height = 0.0, 20.0
        y = (self.height() - height) / 2
        for key, label in self._items:
            p.setFont(key_font)
            x += theme.draw_keycap(p, x, y, key, height) + 7
            p.setFont(label_font)
            metrics = p.fontMetrics()
            label = metrics.elidedText(label, Qt.ElideRight, max(0, int(self.width() - x)))
            advance = metrics.horizontalAdvance(label)
            p.setPen(QColor(_c("muted")))
            p.drawText(QRectF(x, y, advance + 2, height - 2), Qt.AlignVCenter | Qt.AlignLeft, label)
            x += advance + 16
            if x >= self.width():
                break
        p.end()


class HudBlock(QFrame):
    """A telemetry field inside the shared drawing card.

    Kinds: "status" is a tinted row with a state stripe (like the active navigation
    item), "hero" is bare (the big progress readout), "tile" sits on a field-coloured
    rounded plate."""

    contentChanged = Signal()

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self._key = key
        self._kind = "status" if key == "status" else ("hero" if key == "progress" else "tile")
        self._tone = QColor(_c("muted"))
        self.setObjectName("HudBlock")
        self._swatch_color = None

        lay = QVBoxLayout(self)
        margins = {"status": (16, 9, 12, 9), "hero": (0, 0, 0, 2), "tile": (12, 10, 12, 11)}[self._kind]
        lay.setContentsMargins(*margins)
        lay.setSpacing(4)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self._title = QLabel("", self)
        self._title.setObjectName("HudBlockTitle")
        header.addWidget(self._title)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(9)
        self._swatch = QFrame(self)
        self._swatch.setObjectName("HudSwatch")
        self._swatch.setFixedSize(18, 18)
        self._swatch.setVisible(False)
        self._value = QLabel("", self)
        self._value.setObjectName("HudBlockValue")
        self._value.setTextFormat(Qt.RichText)
        self._value.setWordWrap(True)
        row.addWidget(self._swatch, 0, Qt.AlignTop)
        row.addWidget(self._value, 1)

        self._bar = _HudBar(self)
        self._bar.setVisible(False)
        self._strip = _SegmentStrip(self)
        self._strip.setVisible(False)

        lay.addLayout(header)
        lay.addLayout(row)
        lay.addWidget(self._bar)
        lay.addWidget(self._strip)
        lay.addStretch(1)   # paired tiles share a row height; content stays on top
        self.setMinimumWidth(0)

    def set_progress(self, percent, done: bool = False) -> None:
        """Show/update the real progress bar (used by the 'progress' block)."""
        was_hidden = self._bar.isHidden()
        if percent is None:
            self._bar.setVisible(False)
        else:
            self._bar.setVisible(True)
            self._bar.set_value(percent, done)
        if was_hidden != self._bar.isHidden():
            self.contentChanged.emit()

    def set_segments(self, done: int, total: int, current=None) -> None:
        """Per-colour strip (used by the 'colors' block); hidden without a palette."""
        was_hidden = self._strip.isHidden()
        self._strip.setVisible(total > 0)
        self._strip.set_segments(done, total, current)
        if was_hidden != self._strip.isHidden():
            self.contentChanged.emit()

    def set_tone(self, color: QColor) -> None:
        if color != self._tone:
            self._tone = QColor(color)
            self.update()

    # ----- content --------------------------------------------------------- #
    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_value(self, html: str, swatch_color: str | None = None) -> None:
        if self._value.text() == html and self._swatch_color == swatch_color:
            return
        self._swatch_color = swatch_color
        self._value.setText(html)
        if swatch_color:
            self._swatch.setVisible(True)
            ring = "rgba(255,255,255,60)" if theme.is_dark() else "rgba(0,0,0,40)"
            self._swatch.setStyleSheet(
                f"#HudSwatch{{background:{swatch_color};border:2px solid {ring};border-radius:6px;}}"
            )
        else:
            self._swatch.setVisible(False)
        self.updateGeometry()
        self.contentChanged.emit()

    def set_scale(self, scale: float) -> None:
        tf = QFont(theme.font_family())
        tf.setPointSize(max(7, round(9 * scale)))
        self._title.setFont(tf)
        vf = self._value.font()
        vf.setPointSize(max(8, round(11 * scale)))
        self._value.setFont(vf)
        self.adjustSize()

    def set_highlight(self, on: bool) -> None:
        if bool(self.property("capturing")) == bool(on):
            return
        self.setProperty("capturing", bool(on))
        self.update()

    def paintEvent(self, event):
        capturing = bool(self.property("capturing"))
        if self._kind == "hero" and not capturing:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing, True)
        r = QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5)
        if self._kind == "status":
            tint = QColor(self._tone)
            tint.setAlpha(38 if theme.is_dark() else 40)
            p.setPen(Qt.NoPen)
            p.setBrush(tint)
            p.drawRoundedRect(r, 10, 10)
            p.setBrush(self._tone)
            p.drawRoundedRect(QRectF(r.left() + 6, r.top() + 9, 3, max(4.0, r.height() - 18)), 1.5, 1.5)
        elif self._kind == "tile":
            p.setPen(QPen(theme.BORDER, 1))
            p.setBrush(theme.rgba("tile"))
            p.drawRoundedRect(r, 10, 10)
        if capturing:
            p.setBrush(QColor(255, 210, 30, 38))
            p.setPen(QPen(QColor(255, 210, 30, 85), 1))
            p.drawRoundedRect(r, 10, 10)
        p.end()


class _HudPanel(GlassCard):
    moved = Signal()

    def __init__(self, parent):
        super().__init__(parent, radius=_PANEL_RADIUS)
        self.setObjectName("HudPanel")
        self.editing = False
        self._drag_offset = None

    def mousePressEvent(self, event):
        if self.editing and event.button() == Qt.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.pos()
            event.accept()
        else:
            super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._drag_offset is not None:
            pos = event.globalPosition().toPoint() - self._drag_offset
            area = self.parentWidget().rect()
            self.move(max(12, min(pos.x(), area.width() - self.width() - 12)),
                      max(12, min(pos.y(), area.height() - self.height() - 12)))
            event.accept()
        else:
            super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._drag_offset is not None:
            self._drag_offset = None
            self.moved.emit()
            event.accept()
        else:
            super().mouseReleaseEvent(event)

    def moveEvent(self, event):
        super().moveEvent(event)
        host = self.parentWidget()
        if host is not None:
            host.update()   # the soft shadow is painted by the host and must follow

    def resizeEvent(self, event):
        super().resizeEvent(event)
        host = self.parentWidget()
        if host is not None:
            host.update()


from ui.widgets.application_hud_state import ApplicationHudStateMixin


class HudOverlay(ApplicationHudStateMixin, ClickThroughOverlay):
    """The full-screen click-through HUD container."""

    editModeChanged = Signal(bool)
    editAreaRequested = Signal()          # draw-area section: open on-screen area editor

    _STATE_KEY = {
        "idle": ("hud_state_idle", "muted"),
        "stopped": ("hud_state_stopped", "muted"),
        "started": ("hud_state_running", "ok"),
        "running": ("hud_state_running", "ok"),
        "paused": ("hud_state_paused", "warn"),
        "completed": ("hud_state_completed", "info"),
        "failed": ("hud_state_failed", "hot"),
        "stopping": ("hud_state_stopping", "warn"),
    }

    def __init__(self, parent=None):
        super().__init__(parent, window_kind=Qt.Tool)
        self.setObjectName("HudOverlay")
        self.setFocusPolicy(Qt.NoFocus)

        self._edit_mode = False
        self._hidden_for_tool = False
        self._bg_alpha = 238
        self._scale = 1.0
        self._hotkeys_map: dict = {}
        self._statuses = {"state": "idle", "layers": (0, 0), "palette": (0, 0), "hex": False}
        self._capture = {"active": False, "kind": None, "count": 0, "max": 0, "slot": None}
        self._last_stats: dict = {}
        self._image_info: dict = {}
        self._settings_summary: dict = {}

        self._panel_position = (1.0, 0.04)
        self._panel = _HudPanel(self)
        self._panel.moved.connect(self._on_block_moved)
        panel_layout = QVBoxLayout(self._panel)
        panel_layout.setContentsMargins(18, 18, 18, 14)
        panel_layout.setSpacing(12)
        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(9)
        header.addWidget(_AppLogo(20, self._panel))
        self._panel_title = QLabel("", self._panel)
        self._panel_title.setObjectName("HudPanelTitle")
        self._panel_title.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        header.addWidget(self._panel_title, 1)
        panel_layout.addLayout(header)
        self._panel_scroll = QScrollArea(self._panel)
        self._panel_scroll.setFrameShape(QFrame.NoFrame)
        self._panel_scroll.setWidgetResizable(True)
        self._panel_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self._panel_body = QWidget()
        self._panel_body.setObjectName("HudPanelBody")
        self._panel_scroll.setWidget(self._panel_body)
        self._panel_grid = QGridLayout(self._panel_body)
        self._panel_grid.setContentsMargins(0, 0, 0, 0)
        self._panel_grid.setSpacing(10)
        self._panel_grid.setColumnStretch(0, 1)
        self._panel_grid.setColumnStretch(1, 1)
        panel_layout.addWidget(self._panel_scroll)
        self._panel_footer = _KeyHints(self._panel)
        panel_layout.addWidget(self._panel_footer)
        self._blocks: dict[str, HudBlock] = {}
        for key in _BLOCK_ORDER:
            block = HudBlock(key, self._panel_body)
            block.contentChanged.connect(self._queue_relayout)
            self._blocks[key] = block
            block.setVisible(key in _DEFAULT_VISIBLE)
        for key in ("status", "progress"):
            self._blocks[key]._title.hide()
        self._arrange_panel()

        self._build_toolbar()
        self._apply_styles()
        self._retranslate()

        self._geom_timer = QTimer(self)
        self._geom_timer.setSingleShot(True)
        self._geom_timer.timeout.connect(self._relayout)
        try:
            i18n.languageChanged.connect(self._retranslate)
        except Exception:
            log.debug("ignored exception connecting languageChanged", exc_info=True)
        theme.watch(self._theme_changed)

    # ----- window flags / click-through (inherited from ClickThroughOverlay) #
    def _cover_primary_screen(self) -> None:
        screen = QGuiApplication.primaryScreen()
        if screen is not None:
            self.setGeometry(screen.geometry())

    # ----- toolbar (edit mode) -------------------------------------------- #
    def _build_toolbar(self) -> None:
        self._toolbar = GlassCard(self, radius=16)
        self._toolbar.setObjectName("HudToolbar")
        self._toolbar.hide()
        row = QHBoxLayout(self._toolbar)
        row.setContentsMargins(14, 10, 10, 10)
        row.setSpacing(6)
        row.addWidget(_AppLogo(20, self._toolbar))
        row.addSpacing(2)
        self._tb_title = QLabel("", self._toolbar)
        self._tb_title.setObjectName("ScreenPanelTitle")
        row.addWidget(self._tb_title)
        row.addSpacing(12)
        self._appearance_btn = QPushButton("", self._toolbar)
        row.addWidget(self._appearance_btn)
        self._btn_area_edit = QPushButton("", self._toolbar)
        self._btn_area_edit.clicked.connect(self.editAreaRequested.emit)
        row.addWidget(self._btn_area_edit)
        self._btn_reset = QPushButton("", self._toolbar)
        self._btn_reset.clicked.connect(self.reset_layout)
        row.addWidget(self._btn_reset)
        self._btn_done = QPushButton("", self._toolbar)
        self._btn_done.setObjectName("HudToolbarPrimary")
        self._btn_done.clicked.connect(lambda: self.set_edit_mode(False))
        row.addWidget(self._btn_done)

        self._appearance_pop = FloatingPopover(self)
        self._appearance_btn.clicked.connect(lambda: self._appearance_pop.open_below(self._appearance_btn))
        body = self._appearance_pop.body
        self._appearance_title = QLabel()
        self._appearance_title.setObjectName("ScreenPanelTitle")
        body.addWidget(self._appearance_title)
        controls = QGridLayout()
        body.addLayout(controls)
        self._tb_lbl_opacity = QLabel()
        self._tb_lbl_bg = QLabel()
        self._tb_lbl_scale = QLabel()
        self._sld_opacity = QSlider(Qt.Horizontal)
        self._sld_bg = QSlider(Qt.Horizontal)
        self._sld_scale = QSlider(Qt.Horizontal)
        for index, (label, slider, lo, hi, value, callback) in enumerate((
            (self._tb_lbl_opacity, self._sld_opacity, 30, 100, 100, self._on_opacity),
            (self._tb_lbl_bg, self._sld_bg, 0, 255, self._bg_alpha, self._on_bg_alpha),
            (self._tb_lbl_scale, self._sld_scale, 70, 160, 100, self._on_scale),
        )):
            slider.setRange(lo, hi)
            slider.setValue(value)
            slider.setFixedWidth(160)
            slider.valueChanged.connect(callback)
            controls.addWidget(label, index, 0)
            controls.addWidget(slider, index, 1)
        self._fields_title = QLabel()
        self._fields_title.setObjectName("ScreenPanelTitle")
        body.addWidget(self._fields_title)
        fields = QGridLayout()
        fields.setSpacing(12)
        self._vis_checks = {}
        for index, key in enumerate(_BLOCK_ORDER):
            check = QCheckBox()
            check.setChecked(key in _DEFAULT_VISIBLE)
            check.toggled.connect(lambda on, k=key: self._on_block_visibility(k, on))
            self._vis_checks[key] = check
            fields.addWidget(check, index // 2, index % 2)
        body.addLayout(fields)
        self._tb_lbl_area_size = QLabel()
        body.addWidget(self._tb_lbl_area_size)
        # The stencil itself owns its appearance controls; there is no second opacity slider here.

    def _arrange_panel(self):
        while self._panel_grid.count():
            self._panel_grid.takeAt(0)
        row = 0
        for key in ("status", "progress"):
            self._panel_grid.addWidget(self._blocks[key], row, 0, 1, 2)
            row += 1
        self._panel_grid.addWidget(self._blocks["time"], row, 0)
        self._panel_grid.addWidget(self._blocks["colors"], row, 1)
        for key in ("speed", "layers", "palette", "image", "settings", "hotkeys"):
            row += 1
            self._panel_grid.addWidget(self._blocks[key], row, 0, 1, 2)

    def _queue_relayout(self):
        timer = getattr(self, "_geom_timer", None)
        if timer is not None and not timer.isActive():
            timer.start(0)

    # ----- styling --------------------------------------------------------- #
    def _apply_styles(self) -> None:
        self._panel.set_surface_alpha(self._bg_alpha)
        css = panel_styles("#HudToolbar", primary="HudToolbarPrimary", painted=True) + f"""
        #HudPanel {{ background: transparent; border: none; font-family: '{theme.font_family()}'; }}
        #HudPanel QScrollArea, #HudPanelBody, #HudBlock {{ background: transparent; border: none; }}
        #HudPanelTitle {{ color: {_c('text')}; font-size: 14px; font-weight: 700; }}
        #HudBlockTitle {{ color: {_c('muted')}; background: transparent; }}
        #HudBlockValue {{ color: {_c('text')}; background: transparent; }}
        #HudPanel QScrollBar:vertical {{ background: transparent; width: 5px; }}
        #HudPanel QScrollBar::handle:vertical {{ background: {_c('scroll')}; min-height: 24px; border-radius: 2px; }}
        #HudPanel QScrollBar::add-line:vertical, #HudPanel QScrollBar::sub-line:vertical {{ height: 0; }}
        """
        self.setStyleSheet(css)
        for block in self._blocks.values():
            block.set_scale(self._scale)
        self._relayout()

    def _theme_changed(self, *_) -> None:
        # Stylesheet colours and the rich-text values of every block are theme tokens.
        self._apply_styles()
        self._retranslate()

    # ----- i18n / retranslate --------------------------------------------- #
    def _retranslate(self, *_) -> None:
        ru = str(i18n.current_language() or "ru") == "ru"
        self._panel_title.setText("Рисование" if ru else "Drawing")
        self._tb_title.setText("Панель рисования" if ru else "Drawing panel")
        self._appearance_btn.setText("Вид" if ru else "Appearance")
        self._appearance_title.setText("Внешний вид" if ru else "Appearance")
        self._fields_title.setText("Показатели" if ru else "Visible fields")
        self._tb_lbl_opacity.setText("Непрозрачность" if ru else "Opacity")
        self._tb_lbl_bg.setText(tr("hud_tb_bg"))
        self._tb_lbl_scale.setText(tr("hud_tb_scale"))
        self._btn_reset.setText("Сбросить" if ru else "Reset")
        self._btn_done.setText(tr("hud_tb_done"))
        self._btn_area_edit.setText("Трафарет" if ru else "Stencil")
        for key, block in self._blocks.items():
            block.set_title(tr(_BLOCK_TITLE_KEY[key]))
        for key, cb in self._vis_checks.items():
            cb.setText(tr(_BLOCK_TITLE_KEY[key]).replace("&", "&&"))  # "&" is a mnemonic in buttons
        self._render_status()
        self.update_stats(self._last_stats)
        self._render_layers()
        self._render_palette()
        self._render_hotkeys()
        self._render_image()
        self._render_settings()
        if self._edit_mode:
            self._position_toolbar()

    # ----- public setters (fed by main_window from service signals) ------- #
    def update_area_size(self, rect) -> None:
        """Update the draw-area size/position readout in the toolbar."""
        lbl = getattr(self, "_tb_lbl_area_size", None)
        if lbl is None:
            return
        try:
            if rect is None:
                lbl.setText("—")
                return
            if hasattr(rect, "width"):
                x, y, w, h = rect.x(), rect.y(), rect.width(), rect.height()
            else:
                x, y, w, h = rect
            lbl.setText(f"{int(w)}×{int(h)} @ {int(x)},{int(y)}")
        except Exception:
            log.debug("ignored exception in update_area_size", exc_info=True)

    def set_drawing_state(self, state: str) -> None:
        self._statuses["state"] = str(state or "idle").lower()
        self._render_status()
        self._render_hotkeys()

    def update_stats(self, stats: dict) -> None:
        stats = stats if isinstance(stats, dict) else {}
        self._last_stats = stats
        percent = stats.get("percent")
        if percent is None:
            pct = "<span style='font-size:34px;font-weight:700'>—</span>"
        else:
            pct = (f"<span style='font-size:36px;font-weight:700'>{int(percent)}</span>"
                   f"<span style='font-size:18px;font-weight:600;color:{_c('muted')}'>&thinsp;%</span>")
        eta = self._eta_only(stats.get("eta_text")) if stats.get("eta_text") else ""
        finishing = stats.get("phase") == "finishing"
        finishing_html = ""
        if finishing:
            finishing_html = f"<span style='color:{_c('hot')};font-size:11px'>◔ {tr('hud_finishing')}</span>"
        done_flag = (not finishing) and percent is not None and int(percent) >= 100
        eta_html = (f"<span style='color:{_c('muted')};font-size:12px'>{eta}</span>"
                    if eta else "")
        side = "<br>".join(part for part in (eta_html, finishing_html) if part)
        self._blocks["progress"].set_value(
            "<table width='100%' cellspacing='0' cellpadding='0'><tr>"
            f"<td valign='bottom'>{pct}</td>"
            f"<td align='right' valign='bottom' style='padding-bottom:7px'>{side}</td>"
            "</tr></table>"
        )
        self._blocks["progress"].set_progress(percent, done_flag)
        self._render_time(stats)
        self._render_speed(stats)
        total = int(stats.get("colors_total") or 0)
        done = int(stats.get("colors_done") or 0)
        remaining = int(stats.get("colors_remaining") or max(0, total - done))
        cur = stats.get("current_color")
        if total > 0:
            colors_html = (
                f"<span style='font-size:18px;font-weight:700'>{done}</span>"
                f"<span style='font-size:13px;color:{_c('muted')}'> / {total}</span>"
                f"<br><span style='color:{_c('muted')};font-size:11px'>{tr('hud_colors_left')} {remaining}</span>"
            )
        else:
            colors_html = _dash()
        self._blocks["colors"].set_value(colors_html, swatch_color=cur if total > 0 else None)
        self._blocks["colors"].set_segments(done, total, cur if total > 0 else None)

    def set_layers(self, count: int, maximum: int | None = None) -> None:
        self._statuses["layers"] = (int(count or 0), int(maximum or 0))
        self._render_layers()
        self._render_hotkeys()

    def set_palette(self, count: int, maximum: int | None = None) -> None:
        self._statuses["palette"] = (int(count or 0), int(maximum or 0))
        self._render_palette()
        self._render_hotkeys()

    def set_hex_ready(self, ready: bool) -> None:
        self._statuses["hex"] = bool(ready)
        self._render_hotkeys()

    def set_hotkeys(self, global_map: dict) -> None:
        self._hotkeys_map = dict(global_map or {})
        self._render_hotkeys()
        self._render_status()

    def set_capture_state(self, payload) -> None:
        payload = payload if isinstance(payload, dict) else {}
        self._capture = {
            "active": bool(payload.get("active")),
            "kind": payload.get("kind"),
            "count": int(payload.get("count") or 0),
            "max": int(payload.get("max") or 0),
            "slot": payload.get("slot"),
        }
        active, kind = self._capture["active"], self._capture["kind"]
        self._blocks["layers"].set_highlight(active and kind == "layers")
        self._blocks["palette"].set_highlight(active and kind == "palette")
        self._blocks["colors"].set_highlight(active and kind == "hex")
        self._render_status()
        self._render_hotkeys()

    def _hk(self, code: str, fallback: str = "") -> str:
        return self._hotkeys_map.get(code, fallback) or fallback

    # ----- block renderers ------------------------------------------------- #
    def _render_status(self) -> None:
        self._render_status_text()
        self._blocks["status"].set_tone(self._status_tone())

    def _status_tone(self) -> QColor:
        """The state colour of the status row: its tint and stripe match its text."""
        if self._capture.get("active"):
            return QColor(ACCENT)
        state_name = self._statuses["state"]
        app_state = getattr(self, "_application_state", None)
        if app_state is not None and not app_state.drawing_busy and state_name in ("idle", "stopped"):
            return QColor(_c("ok") if app_state.can_start else _c("warn"))
        return QColor(_c(self._STATE_KEY.get(state_name, ("", "muted"))[1]))

    def _render_status_text(self) -> None:
        application_status = self._application_status_html()
        if application_status is not None:
            self._blocks["status"].set_value(application_status)
            return
        cap = self._capture
        if cap.get("active"):
            kind = cap.get("kind")
            n, mx = cap.get("count", 0), cap.get("max", 0)
            if kind == "layers":
                title = tr("hud_cap_layers")
                count_txt = f" {n}/{mx}" if mx else f" {n}"
                hint = f"{self._hk('define_app_layers', 'F8')} — {tr('hud_cap_finish')}"
            elif kind == "palette":
                title = tr("hud_cap_palette")
                count_txt = f" {n}"
                hint = f"{self._hk('define_manual_palette', 'F9')} — {tr('hud_cap_finish')}"
            elif kind == "hex":
                title = tr("hud_cap_hex")
                count_txt = ""
                hint = tr("hud_cap_hex_hint")
            elif kind == "extra":
                slot = cap.get("slot")
                key = "hud_cap_extra_pre" if slot == "pre" else ("hud_cap_extra_post" if slot == "post" else "hud_cap_extra")
                title = tr(key)
                count_txt = ""
                hint = f"{self._hk('record_pre_color_actions' if slot == 'pre' else 'record_post_color_actions', '')} — {tr('hud_cap_finish')}".strip(" —")
            elif kind == "brush":
                title = tr("hud_cap_brush")
                count_txt = ""
                hint = ""
            else:
                title, count_txt, hint = tr("hud_cap_extra"), "", ""
            body = f"<span style='font-size:13px;font-weight:500;color:{_c('accent_text')}'>◉ {title}…{count_txt}</span>"
            if hint:
                body += f"<br><span style='color:{_c('hint')}'>{hint}</span>"
            self._blocks["status"].set_value(body)
            return
        key, color = self._STATE_KEY.get(self._statuses["state"], ("hud_state_idle", "muted"))
        color = _c(color)
        self._blocks["status"].set_value(
            f"<span style='font-size:13px;font-weight:600;color:{color}'>● {tr(key)}</span>"
        )

    def _render_layers(self) -> None:
        n, mx = self._statuses["layers"]
        text = f"{n}/{mx}" if mx else (str(n) if n else "—")
        self._blocks["layers"].set_value(
            f"<span style='font-size:18px;font-weight:700'>{text}</span>"
            f"<br><span style='color:{_c('muted')};font-size:11px'>{tr('hud_layers_caption')}</span>"
        )

    def _render_palette(self) -> None:
        n, mx = self._statuses["palette"]
        text = f"{n}/{mx}" if mx else (str(n) if n else "—")
        self._blocks["palette"].set_value(
            f"<span style='font-size:18px;font-weight:700'>{text}</span>"
            f"<br><span style='color:{_c('muted')};font-size:11px'>{tr('hud_palette_caption')}</span>"
        )

    # ----- new context blocks: time / speed / image / settings ------------ #
    def set_image_info(self, name=None, width=None, height=None, palette=None, area=None) -> None:
        self._image_info = {"name": name, "w": width, "h": height, "palette": palette, "area": area}
        self._render_image()

    def set_settings_summary(self, payload) -> None:
        self._settings_summary = payload if isinstance(payload, dict) else {}
        self._render_settings()

    def _render_time(self, stats: dict) -> None:
        elapsed = stats.get("elapsed_seconds")
        remaining = stats.get("eta_seconds")
        if elapsed is None and remaining is None:
            self._blocks["time"].set_value(_dash())
            return
        self._blocks["time"].set_value(
            f"<span style='font-size:18px;font-weight:700'>{self._fmt_dur(elapsed)}</span>"
            f"<br><span style='color:{_c('muted')};font-size:11px'>{self._fmt_dur(remaining)} {tr('hud_time_left')}</span>"
        )

    def _render_speed(self, stats: dict) -> None:
        elapsed = stats.get("elapsed_seconds")
        try:
            elapsed = float(elapsed) if elapsed is not None else 0.0
        except Exception:
            elapsed = 0.0
        if elapsed <= 0.5:
            self._blocks["speed"].set_value(_dash())
            return
        done = float(stats.get("colors_done") or 0)
        percent = float(stats.get("percent") or 0)
        cpm = done / elapsed * 60.0
        ppm = percent / elapsed * 60.0
        self._blocks["speed"].set_value(
            f"<span style='font-size:18px;font-weight:700'>{cpm:.1f}</span>"
            f" <span style='color:{_c('muted')};font-size:11px'>{tr('hud_speed_cpm')}</span><br>"
            f"<span style='color:{_c('muted')};font-size:11px'>{ppm:.1f} {tr('hud_speed_ppm')}</span>"
        )

    def _render_image(self) -> None:
        info = self._image_info or {}
        name = info.get("name")
        if not name:
            self._blocks["image"].set_value(
                f"<span style='color:{_c('muted')}'>{tr('hud_img_none')}</span>"
            )
            return
        w, h = info.get("w"), info.get("h")
        dims = f"{int(w)}×{int(h)}" if w and h else "—"
        pal = info.get("palette")
        pal_txt = f" · {tr('hud_palette_size')} {int(pal)}" if pal else ""
        area = info.get("area")
        area_txt = ""
        if area and len(area) >= 4 and area[2] and area[3]:
            area_txt = (f"<br><span style='color:{_c('muted')}'>{tr('hud_area_caption')}: "
                        f"{int(area[2])}×{int(area[3])}</span>")
        self._blocks["image"].set_value(
            f"<span style='font-size:13px;font-weight:700'>{self._elide(name)}</span><br>"
            f"<span style='color:{_c('muted')}'>{dims}{pal_txt}</span>{area_txt}"
        )

    def _render_settings(self) -> None:
        s = self._settings_summary or {}
        if not s:
            self._blocks["settings"].set_value(_dash())
            return

        def onoff(v):
            col = _c("ok") if v else _c("off")
            return f"<span style='color:{col}'>{tr('hud_on') if v else tr('hud_off')}</span>"

        mode_txt = tr("hud_set_bw") if s.get("mode") == "bw" else tr("hud_set_color")
        rows = [
            f"{tr('hud_set_mode')}: <b>{mode_txt}</b> · {tr('hud_set_brush')} <b>{s.get('brush', '—')}</b>",
            f"{tr('hud_set_dither')} {onoff(s.get('dither'))} · {tr('hud_set_dyn_brush')} {onoff(s.get('dynamic_brush'))}",
            f"{tr('hud_set_bg')} {onoff(s.get('background_removal'))}",
        ]
        algo = s.get("algorithm")
        if algo:
            rows.append(f"<span style='color:{_c('muted')}'>{tr('hud_set_algo')}: {algo}</span>")
        self._blocks["settings"].set_value("<br>".join(rows))

    @staticmethod
    def _fmt_dur(seconds) -> str:
        try:
            s = int(round(float(seconds)))
        except Exception:
            return "--:--"
        if s < 0:
            s = 0
        h, rem = divmod(s, 3600)
        m, sec = divmod(rem, 60)
        return f"{h}:{m:02d}:{sec:02d}" if h else f"{m:02d}:{sec:02d}"

    @staticmethod
    def _elide(text, n: int = 24) -> str:
        text = str(text or "")
        return text if len(text) <= n else "…" + text[-(n - 1):]

    def _render_hotkeys(self) -> None:
        state = self._statuses["state"]
        layers_n, layers_mx = self._statuses["layers"]
        pal_n, _ = self._statuses["palette"]
        cap_kind = self._capture.get("kind") if self._capture.get("active") else None
        playing = state in ("started", "running")
        paused = state == "paused"

        sp_tag = tr("hud_st_drawing") if playing else (tr("hud_st_paused") if paused else "")
        rows = [
            (tr("hud_hk_start_pause"), self._hk("start_pause", "F3"), sp_tag, False),
            (tr("hud_hk_stop"), self._hk("stop", "F4"), "", False),
            (tr("hud_hk_area"), self._hk("select_area", "F1"), "", False),
            (tr("hud_hk_stencil"), self._hk("toggle_stencil", "F2"), "", False),
            (tr("hud_hk_hex"), self._hk("capture_hex_palette", "F5"),
                tr("hud_hex_ready") if self._statuses["hex"] else tr("hud_hex_unset"), cap_kind == "hex"),
            (tr("hud_hk_layers"), self._hk("define_app_layers", "F8"),
                (f"{layers_n}/{layers_mx}" if layers_mx else (str(layers_n) if layers_n else "")), cap_kind == "layers"),
            (tr("hud_hk_palette"), self._hk("define_manual_palette", "F9"),
                (str(pal_n) if pal_n else ""), cap_kind == "palette"),
            (tr("hud_hk_overlay"), self._hk("toggle_overlay", "Ctrl+Shift+P"), "", False),
        ]
        cap_bg, cap_fg = _c("keycap"), _c("keycap_text")
        cells = []
        for name, key, status, hot in rows:
            name_color = _c("hot") if hot else _c("text")
            status_html = ""
            if status:
                status_color = _c("hot") if hot else _c("ready")
                status_html = f" <span style='color:{status_color};font-size:11px'>· {status}</span>"
            cells.append(
                f"<tr><td style='padding:2px 0'><span style='color:{name_color}'>{name}</span>{status_html}</td>"
                f"<td align='right' style='padding:2px 0'><span style='background-color:{cap_bg};color:{cap_fg};"
                f"font-weight:600;font-size:11px'>&nbsp;{key}&nbsp;</span></td></tr>"
            )
        self._blocks["hotkeys"].set_value(
            "<table width='100%' cellspacing='0' cellpadding='0'>" + "".join(cells) + "</table>"
        )
        self._panel_footer.set_items([
            (self._hk("start_pause", "F3"), tr("hud_hk_start_pause")),
            (self._hk("stop", "F4"), tr("hud_hk_stop")),
        ])

    # ----- helpers --------------------------------------------------------- #
    @staticmethod
    def _eta_only(eta_text: str) -> str:
        return str(eta_text).split("  ")[0].strip() if eta_text else "~ --:--"

    # ----- edit mode ------------------------------------------------------- #
    def set_edit_mode(self, enabled: bool) -> None:
        enabled = bool(enabled)
        if enabled == self._edit_mode:
            if enabled and not self.isVisible():
                self.show_overlay()
            return
        self._edit_mode = enabled
        if enabled and not self.isVisible():
            self.show_overlay()
        self._panel.editing = enabled
        self._panel.setProperty("editing", enabled)
        self._panel.setCursor(Qt.OpenHandCursor if enabled else Qt.ArrowCursor)
        self._panel.set_focused(enabled)
        if not enabled:
            self._appearance_pop.hide()
        self._apply_passthrough(not enabled)
        self._toolbar.setVisible(enabled)
        if enabled:
            self._position_toolbar()
            self.raise_()
            self.activateWindow()
            self.setFocus(Qt.OtherFocusReason)
        self.update()
        self.editModeChanged.emit(enabled)

    def keyPressEvent(self, event):
        if self._edit_mode and event.key() in (Qt.Key_Escape, Qt.Key_Return, Qt.Key_Enter):
            self.set_edit_mode(False)
            event.accept()
            return
        super().keyPressEvent(event)

    def is_edit_mode(self) -> bool:
        return self._edit_mode

    def _on_opacity(self, value: int) -> None:
        self.setWindowOpacity(max(0.3, min(1.0, value / 100.0)))

    def _on_bg_alpha(self, value: int) -> None:
        self._bg_alpha = int(value)
        self._apply_styles()
        self.update()

    def _on_scale(self, value: int) -> None:
        self._scale = max(0.7, min(1.6, value / 100.0))
        for block in self._blocks.values():
            block.set_scale(self._scale)
        self._relayout()

    def _on_block_visibility(self, key: str, visible: bool) -> None:
        block = self._blocks.get(key)
        if block is not None:
            block.setVisible(bool(visible))
            self._queue_relayout()

    def _on_block_moved(self) -> None:
        self._panel_position = (
            (self._panel.x() - 12) / max(1, self.width() - self._panel.width() - 24),
            (self._panel.y() - 12) / max(1, self.height() - self._panel.height() - 24),
        )

    def reset_layout(self) -> None:
        self._panel_position = (1.0, 0.04)
        for key, check in self._vis_checks.items():
            check.setChecked(key in _DEFAULT_VISIBLE)
        self._relayout()

    def _relayout(self) -> None:
        if not hasattr(self, "_panel") or self.width() <= 0 or self.height() <= 0:
            return
        width = min(max(320, round(360 * self._scale)), max(1, self.width() - 24))
        self._panel.setFixedWidth(width)
        self._panel_grid.activate()
        content_height = self._panel_grid.sizeHint().height()
        self._panel_scroll.setFixedHeight(min(content_height, max(80, self.height() - 180)))
        self._panel.adjustSize()
        fx, fy = self._panel_position
        self._panel.move(12 + round(max(0, self.width() - self._panel.width() - 24) * max(0, min(1, fx))),
                         12 + round(max(0, self.height() - self._panel.height() - 24) * max(0, min(1, fy))))
        if self._edit_mode:
            self._position_toolbar()

    def _position_toolbar(self) -> None:
        self._toolbar.setMaximumWidth(max(1, min(900, self.width() - 24)))
        self._toolbar.adjustSize()
        x = max(0, (self.width() - self._toolbar.width()) // 2)
        y = max(0, self.height() - self._toolbar.height() - 48)
        self._toolbar.move(x, y)
        self._toolbar.raise_()
        self.update()   # the toolbar shadow is painted by this host

    def paintEvent(self, event):
        painter = QPainter(self)
        if self._edit_mode:
            painter.fillRect(self.rect(), QColor(0, 0, 0, 64))
        # Shadows scale with the chosen card opacity: a see-through card casts none.
        strength = (self._bg_alpha / 255.0) ** 2
        if self._panel.isVisible() and strength > 0.02:
            theme.draw_soft_shadow(painter, self._panel.geometry(), _PANEL_RADIUS, blur=24,
                                   offset_y=8, strength=strength)
        if self._toolbar.isVisible():
            theme.draw_soft_shadow(painter, self._toolbar.geometry(), 16, blur=22, offset_y=8)
        painter.end()
        super().paintEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._relayout()

    def showEvent(self, event):
        super().showEvent(event)
        self._cover_primary_screen()
        self._geom_timer.start(0)

    # ----- visibility ------------------------------------------------------ #
    def show_overlay(self) -> None:
        self._cover_primary_screen()
        self.show()
        self.raise_()
        self._relayout()

    def hide_overlay(self) -> None:
        if self._edit_mode:
            self.set_edit_mode(False)
        self.hide()

    def set_tool_hidden(self, hidden: bool) -> None:
        """Step aside while the user points at the target program: the panel sat
        over the game's size and colour fields exactly when they had to be clicked."""
        if hidden and self.isVisible() and not self._edit_mode:
            self._hidden_for_tool = True
            self.hide()
        elif not hidden and self._hidden_for_tool:
            self._hidden_for_tool = False
            self.show_overlay()

    def toggle_visible(self) -> None:
        self._hidden_for_tool = False
        if self.isVisible():
            self.hide_overlay()
        else:
            self.show_overlay()

    # ----- persistence ----------------------------------------------------- #
    def export_state(self) -> dict:
        return {
            "visible": self.isVisible() or self._hidden_for_tool,
            "bg_alpha": self._bg_alpha,
            "scale": self._scale,
            "opacity": round(self.windowOpacity(), 3),
            "layout_version": 2,
            "panel_position": list(self._panel_position),
            "hidden_blocks": [k for k, b in self._blocks.items() if b.isHidden()],
        }

    def restore_state(self, state) -> None:
        if not isinstance(state, dict):
            return
        try:
            self._bg_alpha = int(state.get("bg_alpha", self._bg_alpha))
            self._scale = max(0.7, min(1.6, float(state.get("scale", self._scale))))
            if state.get("layout_version") == 2:
                position = state.get("panel_position", [1.0, 0.04])
                if isinstance(position, (list, tuple)) and len(position) == 2:
                    self._panel_position = tuple(max(0, min(1, float(v))) for v in position)
                hidden = set(state.get("hidden_blocks", []))
            else:
                # v1 scattered blocks become a compact card, preserving explicitly hidden fields.
                hidden = (set(_BLOCK_ORDER) - _DEFAULT_VISIBLE) | set(state.get("hidden_blocks", []))
                position = (state.get("positions") or {}).get("status")
                if isinstance(position, (list, tuple)) and len(position) == 2:
                    self._panel_position = tuple(max(0, min(1, float(v))) for v in position)
            # A HUD with every information block hidden only shows shortcut hints.
            # Recover this empty saved layout; retain all individual visibility choices.
            if set(_BLOCK_ORDER).issubset(hidden):
                hidden = set(_BLOCK_ORDER) - _DEFAULT_VISIBLE
            for key, block in self._blocks.items():
                block.setVisible(key not in hidden)
            self._sld_bg.setValue(self._bg_alpha)
            self._sld_scale.setValue(int(round(self._scale * 100)))
            op = int(round(float(state.get("opacity", 1.0)) * 100))
            self._sld_opacity.setValue(max(30, min(100, op)))
            for key, cb in self._vis_checks.items():
                cb.setChecked(key not in hidden)
            self._apply_styles()
            if state.get("visible"):
                self.show_overlay()
        except Exception:
            log.debug("ignored exception restoring HUD state", exc_info=True)
