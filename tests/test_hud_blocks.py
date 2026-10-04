# -*- coding: utf-8 -*-
"""HUD: new context blocks (time / speed / image / settings) + real progress bar."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402

_app = QApplication.instance() or QApplication([])

from ui.widgets.hud_overlay import HudOverlay, _BLOCK_ORDER  # noqa: E402


class HudBlocksTests(unittest.TestCase):
    def setUp(self):
        self.h = HudOverlay()

    def tearDown(self):
        self.h.deleteLater(); _app.processEvents()

    def test_new_blocks_exist(self):
        for key in ("time", "speed", "image", "settings"):
            self.assertIn(key, _BLOCK_ORDER)
            self.assertIn(key, self.h._blocks)
            # the editor's show/hide checkbox is auto-created for each block
            self.assertIn(key, self.h._vis_checks)

    def test_progress_uses_real_bar(self):
        self.h.update_stats({"percent": 42, "elapsed_seconds": 128, "eta_seconds": 305,
                             "colors_total": 20, "colors_done": 8, "phase": "drawing"})
        bar = self.h._blocks["progress"]._bar
        self.assertAlmostEqual(bar._value, 42.0, places=1)
        self.assertFalse(bar._done)
        # completion turns the bar green/done
        self.h.update_stats({"percent": 100, "elapsed_seconds": 200, "phase": "drawing"})
        self.assertTrue(self.h._blocks["progress"]._bar._done)

    def test_time_and_speed_render(self):
        self.h.update_stats({"percent": 50, "elapsed_seconds": 128, "eta_seconds": 60,
                             "colors_total": 20, "colors_done": 10, "phase": "drawing"})
        self.assertIn("02:08", self.h._blocks["time"]._value.text())   # 128s elapsed
        self.assertIn("01:00", self.h._blocks["time"]._value.text())   # 60s remaining
        # speed: 10 colors / 128s * 60 ≈ 4.7 colors/min
        self.assertRegex(self.h._blocks["speed"]._value.text(), r"\d")

    def test_speed_idle_when_no_elapsed(self):
        self.h.update_stats({"percent": 0, "elapsed_seconds": 0})
        self.assertIn("—", self.h._blocks["speed"]._value.text())

    def test_image_and_settings(self):
        self.h.set_image_info(name="dragon.webp", width=459, height=427, palette=55,
                              area=(285, 230, 510, 340))
        txt = self.h._blocks["image"]._value.text()
        self.assertIn("dragon.webp", txt)
        self.assertIn("459", txt)
        self.assertIn("510", txt)   # draw-area width
        self.h.set_settings_summary({"mode": "bw", "brush": 3, "dither": True,
                                     "dynamic_brush": False, "background_removal": True,
                                     "algorithm": "dfs_4dir"})
        s = self.h._blocks["settings"]._value.text()
        self.assertIn("dfs_4dir", s)
        self.assertIn("3", s)

    def test_image_empty(self):
        self.h.set_image_info(name=None)
        self.assertTrue(len(self.h._blocks["image"]._value.text()) > 0)


if __name__ == "__main__":
    unittest.main()
