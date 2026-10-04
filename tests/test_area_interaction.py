# -*- coding: utf-8 -*-
"""Unit tests for the pure-geometry stencil area controller."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ui.helpers.area_interaction import (  # noqa: E402
    AreaInteractionController,
    Rect,
    cursor_for_region,
    CURSOR_ARROW,
    CURSOR_MOVE,
    CURSOR_SIZE_FDIAG,
    CURSOR_SIZE_BDIAG,
    CURSOR_SIZE_HOR,
    CURSOR_SIZE_VER,
    REGION_NONE,
    REGION_MOVE,
)


class HitTestTests(unittest.TestCase):
    def setUp(self):
        self.ctrl = AreaInteractionController(handle_size=14.0, min_size=8.0, snap=8.0)
        self.rect = Rect(100, 100, 200, 120)  # corners: (100,100)..(300,220)

    def test_corner_handles(self):
        self.assertEqual(self.ctrl.hit_test(100, 100, self.rect), "nw")
        self.assertEqual(self.ctrl.hit_test(300, 100, self.rect), "ne")
        self.assertEqual(self.ctrl.hit_test(300, 220, self.rect), "se")
        self.assertEqual(self.ctrl.hit_test(100, 220, self.rect), "sw")

    def test_edge_handles(self):
        self.assertEqual(self.ctrl.hit_test(200, 100, self.rect), "n")
        self.assertEqual(self.ctrl.hit_test(200, 220, self.rect), "s")
        self.assertEqual(self.ctrl.hit_test(100, 160, self.rect), "w")
        self.assertEqual(self.ctrl.hit_test(300, 160, self.rect), "e")

    def test_body_is_move(self):
        self.assertEqual(self.ctrl.hit_test(200, 160, self.rect), REGION_MOVE)

    def test_outside_is_none(self):
        self.assertEqual(self.ctrl.hit_test(50, 50, self.rect), REGION_NONE)
        self.assertEqual(self.ctrl.hit_test(400, 400, self.rect), REGION_NONE)

    def test_corner_priority_over_edge(self):
        # A point exactly on a corner must resolve to the corner, not an edge.
        self.assertEqual(self.ctrl.hit_test(100, 100, self.rect), "nw")

    def test_empty_rect_is_none(self):
        self.assertEqual(self.ctrl.hit_test(0, 0, Rect(0, 0, 0, 0)), REGION_NONE)


class CursorMappingTests(unittest.TestCase):
    def test_cursor_map(self):
        self.assertEqual(cursor_for_region(REGION_MOVE), CURSOR_MOVE)
        self.assertEqual(cursor_for_region("n"), CURSOR_SIZE_VER)
        self.assertEqual(cursor_for_region("s"), CURSOR_SIZE_VER)
        self.assertEqual(cursor_for_region("e"), CURSOR_SIZE_HOR)
        self.assertEqual(cursor_for_region("w"), CURSOR_SIZE_HOR)
        self.assertEqual(cursor_for_region("nw"), CURSOR_SIZE_FDIAG)
        self.assertEqual(cursor_for_region("se"), CURSOR_SIZE_FDIAG)
        self.assertEqual(cursor_for_region("ne"), CURSOR_SIZE_BDIAG)
        self.assertEqual(cursor_for_region("sw"), CURSOR_SIZE_BDIAG)
        self.assertEqual(cursor_for_region(""), CURSOR_ARROW)
        self.assertEqual(cursor_for_region("bogus"), CURSOR_ARROW)


class MoveTests(unittest.TestCase):
    def setUp(self):
        self.ctrl = AreaInteractionController(handle_size=14.0, min_size=8.0, snap=8.0)
        self.bounds = Rect(0, 0, 1000, 800)
        self.rect = Rect(100, 100, 200, 120)

    def test_move_basic(self):
        self.ctrl.begin(200, 160, REGION_MOVE, self.rect)
        out = self.ctrl.update(250, 210, self.bounds, snap=False)
        self.assertEqual(out.as_int_tuple(), (150, 150, 200, 120))

    def test_move_clamped_to_bounds_topleft(self):
        self.ctrl.begin(200, 160, REGION_MOVE, self.rect)
        out = self.ctrl.update(-500, -500, self.bounds, snap=False)
        self.assertEqual(out.as_int_tuple(), (0, 0, 200, 120))

    def test_move_clamped_to_bounds_bottomright(self):
        self.ctrl.begin(200, 160, REGION_MOVE, self.rect)
        out = self.ctrl.update(5000, 5000, self.bounds, snap=False)
        # right/bottom pinned: x = 1000-200, y = 800-120
        self.assertEqual(out.as_int_tuple(), (800, 680, 200, 120))

    def test_move_snaps_to_left_edge(self):
        self.ctrl.begin(200, 160, REGION_MOVE, self.rect)
        # drag so left lands at x=5 (within snap=8 of 0) -> snaps to 0
        out = self.ctrl.update(200 - 95, 160, self.bounds, snap=True)
        self.assertEqual(out.x, 0)

    def test_move_size_preserved(self):
        self.ctrl.begin(200, 160, REGION_MOVE, self.rect)
        out = self.ctrl.update(333, 222, self.bounds, snap=False)
        self.assertEqual((out.w, out.h), (200, 120))


class ResizeTests(unittest.TestCase):
    def setUp(self):
        self.ctrl = AreaInteractionController(handle_size=14.0, min_size=8.0, snap=8.0)
        self.bounds = Rect(0, 0, 1000, 800)
        self.rect = Rect(100, 100, 200, 120)  # se=(300,220)

    def test_resize_east(self):
        self.ctrl.begin(300, 160, "e", self.rect)
        out = self.ctrl.update(350, 160, self.bounds, snap=False)
        self.assertEqual(out.as_int_tuple(), (100, 100, 250, 120))

    def test_resize_se_corner(self):
        self.ctrl.begin(300, 220, "se", self.rect)
        out = self.ctrl.update(360, 260, self.bounds, snap=False)
        self.assertEqual(out.as_int_tuple(), (100, 100, 260, 160))

    def test_resize_nw_moves_origin(self):
        self.ctrl.begin(100, 100, "nw", self.rect)
        out = self.ctrl.update(80, 70, self.bounds, snap=False)
        # left/top move out, size grows
        self.assertEqual(out.as_int_tuple(), (80, 70, 220, 150))

    def test_resize_respects_min_size(self):
        self.ctrl.begin(300, 160, "e", self.rect)
        out = self.ctrl.update(-500, 160, self.bounds, snap=False)
        # right cannot go below left+min_size
        self.assertEqual(out.w, self.ctrl.min_size)

    def test_resize_clamped_to_bounds(self):
        self.ctrl.begin(300, 160, "e", self.rect)
        out = self.ctrl.update(5000, 160, self.bounds, snap=False)
        self.assertEqual(out.right, self.bounds.right)

    def test_resize_snaps_right_to_bounds(self):
        self.ctrl.begin(300, 160, "e", self.rect)
        # push right to 996 (within snap=8 of 1000) -> snaps to 1000
        out = self.ctrl.update(300 + (1000 - 300) - 4, 160, self.bounds, snap=True)
        self.assertEqual(out.right, 1000)

    def test_aspect_lock_corner_keeps_ratio(self):
        ctrl = AreaInteractionController(handle_size=14.0, min_size=8.0, snap=0.0)
        rect = Rect(0, 0, 200, 100)  # aspect 2:1
        ctrl.begin(200, 100, "se", rect)
        out = ctrl.update(300, 120, Rect(0, 0, 10000, 10000), keep_aspect=True, snap=False)
        # width changed more (+100) than height (+20) -> height derived = w/2
        self.assertAlmostEqual(out.w / out.h, 2.0, places=5)
        self.assertEqual((out.x, out.y), (0, 0))


class NudgeTests(unittest.TestCase):
    def setUp(self):
        self.ctrl = AreaInteractionController()
        self.bounds = Rect(0, 0, 1000, 800)
        self.rect = Rect(100, 100, 200, 120)

    def test_nudge_move(self):
        out = self.ctrl.nudge(self.rect, 1, 0, self.bounds)
        self.assertEqual(out.as_int_tuple(), (101, 100, 200, 120))

    def test_nudge_move_clamped(self):
        out = self.ctrl.nudge(Rect(0, 0, 200, 120), -5, -5, self.bounds)
        self.assertEqual(out.as_int_tuple(), (0, 0, 200, 120))

    def test_nudge_resize(self):
        out = self.ctrl.nudge(self.rect, 10, 5, self.bounds, resize=True)
        self.assertEqual(out.as_int_tuple(), (100, 100, 210, 125))

    def test_nudge_resize_min_size(self):
        out = self.ctrl.nudge(Rect(100, 100, 10, 10), -100, -100, self.bounds, resize=True)
        self.assertEqual((out.w, out.h), (self.ctrl.min_size, self.ctrl.min_size))


if __name__ == "__main__":
    unittest.main()
