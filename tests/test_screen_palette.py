# -*- coding: utf-8 -*-
"""Empirical 'рамка' palette mode (screen_palette): calibrate a rectangle -> sample pixels
into (LAB, screen-xy) -> pick the nearest-colour pixel for any target. Layout-agnostic."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from ui.services.painter_service import PainterService  # noqa: E402

_app = QApplication.instance() or QApplication([])


class ScreenPaletteTests(unittest.TestCase):
    def setUp(self):
        self.svc = PainterService()
        self.e = self.svc.engine
        self.e.stop_flag = False
        self.e.drawing_enabled = True
        self.e._automation_cancelled = lambda: False
        self.e._should_play_actions = lambda phase: False
        self.calls = []
        self.e._click_abs = lambda x, y: self.calls.append((x, y))

    def tearDown(self):
        self.svc.shutdown(); self.svc.deleteLater(); _app.processEvents()

    def _calibrate_rgb(self):
        # red@(10,10), green@(20,10), blue@(30,10), white@(40,10)
        rgb = np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255], [255, 255, 255]], dtype=np.float32)
        lab = self.e._srgb_to_lab_array(rgb)
        coords = np.array([[10, 10], [20, 10], [30, 10], [40, 10]], dtype=np.int32)
        self.e.screen_palette_calib = {"lab": lab, "coords": coords}

    def test_picks_nearest_colour_coordinate(self):
        self._calibrate_rgb()
        self.e.color_picking_method = "screen_palette"
        for hexv, expected in (("FF0000", (10, 10)), ("00FF00", (20, 10)),
                               ("0000FF", (30, 10)), ("FFFFFF", (40, 10))):
            self.calls.clear()
            self.e.pick_color(hexv)
            self.assertEqual(self.calls[-1], expected, f"{hexv} should click {expected}")

    def test_near_colour_snaps_to_closest(self):
        self._calibrate_rgb()
        self.e.color_picking_method = "screen_palette"
        self.e.pick_color("EE1010")   # near-red -> red sample
        self.assertEqual(self.calls[-1], (10, 10))

    def test_not_calibrated_stops(self):
        self.e.screen_palette_calib = None
        self.e.color_picking_method = "screen_palette"
        self.e.stop_flag = False
        self.e.pick_color("FF0000")
        self.assertTrue(self.e.stop_flag, "uncalibrated screen palette must hard-stop")
        self.assertEqual(self.calls, [])

    def _calibrate_wheel_and_slider(self):
        # wheel rendered at FULL value: red@(10,10), green@(20,10), blue@(30,10)
        rgb = np.array([[255, 0, 0], [0, 255, 0], [0, 0, 255]], dtype=np.float32)
        lab = self.e._srgb_to_lab_array(rgb)
        coords = np.array([[10, 10], [20, 10], [30, 10]], dtype=np.int32)
        slider = ("vertical", 100, 200, 300)   # x=100, top y=200 (V=1), bottom y=300 (V=0)
        self.e.screen_palette_calib = {"lab": lab, "coords": coords, "slider": slider}

    def test_wheel_plus_slider_dark_colour_two_clicks(self):
        self._calibrate_wheel_and_slider()
        self.e.color_picking_method = "screen_palette"
        self.calls.clear()
        self.e.pick_color("800000")   # dark red: hue=red, V=128/255~0.502
        self.assertEqual(len(self.calls), 2, "wheel + slider = two clicks")
        self.assertEqual(self.calls[0], (10, 10), "full-value match -> red wheel pixel")
        self.assertEqual(self.calls[1][0], 100, "slider x is fixed")
        self.assertAlmostEqual(self.calls[1][1], 250, delta=2, msg="V~0.5 -> slider midpoint")

    def test_wheel_plus_slider_bright_colour(self):
        self._calibrate_wheel_and_slider()
        self.e.color_picking_method = "screen_palette"
        self.calls.clear()
        self.e.pick_color("00FF00")   # bright green: V=1
        self.assertEqual(self.calls[0], (20, 10), "green wheel pixel")
        self.assertEqual(self.calls[1], (100, 200), "V=1 -> slider top")

    def test_config_round_trip(self):
        self._calibrate_rgb()
        sp = self.e.get_config().get("screen_palette_calib")
        self.assertIsInstance(sp, dict)
        self.assertEqual(len(sp["lab"]), 4)
        self.e.screen_palette_calib = None
        self.e.load_config({"screen_palette_calib": sp})
        self.assertIsNotNone(self.e.screen_palette_calib)
        self.assertEqual(self.e.screen_palette_calib["lab"].shape, (4, 3))
        self.assertEqual(self.e.screen_palette_calib["coords"][0].tolist(), [10, 10])
        # picking still works after reload
        self.e.color_picking_method = "screen_palette"
        self.calls.clear()
        self.e.pick_color("0000FF")
        self.assertEqual(self.calls[-1], (30, 10))


if __name__ == "__main__":
    unittest.main()
