# -*- coding: utf-8 -*-
"""pen_max_step: continuous-stroke sub-stepping for targets that stamp at the sampled
cursor position (Roblox spray). 0 = instant jump (MS Paint behaviour, unchanged)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from ui.services.painter_service import PainterService  # noqa: E402

_app = QApplication.instance() or QApplication([])


class PenMaxStepTests(unittest.TestCase):
    def setUp(self):
        self.svc = PainterService()
        self.e = self.svc.engine
        self.e.drawing_enabled = True
        self.e.stop_flag = False
        self.e._automation_cancelled = lambda: False
        self.e.draw_delay = 0.0
        self.e.area_fill_delay = 0.0
        self.calls = []
        self.e._move_abs = lambda x, y: self.calls.append((x, y))

    def tearDown(self):
        self.svc.shutdown(); self.svc.deleteLater(); _app.processEvents()

    def test_off_is_single_instant_jump(self):
        self.e.pen_max_step = 0
        self.e.draw_line(0, 0, 100, 0)
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[-1], (100, 0))

    def test_walks_in_bounded_hops(self):
        self.e.pen_max_step = 10
        self.e.draw_line(0, 0, 100, 0)
        self.assertGreaterEqual(len(self.calls), 9)
        hops = [abs(self.calls[i][0] - self.calls[i - 1][0]) for i in range(1, len(self.calls))]
        self.assertLessEqual(max(hops), 10, "no hop may exceed pen_max_step")
        self.assertEqual(self.calls[-1], (100, 0), "must land exactly on the endpoint")

    def test_short_move_not_subdivided(self):
        self.e.pen_max_step = 10
        self.e.draw_line(0, 0, 6, 0)   # below the step
        self.assertEqual(len(self.calls), 1)

    def test_config_round_trip(self):
        self.e.pen_max_step = 7
        self.assertEqual(self.e.get_config().get("pen_max_step"), 7)
        self.e.pen_max_step = 0
        self.e.load_config({"pen_max_step": 7})
        self.assertEqual(self.e.pen_max_step, 7)

    def test_service_setter(self):
        self.svc.set_pen_max_step(15)
        self.assertEqual(self.e.pen_max_step, 15)
        self.svc.set_pen_max_step(-5)   # clamped to >= 0
        self.assertEqual(self.e.pen_max_step, 0)

    def test_pause_mid_stroke_completes_no_teleport(self):
        # Pause (drawing_enabled=False) mid-stroke must NOT break early and teleport to
        # the endpoint — the stroke completes so nothing is left unpainted.
        self.e.pen_max_step = 10
        state = {"i": 0}
        def move(x, y):
            self.calls.append((x, y)); state["i"] += 1
            if state["i"] == 2:
                self.e.drawing_enabled = False   # user paused mid-stroke
        self.e._move_abs = move
        self.e.draw_line(0, 0, 100, 0)
        self.assertEqual(self.calls[-1], (100, 0), "stroke must finish at the endpoint")
        self.assertGreaterEqual(len(self.calls), 9, "all sub-steps drawn, not skipped")

    def test_hard_stop_skips_endpoint_teleport(self):
        # A HARD stop mid-stroke breaks AND skips the final jump to the endpoint (no stray
        # line dragged to the end on abort).
        self.e.pen_max_step = 10
        state = {"i": 0}
        def move(x, y):
            self.calls.append((x, y)); state["i"] += 1
            if state["i"] == 2:
                self.e.stop_flag = True
        self.e._move_abs = move
        self.e.draw_line(0, 0, 100, 0)
        self.assertNotEqual(self.calls[-1], (100, 0), "no endpoint teleport on hard stop")

    def test_focus_target_round_trip(self):
        self.e.focus_target_window_enabled = False
        self.assertEqual(self.e.get_config().get("focus_target_window_enabled"), False)
        self.e.load_config({"focus_target_window_enabled": True})
        self.assertTrue(self.e.focus_target_window_enabled)
        self.svc.set_focus_target_window_enabled(False)
        self.assertFalse(self.e.focus_target_window_enabled)


if __name__ == "__main__":
    unittest.main()
