"""Shared visual language for stencil, HUD and screen calibration tools.

The tokens mirror the main window theme (ui/quick/qml/Theme.qml) one to one,
so screen tools look like parts of the same application: flat surfaces, thin
hairline borders, 16 px cards, 10 px controls, flat brand-yellow primary
actions, muted keycaps. Dark or light switches at runtime through
theme_manager; the shared QColor objects below are updated in place, so
painters that imported them see the new theme on their next paint.
Stylesheet users rebuild their CSS in a `watch` slot.

Screen marks (crosshair, picked points, region frames) stay the bright brand
yellow in both themes: they sit over arbitrary foreign content, not over our
own surfaces.
"""
from app_paths import get_app_paths
from PySide6.QtCore import QPoint, QPointF, QRect, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QImage, QPainter, QPen

from ui.helpers.theme_manager import theme_manager

# Names follow Theme.qml: card = Theme.sidebar (cards and panels), control =
# Theme.surface (buttons), raised = hover, input = pressed/fields.
_PALETTES = {
    "dark": {
        "surface": "#212121", "surface_rgb": (33, 33, 33),
        "border": "rgba(255,255,255,18)", "border_color": (255, 255, 255, 18),
        "border_hover": "rgba(255,255,255,36)",
        "text": "#e6e6e6", "muted": "#9c9c9c", "accent_text": "#FFD21E",
        "control": "#2e2e2e", "hover": "#3a3a3a", "checked": "#282828",
        "accent_soft": "rgba(255,210,30,38)", "accent_border": "rgba(255,210,30,85)",
        "groove": "#3a3a3a", "knob": "#e6e6e6", "knob_border": "transparent",
        "check_border": "rgba(255,255,255,36)",
        "scroll": "#4a4a4a", "track": (58, 58, 58, 255),
        "shadow": (0, 0, 0, 140), "icons": "dark",
        # HUD status colours: readable on the panel surface of this theme.
        "ok": "#5fd07a", "warn": "#e7b94a", "info": "#5fa8ff", "hot": "#ff8a4c",
        "off": "#7a8295", "hint": "#ffd9b0", "ready": "#7fd6a0",
        "tile": (40, 40, 40, 255), "keycap": "#2e2e2e", "keycap_text": "#9c9c9c",
    },
    "light": {
        "surface": "#fbfbfa", "surface_rgb": (251, 251, 250),
        "border": "rgba(0,0,0,23)", "border_color": (0, 0, 0, 23),
        "border_hover": "rgba(0,0,0,46)",
        "text": "#1a1a1a", "muted": "#666666", "accent_text": "#9a7700",
        "control": "#ffffff", "hover": "#e6e6e3", "checked": "#efefed",
        "accent_soft": "rgba(245,196,0,51)", "accent_border": "rgba(217,173,0,128)",
        "groove": "#e6e6e3", "knob": "#ffffff", "knob_border": "rgba(0,0,0,51)",
        "check_border": "rgba(0,0,0,46)",
        "scroll": "#c4c4c0", "track": (230, 230, 227, 255),
        "shadow": (0, 0, 0, 55), "icons": "light",
        "ok": "#1f9d4c", "warn": "#a87a00", "info": "#2f6fd0", "hot": "#c85a1e",
        "off": "#8a90a0", "hint": "#a0602a", "ready": "#1f9d4c",
        "tile": (239, 239, 237, 255), "keycap": "#e3e3e0", "keycap_text": "#666666",
    },
}

ACCENT_HEX = "#FFD21E"
ACCENT = QColor(ACCENT_HEX)
ACCENT_SOFT = QColor(255, 210, 30, 75)
ACCENT_INK = "#201900"
DIM = QColor(0, 0, 0, 95)
SURFACE = QColor()
TEXT = QColor()
MUTED = QColor()
ACCENT_TEXT = QColor()
BORDER = QColor()
TRACK = QColor()

_current = {"name": "dark"}


def name() -> str:
    return _current["name"]


def is_dark() -> bool:
    return _current["name"] == "dark"


def color(key: str) -> str:
    """One theme token as a CSS colour string (for stylesheets and rich text)."""
    return _PALETTES[_current["name"]][key]


def rgba(key: str) -> QColor:
    """One painted-surface token as a QColor (tuples and hex strings)."""
    value = _PALETTES[_current["name"]][key]
    return QColor(*value) if isinstance(value, tuple) else QColor(value)


def icon_theme() -> str:
    """Tint name for icon_tinter: light glyphs on dark panels and vice versa."""
    return _PALETTES[_current["name"]]["icons"]


