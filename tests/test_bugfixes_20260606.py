# -*- coding: utf-8 -*-
"""Regression tests for the 2026-06-06 bug-fix batch (fill-path + new bugs)."""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication  # noqa: E402
from PIL import Image  # noqa: E402
from ui.services.painter_service import PainterService  # noqa: E402
from ui.i18n import tr  # noqa: E402
from engine.olegpainter.path_planning import astar_bridge  # noqa: E402

_app = QApplication.instance() or QApplication([])


class FillPathFixes(unittest.TestCase):
    def setUp(self):
        self.svc = PainterService()
        self.e = self.svc.engine

    def tearDown(self):
        self.svc.shutdown(); self.svc.deleteLater(); _app.processEvents()

    def test_astar_failure_returns_none_success_returns_list(self):
        passable = np.ones((5, 5), dtype=bool)
        # success -> list of intermediates
        self.assertIsInstance(astar_bridge((0, 0), (0, 4), passable), list)
        # failure (walled off) -> None so the caller can teleport, not draw a stray line
        walled = np.ones((5, 5), dtype=bool); walled[:, 2] = False
        self.assertIsNone(astar_bridge((0, 0), (0, 4), walled))

    def test_dfs_traversal_treats_drawn_cells_as_walls(self):
        # a 1x5 row; cell (0,2) already drawn -> a wall the walk must not cross
        mask = np.full((1, 5), 255, dtype=np.uint8)
        self.e.drawn_mask = np.zeros((1, 5), dtype=bool)
        self.e.drawn_mask[0, 2] = True
        order = set(self.e._dfs_traversal_order(mask, 0, 0))
        self.assertIn((0, 0), order)
        self.assertIn((0, 1), order)
        self.assertNotIn((0, 2), order, "drawn cell must be a wall")
        self.assertNotIn((0, 3), order, "cells behind the drawn wall are unreachable")
        self.assertNotIn((0, 4), order)


class ClipboardSourcePreserved(unittest.TestCase):
    def setUp(self):
        self.svc = PainterService()
        self.e = self.svc.engine

    def tearDown(self):
        self.svc.shutdown(); self.svc.deleteLater(); _app.processEvents()

    def test_profile_switch_keeps_clipboard_image(self):
        # Simulate a pasted clipboard image: pixels in RAM, IMAGE_PATH = placeholder label.
        img = Image.new("RGB", (8, 8), (200, 100, 50))
        self.e.source_pil_image = img
        self.e.IMAGE_PATH = tr("clipboard_source_name")
        # Switching place/profile must NOT drop the in-memory clipboard image (the crash).
        self.svc.apply_profile_state({"drawing_algorithm": "dfs_4dir"})
        self.assertIsNotNone(
            getattr(self.e, "source_pil_image", None),
            "clipboard image must survive a profile/place switch",
        )


if __name__ == "__main__":
    unittest.main()
