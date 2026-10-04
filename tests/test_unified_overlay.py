# -*- coding: utf-8 -*-
"""Tests for the unified board overlay building blocks (grows across phases)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from ui.widgets.hud_blocks import HudBlock, HudContentMixin, _BLOCK_ORDER  # noqa: E402

_app = QApplication.instance() or QApplication([])


class _Host(HudContentMixin):
    """Minimal host providing the state attrs HudContentMixin expects."""

    def __init__(self):
        self._blocks = {k: HudBlock(k) for k in _BLOCK_ORDER}
        self._statuses = {"state": "idle", "layers": (0, 0), "palette": (0, 0), "hex": False}
        self._hotkeys_map = {}
        self._capture = {"active": False, "kind": None, "count": 0, "max": 0, "slot": None}
        self._last_stats = {}


class HudContentMixinTests(unittest.TestCase):
    def test_stats_render_into_blocks(self):
        h = _Host()
        h.update_stats({"percent": 42, "colors_total": 10, "colors_done": 3, "current_color": "#ff0000"})
        self.assertIn("42%", h._blocks["progress"]._value.text())
        self.assertIn("3/10", h._blocks["colors"]._value.text())

    def test_state_and_layers_palette(self):
        h = _Host()
        h.set_drawing_state("running")
        self.assertIn("●", h._blocks["status"]._value.text())
        h.set_layers(2, 5)
        self.assertIn("2/5", h._blocks["layers"]._value.text())
        h.set_palette(8)
        self.assertIn("8", h._blocks["palette"]._value.text())

    def test_hotkeys_and_badges(self):
        h = _Host()
        h.set_hotkeys({"start_pause": "F3", "define_app_layers": "F8"})
        self.assertIn("F3", h._blocks["hotkeys"]._value.text())
        self.assertEqual(h._blocks["progress"]._badge.text(), "F3")
        self.assertEqual(h._blocks["layers"]._badge.text(), "F8")

    def test_capture_highlight(self):
        h = _Host()
        h.set_capture_state({"active": True, "kind": "layers", "count": 1, "max": 6})
        self.assertTrue(bool(h._blocks["layers"].property("capturing")))
        self.assertFalse(bool(h._blocks["palette"].property("capturing")))

    def test_mini_bar_and_eta(self):
        self.assertEqual(len(HudContentMixin._mini_bar(50)), 10)
        self.assertEqual(HudContentMixin._eta_only("~ 01:23  extra"), "~ 01:23")


from PySide6.QtCore import QRect, QRectF, Qt  # noqa: E402
from PySide6.QtGui import QImage  # noqa: E402
from ui.widgets.unified_board_overlay import UnifiedBoardOverlay, HudBlockItem  # noqa: E402


class UnifiedBoardOverlayTests(unittest.TestCase):
    def _overlay(self):
        o = UnifiedBoardOverlay()
        o.setGeometry(0, 0, 1280, 720)
        return o

    def test_construct_has_items(self):
        o = self._overlay()
        self.assertIsNotNone(o._area_item)
        self.assertEqual(set(o._blocks.keys()), set(_BLOCK_ORDER))
        self.assertEqual(len(o._hud_items), len(_BLOCK_ORDER))
        # z-order: blocks above stencil
        self.assertGreater(o._hud_items["status"].zValue(), o._area_item.zValue())

    def test_facade_methods_exist(self):
        o = self._overlay()
        for m in ("set_area", "set_image", "set_view_opacity", "show_overlay", "hide_overlay",
                  "toggle_visible", "is_editing", "is_edit_mode", "set_edit_mode", "cancel_edit_session",
                  "reset_layout", "export_state", "restore_state", "update_stats", "set_drawing_state",
                  "set_layers", "set_palette", "set_capture_state", "set_hotkeys"):
            self.assertTrue(callable(getattr(o, m, None)), m)

    def test_set_area_is_programmatic_no_live_emit(self):
        o = self._overlay()
        live = []
        o.areaLiveChanged.connect(lambda *a: live.append(a))
        o.set_area(QRect(100, 100, 400, 300))
        self.assertEqual(live, [], "programmatic set_area must NOT emit areaLiveChanged")
        # a direct (user-style) geometry change DOES emit
        o._area_item.setRect(QRectF(120, 120, 420, 320))
        self.assertTrue(live, "user geometry change must emit areaLiveChanged")

    def test_edit_mode_toggle(self):
        o = self._overlay()
        seen = []
        o.editModeChanged.connect(lambda v: seen.append(v))
        self.assertFalse(o.is_editing())
        o.set_edit_mode(True)
        self.assertTrue(o.is_editing())
        o.set_edit_mode(False)
        self.assertFalse(o.is_editing())
        self.assertEqual(seen, [True, False])

    def test_hud_content_renders(self):
        o = self._overlay()
        o.update_stats({"percent": 55, "colors_total": 4, "colors_done": 1})
        self.assertIn("55%", o._blocks["progress"]._value.text())

    def test_set_image_and_opacity(self):
        o = self._overlay()
        img = QImage(40, 30, QImage.Format_ARGB32); img.fill(0xFF00FF00)
        o.set_image(img)
        self.assertIsNotNone(o._area_item._pixmap)
        o.set_view_opacity(0.3)
        self.assertAlmostEqual(o._stencil_view_opacity, 0.3, places=3)

    def test_persistence_round_trip(self):
        o = self._overlay()
        o._block_fracs["status"] = (0.1, 0.2)
        o.set_view_opacity(0.42)
        state = o.export_state()
        o2 = self._overlay()
        o2.restore_state(state)
        self.assertEqual(o2._block_fracs["status"], (0.1, 0.2))
        self.assertAlmostEqual(o2._stencil_view_opacity, 0.42, places=3)


if __name__ == "__main__":
    unittest.main()
