from __future__ import annotations

import os
import unittest

import numpy as np
from PIL import Image

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from engine.olegpainter.core import OlegPainter


def _engine(brush: int = 2) -> OlegPainter:
    engine = OlegPainter(status_callback=lambda _m: None)
    engine.brush_size = brush
    # 3x3 cells: row 0 white, row 1 red, row 2 background.
    engine.cluster_map = np.array([[0, 0, 0], [1, 1, 1], [2, 2, 2]])
    engine._background_cluster_id = 2
    engine.drawn_mask = engine.cluster_map == 2
    preview = np.zeros((3 * brush, 3 * brush, 4), np.uint8)
    preview[:brush, :, :3] = 255
    preview[brush:2 * brush, :, :3] = (200, 0, 0)
    preview[:2 * brush, :, 3] = 255
    engine.quantized_preview_image = Image.fromarray(preview)
    return engine


class SkipMatchingCanvasTests(unittest.TestCase):
    def test_white_on_white_canvas_is_counted_and_red_is_not(self):
        engine = _engine()
        canvas = np.full((6, 6, 3), 253, np.uint8)
        self.assertEqual(engine._mark_canvas_matching_cells(canvas), 3)
        self.assertTrue(engine.drawn_mask[0].all())
        self.assertFalse(engine.drawn_mask[1].any())

    def test_cell_with_one_wrong_pixel_is_still_drawn(self):
        engine = _engine()
        canvas = np.full((6, 6, 3), 255, np.uint8)
        canvas[2:4, :] = (200, 0, 0)
        canvas[3, 5] = (255, 255, 255)
        engine._mark_canvas_matching_cells(canvas)
        self.assertEqual(engine.drawn_mask[1].tolist(), [True, True, False])

    def test_partial_capture_leaves_uncovered_cells(self):
        engine = _engine()
        canvas = np.full((6, 3, 3), 255, np.uint8)
        engine._mark_canvas_matching_cells(canvas)
        self.assertEqual(engine.drawn_mask[0].tolist(), [True, False, False])

    def test_repair_finds_spill_on_skipped_cells_only(self):
        engine = _engine(brush=1)
        engine._mark_canvas_matching_cells(np.full((3, 3, 3), 255, np.uint8))
        white = np.full((3, 3, 3), 255.0)
        expected = white.copy()
        expected[1] = (200, 0, 0)
        final = expected.copy()
        final[0, 1] = (0, 0, 0)  # a dark stroke spilled onto one skipped white cell
        target = engine.cluster_map != 2
        result = engine._analyze_post_draw_repair_samples(white, final, expected, target)
        self.assertEqual(np.argwhere(result["hole_mask"]).tolist(), [[0, 1]])
        self.assertEqual(result["unverifiable_cells"], 0)

    def test_setting_round_trips_through_config(self):
        engine = _engine()
        self.assertTrue(engine.get_config()["skip_matching_canvas"])
        engine.load_config({"skip_matching_canvas": False})
        self.assertFalse(engine.skip_matching_canvas)


if __name__ == "__main__":
    unittest.main()
