"""DFS backtracking must not connect branches through a different colour."""
import numpy as np
import pytest

from helpers.sim_pen import engine_for


@pytest.mark.parametrize("shape", ["ring", "fork", "comb"])
def test_unmerged_dfs_covers_shape_without_cutting_across_gaps(shape):
    mask = np.zeros((14, 17), dtype=np.uint8)
    if shape == "ring":
        mask[1:13, 1:16] = 255
        mask[3:11, 3:14] = 0
    elif shape == "fork":
        mask[2:12, 2] = 255
        mask[2, 2:15] = 255
        mask[11, 2:15] = 255
    else:
        mask[2, 2:15] = 255
        mask[2:12, 2:15:3] = 255
    engine, pen = engine_for(mask, runlen=False, astar=False)
    engine.dfs_4dir_fill_current_color(mask, 0, 0, 1)
    assert pen.missing(mask) == 0
    assert pen.stray(mask) == 0
    assert not pen.down
    assert np.array_equal(engine.drawn_mask, mask == 255)


def test_unmerged_dfs_releases_pen_when_drawing_fails():
    mask = np.full((4, 4), 255, dtype=np.uint8)
    engine, pen = engine_for(mask, runlen=False, astar=False)

    def broken_stroke(*args, **kwargs):
        raise RuntimeError("drawing failed")

    engine.draw_line = broken_stroke
    with pytest.raises(RuntimeError, match="drawing failed"):
        engine.dfs_4dir_draw(mask, 0, 0, 0, 0)
    assert not pen.down


def test_nearest_order_available_through_common_settings():
    from application.settings import SETTINGS
    from engine.olegpainter.core import OlegPainter
    setting = SETTINGS["area_sequence"]
    assert "nearest" in dict(setting.options)
    engine = OlegPainter()
    engine.set_area_sequence("nearest")
    assert engine.get_config()["area_sequence"] == "nearest"
