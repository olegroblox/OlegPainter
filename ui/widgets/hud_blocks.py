# -*- coding: utf-8 -*-
"""Reusable HUD block widget + content/render logic.

Extracted from ``hud_overlay.py`` so BOTH the legacy `HudOverlay` and the new
unified Graphics-View overlay can share the exact same block rendering without
duplication. ``HudContentMixin`` provides the data→block render methods; it
operates on ``self._blocks`` (a ``dict[str, HudBlock]``) and a small set of state
attributes that the host class initialises. The host owns layout/edit/persistence;
this mixin owns only what each block *shows*.
"""
from __future__ import annotations

import logging

from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtWidgets import QFrame, QLabel, QVBoxLayout, QHBoxLayout

from ui.i18n import tr

log = logging.getLogger("olegpainter.ui.widgets.hud_blocks")


# Default top-left positions of each block, as fractions of the overlay size.
_DEFAULT_POS = {
    "status": (0.83, 0.04),
    "progress": (0.83, 0.10),
    "colors": (0.83, 0.19),
    "layers": (0.83, 0.30),
    "palette": (0.83, 0.39),
    "hotkeys": (0.83, 0.48),
}

_BLOCK_ORDER = ["status", "progress", "colors", "layers", "palette", "hotkeys"]

# i18n key for each block title.
_BLOCK_TITLE_KEY = {
    "status": "hud_title_status",
    "progress": "hud_title_progress",
    "colors": "hud_title_colors",
    "layers": "hud_title_layers",
    "palette": "hud_title_palette",
    "hotkeys": "hud_title_hotkeys",
}

# Hotkey action whose key is shown as a badge in the block corner.
_BLOCK_BADGE_CODE = {
    "progress": "start_pause",
    "colors": "capture_hex_palette",
    "layers": "define_app_layers",
    "palette": "define_manual_palette",
}


