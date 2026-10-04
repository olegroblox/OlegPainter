# -*- coding: utf-8 -*-
"""Tests for the Graphics-View-based draw-area editor (DrawAreaItem / BoardView)."""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QGraphicsScene  # noqa: E402
from PySide6.QtCore import QRectF, QPointF, Qt  # noqa: E402

from ui.widgets.board_editor import DrawAreaItem, BoardView  # noqa: E402
from ui.helpers.viewport_mapper import (  # noqa: E402
    MonitorSnapshot,
    desktop_rect_to_ui_rect,
    qrect_from_tuple,
)

_app = QApplication.instance() or QApplication([])


class DrawAreaItemTests(unittest.TestCase):
    def _item(self):
        scene = QGraphicsScene()
        scene.setSceneRect(0, 0, 1920, 1080)
        item = DrawAreaItem(QRectF(100, 100, 400, 300))  # right=500 bottom=400
        scene.addItem(item)
        return scene, item

    def test_hit_test_regions(self):
        _, item = self._item()
        self.assertEqual(item._region_at(QPointF(100, 100)), "nw")
        self.assertEqual(item._region_at(QPointF(500, 400)), "se")
        self.assertEqual(item._region_at(QPointF(500, 250)), "e")
        self.assertEqual(item._region_at(QPointF(300, 250)), "move")
        self.assertEqual(item._region_at(QPointF(10, 10)), "")

    def test_geometry_changed_emits(self):
        _, item = self._item()
        seen = []
        item.geometryChanged.connect(lambda r: seen.append((r.x(), r.y(), r.width(), r.height())))
        item.setRect(QRectF(0, 0, 200, 200))
        self.assertEqual(seen[-1], (0, 0, 200, 200))

    def test_resize_east_live(self):
        _, item = self._item()
        seen = []
        item.geometryChanged.connect(lambda r: seen.append(r))
        item._drag_region = "e"
        item._start_scene = QPointF(500, 250)
        item._start_rect = QRectF(100, 100, 400, 300)
        item.apply_drag(QPointF(560, 250))
        r = item.rect()
        self.assertEqual((round(r.x()), round(r.width())), (100, 460))
        self.assertTrue(seen, "geometryChanged must fire live during drag")

    def test_move_body(self):
        _, item = self._item()
        item._drag_region = "move"
        item._start_scene = QPointF(300, 250)
        item._start_rect = QRectF(100, 100, 400, 300)
        item.apply_drag(QPointF(350, 300))
        r = item.rect()
        self.assertEqual((round(r.x()), round(r.y())), (150, 150))
        self.assertEqual((round(r.width()), round(r.height())), (400, 300))

    def test_resize_keeps_aspect_with_shift(self):
        _, item = self._item()  # aspect 400/300 = 1.333
        item._drag_region = "se"
        item._start_scene = QPointF(500, 400)
        item._start_rect = QRectF(100, 100, 400, 300)
        item.apply_drag(QPointF(700, 420), keep_aspect=True)
        r = item.rect()
        self.assertAlmostEqual(r.width() / r.height(), 400 / 300, places=2)

    def test_clamped_to_scene(self):
        _, item = self._item()
        item._drag_region = "e"
        item._start_scene = QPointF(500, 250)
        item._start_rect = QRectF(100, 100, 400, 300)
        item.apply_drag(QPointF(99999, 250))
        self.assertLessEqual(item.rect().right(), 1920)


class BoardViewTests(unittest.TestCase):
    def test_construct_and_configure(self):
        view = BoardView()
        view.set_scene_to_screen(QRectF(0, 0, 1280, 720).toRect())
        self.assertEqual(view.scene().sceneRect(), QRectF(0, 0, 1280, 720))
        view.set_passthrough(True)
        view.set_passthrough(False)
        item = DrawAreaItem(QRectF(10, 10, 100, 80))
        view.scene().addItem(item)
        self.assertIn(item, view.scene().items())


