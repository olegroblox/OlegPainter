"""Speed probe for straight-stroke mode: the shortest pause that keeps every stroke."""
import numpy as np
from PIL import Image

from engine.olegpainter import input_timing


class _Engine:
    pen_split_strokes = False
    pen_stroke_gap = 0.0

    def __init__(self):
        self.drawn = []

    def _dynamic_brush_v2_clean_spots(self, zone, need, count):
        return [(100 + 200 * i, 100) for i in range(count)]

    def _grab_patch(self, cx, cy, half):
        return Image.new("RGB", (2 * half, 2 * half), "white")

    def _automation_cancelled(self):
        return False

    def _click_abs(self, x, y):
        pass


def test_probe_picks_the_first_pause_that_keeps_strokes_twice(monkeypatch):
    engine = _Engine()
    expected = input_timing.lift_pattern()
    half = input_timing._LIFT_PATTERN_PX // 2
    painted = {}

    def fill(eng, pattern, x0, y0, pause, dwell, step):
        assert eng.pen_split_strokes
        painted["gap"] = eng.pen_stroke_gap

    def settled(eng, cx, cy, need):
        img = np.full((2 * need, 2 * need, 3), 255, np.uint8)
        drawn = expected.copy()
        if painted["gap"] < 0.005:
            drawn[:, drawn.shape[1] // 2:] = False  # a burst of lost strokes, as seen live
        img[need - half:need - half + drawn.shape[0], need - half:need - half + drawn.shape[1]][drawn] = 0
        return Image.fromarray(img)

    monkeypatch.setattr(input_timing, "_fill_with_engine_route", fill)
    monkeypatch.setattr(input_timing, "_settled_patch", settled)
    result = input_timing.learn_stroke_gap(engine, (0, 0, 2000, 400))
    assert [t["ok"] for t in result["gap_trials"]] == [False, False, True, True]
    assert result["stroke_gap"] == round(0.005 * 1.5, 4)
    assert engine.pen_split_strokes is False and engine.pen_stroke_gap == 0.0  # restored


def test_probe_reports_nothing_when_strokes_are_invisible(monkeypatch):
    engine = _Engine()
    monkeypatch.setattr(input_timing, "_fill_with_engine_route", lambda *a: None)
    monkeypatch.setattr(input_timing, "_settled_patch", lambda eng, cx, cy, need: Image.new("RGB", (2 * need, 2 * need), "white"))
    assert input_timing.learn_stroke_gap(engine, (0, 0, 2000, 400)) is None
