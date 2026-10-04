# -*- coding: utf-8 -*-
"""Per-REGION semantic planes + the two-pass draw (region split, opt-in).

A "mixed" color whose connected regions sit in BOTH zones is painted twice:
its backdrop regions in the backdrop phase, its object regions in the object
phase. SimPen runs the REAL fill pipeline end-to-end via draw_colors_thread:
full coverage, zero stray ink, correct phase order, zero wasted pick_color
calls for colors that have nothing to paint in a phase.
"""
from __future__ import annotations

import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from helpers.sim_pen import SimPen  # noqa: E402

from engine.olegpainter.core import OlegPainter  # noqa: E402

H, W = 16, 16


def _mixed_engine():
    """Two colors on a 16x16 grid. Color 1 lives only in the LEFT (backdrop)
    half; color 2 has one region in each half — the classic mixed color the
    split exists for. Synthetic saliency: left half 0.1, right half 0.9."""
    e = OlegPainter(status_callback=lambda *_a, **_k: None)
    cm = np.full((H, W), -1, dtype=int)
    cm[2:7, 1:7] = 1                      # color 1: backdrop-only block
    cm[9:14, 1:7] = 2                     # color 2, region A (left = backdrop)
    cm[9:14, 9:15] = 2                    # color 2, region B (right = object)
    e.cluster_map = cm
    e.drawn_mask = np.zeros((H, W), bool)
    e.brush_size = 1
    e.draw_region = (0, 0, W, H)
    e.telemetry_enabled = False
    e.color_palette = [
        ("#111111", 1, int((cm == 1).sum()), (17, 17, 17)),
        ("#222222", 2, int((cm == 2).sum()), (34, 34, 34)),
    ]
    sal = np.full((H, W), 0.1, dtype=np.float32)
    sal[:, 8:] = 0.9
    e._semantic_saliency_cache = (id(cm), sal)   # injected as if computed
    pen = SimPen(e, H, W)
    # draw_colors_thread side quests that must not run headless:
    e._refresh_screen_metrics = lambda: None
    e._notify_post_draw_repair = lambda *_a, **_k: None
    e._run_post_draw_repair = lambda *_a, **_k: None
    e._record_drawing_duration = lambda: None
    picks = []
    e.pick_color = lambda hex_color, cluster_id=None: picks.append(hex_color)
    downs = []
    orig_down = pen.pen_down
    pen.pen_down = lambda: (downs.append(pen.pos), orig_down())[1]
    e._pen_down = pen.pen_down       # SimPen bound the original before the spy
    return e, pen, picks, downs


def test_split_draws_everything_in_two_phases_with_no_stray():
    e, pen, picks, downs = _mixed_engine()
    e.semantic_order_mode = "bg_first"
    e.semantic_region_split_enabled = True
    e.draw_colors_thread()

    mask = np.where(e.cluster_map != -1, 255, 0)
    assert pen.missing(mask) == 0, "two-pass draw must cover every target cell"
    assert pen.stray(mask) == 0, "no ink outside the clusters"

    # phase order: every backdrop (left-half) press strictly before any
    # object (right-half) press — color 2's region B waits for phase 1
    sides = ["L" if x < 8 else "R" for x, _y in downs]
    assert "R" in sides and "L" in sides
    assert sides.index("R") == len(sides) - sides[::-1].index("L"), \
        f"object regions painted before the backdrop finished: {sides}"

    # color 1 has nothing in the object phase -> exactly 3 picks, not 4
    assert picks == ["#111111", "#222222", "#222222"]


def test_objects_first_flips_the_phase_order():
    e, pen, picks, downs = _mixed_engine()
    e.semantic_order_mode = "objects_first"
    e.semantic_region_split_enabled = True
    e.draw_colors_thread()

    mask = np.where(e.cluster_map != -1, 255, 0)
    assert pen.missing(mask) == 0 and pen.stray(mask) == 0
    sides = ["L" if x < 8 else "R" for x, _y in downs]
    assert sides.index("L") == len(sides) - sides[::-1].index("R"), \
        f"backdrop painted before the object finished: {sides}"
    assert picks == ["#222222", "#111111", "#222222"]


def test_split_off_is_single_pass_legacy_order():
    e, pen, picks, _downs = _mixed_engine()
    e.semantic_order_mode = "bg_first"
    e.semantic_region_split_enabled = False     # the default
    e.draw_colors_thread()
    mask = np.where(e.cluster_map != -1, 255, 0)
    assert pen.missing(mask) == 0 and pen.stray(mask) == 0
    assert picks == ["#111111", "#222222"], "one pass, one pick per color"


def test_split_with_mode_off_never_activates():
    e, pen, picks, _downs = _mixed_engine()
    e.semantic_order_mode = "off"
    e.semantic_region_split_enabled = True      # split alone is not enough
    assert e._semantic_split_phases() is None
    e.draw_colors_thread()
    assert picks == ["#111111", "#222222"]


def test_region_mask_classifies_mixed_color_per_region():
    e, _pen, _picks, _downs = _mixed_engine()
    e.semantic_region_split_enabled = True
    mask = e._semantic_region_planes_mask()
    assert mask is not None
    cm = e.cluster_map
    assert set(np.unique(mask[cm == 1])) == {0}, "left-only color = backdrop"
    assert set(np.unique(mask[9:14, 1:7])) == {0}, "left region of color 2 = backdrop"
    assert set(np.unique(mask[9:14, 9:15])) == {1}, "right region = object"
    assert set(np.unique(mask[cm == -1])) == {-1}, "outside stays -1"


def test_region_mask_without_saliency_falls_back_to_color_planes():
    """No AI model -> every region inherits its COLOR's plane (the split
    degrades to exactly the per-color behaviour, never something new)."""
    e = OlegPainter(status_callback=lambda *_a, **_k: None)
    cm = np.zeros((40, 40), dtype=int)          # frame-touching backdrop
    cm[12:28, 12:28] = 1                        # central object
    e.cluster_map = cm
    e.semantic_region_split_enabled = True
    planes = e._semantic_cluster_planes()
    assert planes == {0: 0, 1: 1}
    mask = e._semantic_region_planes_mask()
    assert mask is not None
    assert set(np.unique(mask[cm == 0])) == {0}
    assert set(np.unique(mask[cm == 1])) == {1}


def test_region_mask_cache_follows_threshold_and_model():
    e, _pen, _picks, _downs = _mixed_engine()
    e.semantic_region_split_enabled = True
    m1 = e._semantic_region_planes_mask()
    assert m1 is not None
    assert e._semantic_region_planes_mask() is m1, "same key -> cached object"
    e.semantic_threshold = 0.95                 # right half (0.9) flips to backdrop
    m2 = e._semantic_region_planes_mask()
    assert m2 is None, "single plane after the flip -> split disables itself"
    e.semantic_threshold = 0.35
    assert e._semantic_region_planes_mask() is not m1, "key change -> rebuilt"


def test_preview_uses_region_mask_when_split_enabled():
    e, _pen, _picks, _downs = _mixed_engine()
    e.semantic_region_split_enabled = True
    img = e.build_semantic_planes_preview()
    assert img is not None and img.mode == "RGBA"
    arr = np.asarray(img)
    # color 2's LEFT region tinted as backdrop, RIGHT region as object
    assert tuple(arr[10, 3]) == e.SEMANTIC_ZONE_COLORS[0]
    assert tuple(arr[10, 10]) == e.SEMANTIC_ZONE_COLORS[1]
    assert int(arr[0, 0, 3]) == 0               # background transparent