class CoverMonitorTests(unittest.TestCase):
    """Phase 3: window geometry in LOGICAL px, scene rect in PHYSICAL desktop px,
    item rects == physical px (engine draw_region), across DPI / multi-monitor."""

    def _snapshot(self):
        # 4K monitor at 200% DPI, to the RIGHT of a logical primary (not at origin).
        return MonitorSnapshot(
            monitor_id="\\\\.\\DISPLAY2",
            device_name="\\\\.\\DISPLAY2",
            desktop_rect=(1920, 0, 3840, 2160),  # PHYSICAL px
            logical_rect=(960, 0, 1920, 1080),   # LOGICAL px (÷2)
            scale_x=2.0, scale_y=2.0,
            is_primary=False, screen_name="DISPLAY2",
        )

    def test_scene_rect_is_physical(self):
        view = BoardView()
        snap = self._snapshot()
        view.cover_monitor(snap)
        self.assertEqual(view.scene().sceneRect(), QRectF(1920, 0, 3840, 2160))

    def test_window_geometry_is_logical(self):
        view = BoardView()
        snap = self._snapshot()
        view.cover_monitor(snap)
        expected = qrect_from_tuple(desktop_rect_to_ui_rect(snap.desktop_rect, snap))
        self.assertEqual(view.geometry().size(), expected.size())

    def test_item_rect_stays_physical(self):
        view = BoardView()
        view.cover_monitor(self._snapshot())
        item = DrawAreaItem(QRectF(2000, 100, 800, 600))  # physical px on DISPLAY2
        view.scene().addItem(item)
        self.assertEqual(item.rect(), QRectF(2000, 100, 800, 600))
        # negative-origin monitor (secondary to the LEFT) must also work
        view2 = BoardView()
        view2.cover_monitor(MonitorSnapshot(
            monitor_id="L", device_name="L",
            desktop_rect=(-1920, 0, 1920, 1080), logical_rect=(-1920, 0, 1920, 1080),
            scale_x=1.0, scale_y=1.0,
        ))
        self.assertEqual(view2.scene().sceneRect(), QRectF(-1920, 0, 1920, 1080))


class CropModeTests(unittest.TestCase):
    """Phase 5a: drag-edge crop on the DrawAreaItem (PureRef-style)."""

    def _item(self):
        scene = QGraphicsScene()
        scene.setSceneRect(0, 0, 1920, 1080)
        item = DrawAreaItem(QRectF(100, 100, 400, 300))  # right=500 bottom=400
        scene.addItem(item)
        return scene, item

    def test_enter_crop_mode_initialises_full(self):
        _, item = self._item()
        item.set_crop_mode(True)
        self.assertTrue(item.is_cropping())
        self.assertEqual(item._crop_rect, QRectF(100, 100, 400, 300))

    def test_crop_drag_emits_view_normalized(self):
        _, item = self._item()
        item.set_crop_mode(True)
        seen = []
        item.cropApplied.connect(lambda l, t, r, b: seen.append((round(l, 4), round(t, 4), round(r, 4), round(b, 4))))
        item._crop_region = "w"
        item._crop_start_scene = QPointF(100, 250)
        item._crop_start_rect = QRectF(100, 100, 400, 300)
        item.apply_crop_drag(QPointF(200, 250))  # west edge inward to x=200
        self.assertEqual(item._crop_rect.left(), 200)
        item._crop_region = ""
        item._emit_crop()
        self.assertEqual(seen[-1], (0.25, 0.0, 1.0, 1.0))

    def test_crop_confined_to_area(self):
        _, item = self._item()
        item.set_crop_mode(True)
        item._crop_region = "w"
        item._crop_start_scene = QPointF(100, 250)
        item._crop_start_rect = QRectF(100, 100, 400, 300)
        item.apply_crop_drag(QPointF(-500, 250))  # past the area's left edge
        self.assertGreaterEqual(item._crop_rect.left(), 100)

    def test_signals_exist(self):
        _, item = self._item()
        self.assertTrue(hasattr(item, "areaDragFinished"))
        self.assertTrue(hasattr(item, "cropApplied"))


if __name__ == "__main__":
    unittest.main()