def shadow_color() -> QColor:
    return QColor(*_PALETTES[_current["name"]]["shadow"])


def font_family() -> str:
    """The application face (Segoe UI Variable when present), same as the QML shell."""
    app = QGuiApplication.instance()
    family = app.font().family() if app is not None else ""
    return family or "Segoe UI"


def app_icon_path() -> str:
    """The same logo the main window shows in its title bar."""
    return str(get_app_paths().assets_root / "app-icon.png")


def _apply(theme_name) -> None:
    key = theme_name if theme_name in _PALETTES else "dark"
    _current["name"] = key
    palette = _PALETTES[key]
    SURFACE.setRgb(*palette["surface_rgb"], 246)
    TEXT.setRgba(QColor(palette["text"]).rgba())
    MUTED.setRgba(QColor(palette["muted"]).rgba())
    ACCENT_TEXT.setRgba(QColor(palette["accent_text"]).rgba())
    BORDER.setRgb(*palette["border_color"])
    TRACK.setRgb(*palette["track"])


def _on_theme_changed(theme_name) -> None:
    _apply(theme_name)
    # Painted surfaces read the shared colours in paintEvent; stylesheet users
    # restyle through their own `watch` slots (connected after this one).
    try:
        from PySide6.QtWidgets import QApplication
        if isinstance(QApplication.instance(), QApplication):
            for widget in QApplication.topLevelWidgets():
                if widget.isVisible():
                    widget.update()
    except Exception:  # pragma: no cover - repaint is best effort, colours are already set
        pass


def watch(callback) -> None:
    """Call `callback()` after every theme switch (bound methods of QObjects
    disconnect automatically when their owner is destroyed)."""
    theme_manager.themeChanged.connect(lambda *_: callback()) if not hasattr(callback, "__self__") \
        else theme_manager.themeChanged.connect(callback)


_apply(theme_manager.theme())
theme_manager.themeChanged.connect(_on_theme_changed)


_shadow_cache: dict = {}