class HudBlock(QFrame):
    """A single draggable info card inside the HUD."""

    moved = Signal()

    def __init__(self, key: str, parent=None):
        super().__init__(parent)
        self._key = key
        self.setObjectName("HudBlock")
        self.setAttribute(Qt.WA_StyledBackground, True)
        self._edit = False
        self._drag_offset: QPoint | None = None
        self._frac = _DEFAULT_POS.get(key, (0.83, 0.05))

        lay = QVBoxLayout(self)
        lay.setContentsMargins(12, 8, 12, 10)
        lay.setSpacing(3)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)
        self._title = QLabel("", self)
        self._title.setObjectName("HudBlockTitle")
        self._badge = QLabel("", self)
        self._badge.setObjectName("HudBlockBadge")
        self._badge.setVisible(False)
        header.addWidget(self._title, 0, Qt.AlignLeft | Qt.AlignVCenter)
        header.addStretch(1)
        header.addWidget(self._badge, 0, Qt.AlignRight | Qt.AlignVCenter)

        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        row.setSpacing(8)
        self._swatch = QFrame(self)
        self._swatch.setObjectName("HudSwatch")
        self._swatch.setFixedSize(16, 16)
        self._swatch.setVisible(False)
        self._value = QLabel("", self)
        self._value.setObjectName("HudBlockValue")
        self._value.setTextFormat(Qt.RichText)
        row.addWidget(self._swatch, 0, Qt.AlignTop)
        row.addWidget(self._value, 1)

        lay.addLayout(header)
        lay.addLayout(row)
        self.setMinimumWidth(160)

    # ----- content --------------------------------------------------------- #
    def set_title(self, text: str) -> None:
        self._title.setText(text)

    def set_badge(self, text: str) -> None:
        text = str(text or "").strip()
        self._badge.setText(text)
        self._badge.setVisible(bool(text))

    def set_value(self, html: str, swatch_color: str | None = None) -> None:
        self._value.setText(html)
        if swatch_color:
            self._swatch.setVisible(True)
            self._swatch.setStyleSheet(
                f"#HudSwatch{{background:{swatch_color};"
                "border:1px solid rgba(255,255,255,0.5);border-radius:3px;}"
            )
        else:
            self._swatch.setVisible(False)
        self.adjustSize()

    def set_scale(self, scale: float) -> None:
        tf = self._title.font()
        tf.setPointSize(max(7, round(8 * scale)))
        tf.setBold(True)
        self._title.setFont(tf)
        bf = self._badge.font()
        bf.setPointSize(max(7, round(8 * scale)))
        bf.setBold(True)
        self._badge.setFont(bf)
        vf = self._value.font()
        vf.setPointSize(max(8, round(11 * scale)))
        self._value.setFont(vf)
        self.adjustSize()

    # ----- geometry / persistence ----------------------------------------- #
    def fraction(self) -> tuple[float, float]:
        return self._frac

    def set_fraction(self, fx: float, fy: float) -> None:
        self._frac = (float(fx), float(fy))

    def apply_fraction(self, area_w: int, area_h: int) -> None:
        fx, fy = self._frac
        x = max(0, min(int(fx * area_w), max(0, area_w - self.width())))
        y = max(0, min(int(fy * area_h), max(0, area_h - self.height())))
        self.move(x, y)

    def _store_fraction_from_pos(self) -> None:
        parent = self.parentWidget()
        if parent is None:
            return
        self._frac = (self.x() / max(1, parent.width()), self.y() / max(1, parent.height()))

    # ----- edit-mode dragging + highlight ---------------------------------- #
    def set_edit(self, enabled: bool) -> None:
        self._edit = bool(enabled)
        self.setProperty("editing", self._edit)
        self.setCursor(Qt.OpenHandCursor if self._edit else Qt.ArrowCursor)
        self._repolish()

    def set_highlight(self, on: bool) -> None:
        if bool(self.property("capturing")) == bool(on):
            return
        self.setProperty("capturing", bool(on))
        self._repolish()

    def _repolish(self) -> None:
        st = self.style()
        if st is not None:
            st.unpolish(self)
            st.polish(self)

    def mousePressEvent(self, event):
        if self._edit and event.button() == Qt.LeftButton:
            self._drag_offset = event.position().toPoint()
            self.setCursor(Qt.ClosedHandCursor)
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self._edit and self._drag_offset is not None:
            parent = self.parentWidget()
            new_pos = self.mapToParent(event.position().toPoint() - self._drag_offset)
            if parent is not None:
                nx = max(0, min(new_pos.x(), parent.width() - self.width()))
                ny = max(0, min(new_pos.y(), parent.height() - self.height()))
                new_pos = QPoint(nx, ny)
            self.move(new_pos)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        if self._edit and self._drag_offset is not None:
            self._drag_offset = None
            self.setCursor(Qt.OpenHandCursor)
            self._store_fraction_from_pos()
            self.moved.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)


from ui.widgets.application_hud_state import ApplicationHudStateMixin