def _shadow_image(width: int, height: int, radius: float, blur: int, rgba_value: int) -> QImage:
    """A blurred rounded rectangle. Downscale-then-upscale is a cheap, dependency-free
    Gaussian approximation; the result is cached per size, so repaints only blit it."""
    key = (width, height, round(radius), blur, rgba_value)
    cached = _shadow_cache.get(key)
    if cached is not None:
        return cached
    if len(_shadow_cache) > 48:
        _shadow_cache.clear()
    full_w, full_h = width + blur * 2, height + blur * 2
    image = QImage(full_w, full_h, QImage.Format_ARGB32_Premultiplied)
    image.fill(Qt.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(Qt.NoPen)
    painter.setBrush(QColor.fromRgba(rgba_value))
    painter.drawRoundedRect(QRectF(blur, blur, width, height), radius, radius)
    painter.end()
    factor = max(2, blur // 3)
    for _ in range(2):
        small = image.scaled(max(1, full_w // factor), max(1, full_h // factor),
                             Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
        image = small.scaled(full_w, full_h, Qt.IgnoreAspectRatio, Qt.SmoothTransformation)
    _shadow_cache[key] = image
    return image


def draw_soft_shadow(painter: QPainter, rect, radius: float = 16, blur: int = 22,
                     offset_y: int = 8, strength: float = 1.0) -> None:
    """The floating-window shadow Windows gives the main window, for our panels."""
    r = QRectF(rect).toAlignedRect()
    if r.width() <= 0 or r.height() <= 0:
        return
    color = shadow_color()
    color.setAlpha(max(0, min(255, round(color.alpha() * strength))))
    image = _shadow_image(r.width(), r.height(), radius, blur, color.rgba())
    painter.drawImage(QPointF(r.left() - blur, r.top() - blur + offset_y), image)


def paint_card(painter: QPainter, rect, radius: float = 16, *, alpha: int = 246,
               focused: bool = False) -> None:
    """A Card.qml surface: flat panel colour and a hairline border (accent while
    the card itself is being edited, like a focused control)."""
    r = QRectF(rect).adjusted(0.5, 0.5, -0.5, -0.5)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    fill = QColor(SURFACE)
    fill.setAlpha(max(0, min(255, int(alpha))))
    painter.setBrush(fill)
    painter.setPen(QPen(ACCENT, 1.5) if focused else QPen(BORDER, 1))
    painter.drawRoundedRect(r, radius, radius)
    painter.restore()


def keycap_width(metrics, text: str) -> int:
    return metrics.horizontalAdvance(text) + 12


def draw_keycap(painter: QPainter, x: float, y: float, text: str, height: float = 20) -> float:
    """The keycap chip of the main window's buttons and menus. Returns its width."""
    width = keycap_width(painter.fontMetrics(), text)
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QPen(BORDER, 1))
    painter.setBrush(rgba("keycap"))
    painter.drawRoundedRect(QRectF(x + 0.5, y + 0.5, width - 1, height - 1), 4, 4)
    painter.setPen(rgba("keycap_text"))
    painter.drawText(QRectF(x, y, width, height), Qt.AlignCenter, text)
    painter.restore()
    return width


def draw_chip(painter: QPainter, center_x: float, top: float, text: str, bounds: QRect | None = None) -> QRectF:
    """A small flat accent pill with dark ink for live sizes ("640 × 440 px")."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    font = QFont(font_family(), 9)
    font.setWeight(QFont.Weight.DemiBold)
    painter.setFont(font)
    metrics = painter.fontMetrics()
    width, height = metrics.horizontalAdvance(text) + 18, metrics.height() + 6
    x = center_x - width / 2
    if bounds is not None:
        x = max(bounds.left() + 4, min(x, bounds.right() - width - 4))
        top = max(bounds.top() + 4, min(top, bounds.bottom() - height - 4))
    rect = QRectF(x, top, width, height)
    draw_soft_shadow(painter, rect, height / 2, blur=8, offset_y=2, strength=0.7)
    painter.setPen(Qt.NoPen)
    painter.setBrush(ACCENT)
    painter.drawRoundedRect(rect, height / 2, height / 2)
    painter.setPen(QColor(ACCENT_INK))
    painter.drawText(rect, Qt.AlignCenter, text)
    painter.restore()
    return rect


def _fit_lines(metrics, text: str, available: int) -> list[str]:
    """Hints are "part  ·  part  ·  part": when the line does not fit, each part
    goes on its own line instead of being cut mid-word. Lines still too wide are
    elided as the last resort."""
    available = max(1, available)
    lines = [text]
    if metrics.horizontalAdvance(text) > available and "·" in text:
        lines = [part.strip() for part in text.split("·") if part.strip()]
    return [metrics.elidedText(line, Qt.ElideRight, available) for line in lines]


def draw_label(painter: QPainter, bounds: QRect, anchor: QPoint, text: str) -> QRect:
    """A coordinate/hint chip kept inside its display even at screen edges."""
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setFont(QFont(font_family(), 10))
    metrics = painter.fontMetrics()
    lines = _fit_lines(metrics, text, bounds.width() - 40)
    width = max(1, min(max(metrics.horizontalAdvance(line) for line in lines) + 28,
                       bounds.width() - 16))
    height = metrics.height() * len(lines) + 16
    x = max(bounds.left() + 8, min(anchor.x(), bounds.right() - width - 8))
    y = max(bounds.top() + 8, min(anchor.y(), bounds.bottom() - height - 8))
    rect = QRect(x, y, width, height)
    draw_soft_shadow(painter, rect, 10, blur=12, offset_y=4, strength=0.8)
    paint_card(painter, rect, 10)
    painter.setPen(TEXT)
    painter.drawText(rect.adjusted(14, 8, -14, -8), Qt.AlignVCenter | Qt.AlignLeft, "\n".join(lines))
    painter.restore()
    return rect


def draw_banner(painter: QPainter, bounds: QRect, title: str, detail: str = "") -> QRect:
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    title_font = QFont(font_family(), 11, QFont.Weight.DemiBold)
    detail_font = QFont(font_family(), 9)
    painter.setFont(detail_font)
    detail_metrics = painter.fontMetrics()
    painter.setFont(title_font)
    metrics = painter.fontMetrics()
    available = max(1, bounds.width() - 72)
    detail_lines = _fit_lines(detail_metrics, detail, available) if detail else []
    content = max([metrics.horizontalAdvance(title)]
                  + [detail_metrics.horizontalAdvance(line) for line in detail_lines])
    width = min(content + 48, max(1, bounds.width() - 32))
    detail_height = detail_metrics.height() * len(detail_lines)
    height = 44 + (detail_height + 10 if detail_lines else 0)
    rect = QRect(bounds.left() + (bounds.width() - width) // 2, bounds.top() + 24, width, height)
    draw_soft_shadow(painter, rect, 16, blur=24, offset_y=8)
    paint_card(painter, rect, 16)
    painter.setPen(TEXT)
    painter.drawText(QRect(rect.left() + 20, rect.top() + 8, width - 40, 28), Qt.AlignCenter,
                     metrics.elidedText(title, Qt.ElideRight, max(1, width - 40)))
    if detail_lines:
        painter.setFont(detail_font)
        painter.setPen(MUTED)
        painter.drawText(QRect(rect.left() + 20, rect.top() + 36, width - 40, detail_height),
                         Qt.AlignCenter, "\n".join(detail_lines))
    painter.restore()
    return rect


def draw_crosshair(painter: QPainter, bounds: QRect, point: QPoint) -> None:
    painter.save()
    painter.setRenderHint(QPainter.Antialiasing, True)
    painter.setPen(QPen(ACCENT_SOFT, 1))
    painter.drawLine(bounds.left(), point.y(), bounds.right(), point.y())
    painter.drawLine(point.x(), bounds.top(), point.x(), bounds.bottom())
    painter.setBrush(Qt.NoBrush)
    painter.setPen(QPen(QColor(0, 0, 0, 110), 4))
    painter.drawEllipse(point, 9, 9)
    painter.setPen(QPen(ACCENT, 2))
    painter.drawEllipse(point, 9, 9)
    painter.restore()


def panel_styles(selector: str, *, primary: str = "doneBtn", painted: bool = False) -> str:
    """ActionButton/Card look of the main window for floating toolbars and popovers.

    `painted`: the container is a GlassCard that paints its own surface, so the
    stylesheet leaves its background alone."""
    c = _PALETTES[_current["name"]]
    check_icon = (get_app_paths().assets_root / "icons" / "check-ink.svg").as_posix()
    family = font_family()
    container = ("background: transparent; border: none;" if painted else
                 f"background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 16px;")
    return f"""
    {selector} {{ {container} }}
    {selector} QLabel {{ color: {c['muted']}; font-family: '{family}'; font-size: 12px; background: transparent; }}
    {selector} QToolButton, {selector} QPushButton {{ color: {c['text']}; background: {c['control']};
        font-family: '{family}'; border: 1px solid {c['border']}; border-radius: 10px; padding: 7px 13px;
        font-size: 13px; font-weight: 500; }}
    {selector} QToolButton:hover, {selector} QPushButton:hover {{ background: {c['hover']}; border-color: {c['border_hover']}; }}
    {selector} QToolButton:pressed, {selector} QPushButton:pressed {{ background: {c['checked']}; }}
    {selector} QToolButton:checked {{ background: {c['accent_soft']}; color: {c['accent_text']};
        border: 1px solid {c['accent_border']}; }}
    {selector} QPushButton#{primary} {{ background: {ACCENT_HEX}; color: {ACCENT_INK}; font-weight: 600;
        border: 1px solid transparent; }}
    {selector} QPushButton#{primary}:hover {{ background: #FFDF55; }}
    {selector} QPushButton#{primary}:pressed {{ background: #E3BB14; }}
    {selector} QLabel#ScreenPanelTitle {{ color: {c['text']}; font-size: 14px; font-weight: 600; }}
    {selector} QCheckBox {{ color: {c['text']}; spacing: 9px; font-size: 13px; font-family: '{family}'; background: transparent; }}
    {selector} QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {c['check_border']};
        border-radius: 5px; background: {c['control']}; }}
    {selector} QCheckBox::indicator:hover {{ border: 1px solid {c['accent_border']}; }}
    {selector} QCheckBox::indicator:checked {{ background: {ACCENT_HEX}; border: 1px solid {ACCENT_HEX}; image: url("{check_icon}"); }}
    {selector} QSlider {{ min-height: 22px; background: transparent; }}
    {selector} QSlider::groove:horizontal {{ height: 5px; background: {c['groove']}; border-radius: 2px; }}
    {selector} QSlider::sub-page:horizontal {{ background: {ACCENT_HEX}; border-radius: 2px; }}
    {selector} QSlider::handle:horizontal {{ background: {c['knob']}; border: 1px solid {c['knob_border']};
        width: 16px; margin: -6px 0; border-radius: 8px; }}
    {selector} QSlider::handle:horizontal:hover {{ border: 2px solid {ACCENT_HEX}; width: 14px; }}
    {selector} QComboBox {{ color: {c['text']}; background: {c['control']}; border: 1px solid {c['border']};
        border-radius: 10px; padding: 7px 12px; font-family: '{family}'; font-size: 13px; }}
    {selector} QComboBox:hover {{ background: {c['hover']}; border-color: {c['border_hover']}; }}
    {selector} QComboBox QAbstractItemView {{ background: {c['surface']}; color: {c['text']};
        selection-background-color: {c['hover']}; selection-color: {c['text']}; }}
    {selector} QFrame#sep {{ color: {c['border']}; }}
    """