class HudContentMixin(ApplicationHudStateMixin):
    """Data→block render logic shared by HUD overlays.

    Host must provide instance attrs: ``self._blocks`` (dict[str, HudBlock]),
    ``self._statuses`` (dict), ``self._hotkeys_map`` (dict), ``self._capture`` (dict),
    ``self._last_stats`` (dict).
    """

    _STATE_KEY = {
        "idle": ("hud_state_idle", "#aab6cc"),
        "stopped": ("hud_state_stopped", "#aab6cc"),
        "started": ("hud_state_running", "#5fd07a"),
        "running": ("hud_state_running", "#5fd07a"),
        "paused": ("hud_state_paused", "#e7b94a"),
        "completed": ("hud_state_completed", "#5fa8ff"),
        "stopping": ("hud_state_stopping", "#e7b94a"),
        "failed": ("hud_state_failed", "#ff8a4c"),
    }

    def _update_badges(self) -> None:
        for key, code in _BLOCK_BADGE_CODE.items():
            block = self._blocks.get(key)
            if block is not None:
                block.set_badge(self._hk(code, ""))

    # ----- public setters (fed by main_window from service signals) ------- #
    def set_drawing_state(self, state: str) -> None:
        self._statuses["state"] = str(state or "idle").lower()
        self._render_status()
        self._render_hotkeys()

    def update_stats(self, stats: dict) -> None:
        stats = stats if isinstance(stats, dict) else {}
        self._last_stats = stats
        percent = stats.get("percent")
        pct = "--" if percent is None else f"{int(percent)}"
        bar = self._mini_bar(percent)
        eta = self._eta_only(stats.get("eta_text") or "~ --:--")
        finishing_html = ""
        if stats.get("phase") == "finishing":
            finishing_html = f"<br><span style='color:#ff8a4c'>◔ {tr('hud_finishing')}</span>"
        self._blocks["progress"].set_value(
            f"<span style='font-size:20px;font-weight:700'>{pct}%</span><br>"
            f"<span style='color:#aab6cc'>{bar}</span><br>"
            f"<span style='color:#aab6cc'>{eta}</span>{finishing_html}"
        )
        total = int(stats.get("colors_total") or 0)
        done = int(stats.get("colors_done") or 0)
        remaining = int(stats.get("colors_remaining") or max(0, total - done))
        cur = stats.get("current_color")
        if total > 0:
            colors_html = (
                f"<span style='font-size:16px;font-weight:700'>{done}/{total}</span>"
                f"<br><span style='color:#aab6cc'>{tr('hud_colors_left')} {remaining}</span>"
            )
        else:
            colors_html = "<span style='color:#8a93a6'>—</span>"
        self._blocks["colors"].set_value(colors_html, swatch_color=cur if total > 0 else None)

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
        self._update_badges()
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
            body = f"<span style='font-size:16px;font-weight:700;color:#ff8a4c'>◉ {title}…{count_txt}</span>"
            if hint:
                body += f"<br><span style='color:#ffd9b0'>{hint}</span>"
            self._blocks["status"].set_value(body)
            return
        key, color = self._STATE_KEY.get(self._statuses["state"], ("hud_state_idle", "#aab6cc"))
        self._blocks["status"].set_value(
            f"<span style='font-size:16px;font-weight:700;color:{color}'>● {tr(key)}</span>"
        )

    def _render_layers(self) -> None:
        n, mx = self._statuses["layers"]
        text = f"{n}/{mx}" if mx else (str(n) if n else "—")
        self._blocks["layers"].set_value(
            f"<span style='font-size:16px;font-weight:700'>{text}</span>"
            f"<br><span style='color:#aab6cc'>{tr('hud_layers_caption')}</span>"
        )

    def _render_palette(self) -> None:
        n, mx = self._statuses["palette"]
        text = f"{n}/{mx}" if mx else (str(n) if n else "—")
        self._blocks["palette"].set_value(
            f"<span style='font-size:16px;font-weight:700'>{text}</span>"
            f"<br><span style='color:#aab6cc'>{tr('hud_palette_caption')}</span>"
        )

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
        lines = []
        for name, key, status, hot in rows:
            name_color = "#ff8a4c" if hot else "#cdd6e6"
            status_html = ""
            if status:
                status_color = "#ff8a4c" if hot else "#7fd6a0"
                status_html = f" <span style='color:{status_color}'>· {status}</span>"
            lines.append(
                f"<span style='color:{name_color}'>{name}</span> "
                f"<b style='color:#9fc0ff'>{key}</b>{status_html}"
            )
        self._blocks["hotkeys"].set_value("<br>".join(lines))

    # ----- helpers --------------------------------------------------------- #
    @staticmethod
    def _eta_only(eta_text: str) -> str:
        return str(eta_text).split("  ")[0].strip() if eta_text else "~ --:--"

    @staticmethod
    def _mini_bar(percent) -> str:
        try:
            p = max(0, min(100, int(percent)))
        except Exception:
            return "▱" * 10
        filled = round(p / 10)
        return "▰" * filled + "▱" * (10 - filled)
