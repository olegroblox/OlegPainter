"""Drawing routes «Прямые отрезки», «Только контур», «Контур и заливка» (GARTIC-003)."""
import numpy as np

from engine.olegpainter.line_cover import cover_segments, order_segments, segment_cells
from helpers.sim_pen import engine_for


def _blob(seed=3, shape=(40, 50)):
    rng = np.random.default_rng(seed)
    m = np.zeros(shape, bool)
    m[5:30, 6:40] = True
    m[12:18, 10:25] = False                       # a hole
    m[34:38, 2:48] = rng.random((4, 46)) > 0.3    # ragged strip
    return m


def _painted(segments, shape):
    out = np.zeros(shape, bool)
    for (r0, c0), (r1, c1) in segments:
        out[min(r0, r1):max(r0, r1) + 1, min(c0, c1):max(c0, c1) + 1] = True
    return out


def test_segments_cover_exactly_the_colour_with_fewest_runs():
    for seed in range(5):
        m = _blob(seed)
        segs = cover_segments(m)
        assert (_painted(segs, m.shape) == m).all()
        for (r0, c0), (r1, c1) in segs:
            assert r0 == r1 or c0 == c1        # straight only
        rows = int((m & ~np.pad(m, ((0, 0), (1, 0)))[:, :-1]).sum())
        cols = int((m & ~np.pad(m, ((1, 0), (0, 0)))[:-1, :]).sum())
        assert len(segs) <= min(rows, cols)    # never worse than all rows or all columns


def test_single_line_colours_need_one_direction_only():
    for m in (np.zeros((9, 5), bool), np.zeros((5, 9), bool), np.zeros((4, 4), bool)):
        if m.shape[0] > m.shape[1]:
            m[:, 2] = True          # one vertical line: no horizontal run kept
        elif m.shape[1] > m.shape[0]:
            m[2, :] = True
        else:
            m[1, 1] = True          # a single dot
        segs = cover_segments(m)
        assert len(segs) == 1 and (_painted(segs, m.shape) == m).all()


def test_ordering_keeps_every_segment_and_cells_follow_them():
    m = _blob(1)
    segs = cover_segments(m)
    ordered = order_segments(segs, (0, 0))
    assert sorted(map(sorted, ordered)) == sorted(map(sorted, segs))
    cells = segment_cells(ordered)
    assert set(cells) == set(zip(*np.nonzero(m)))


def test_line_cover_route_paints_every_cell_and_nothing_else():
    m = _blob(2)
    mask = m.astype(np.uint8) * 255
    e, pen = engine_for(mask, runlen=True, astar=False)
    e.drawing_algorithm = "line_cover"
    e._draw_cluster_with_algorithm(1, 0, 0)
    assert pen.missing(mask) == 0 and pen.stray(mask) == 0
    assert e.drawn_mask[m].all()


def test_outline_only_draws_the_edge_and_counts_the_inside_done():
    m = np.zeros((20, 20), bool)
    m[3:17, 4:16] = True
    mask = m.astype(np.uint8) * 255
    e, pen = engine_for(mask, runlen=True, astar=False)
    e.drawing_algorithm = "outline_only"
    e._draw_cluster_with_algorithm(1, 0, 0)
    ring = m.copy()
    ring[4:16, 5:15] = False
    assert (pen.canvas == ring).all()
    assert e.drawn_mask[m].all()


def test_outline_and_fill_rings_clicks_deep_inside_and_repairs_misses():
    m = np.zeros((30, 30), bool)
    m[2:28, 2:28] = True
    m[10:12, 20:22] = False                       # something else inside
    mask = m.astype(np.uint8) * 255
    e, pen = engine_for(mask, runlen=True, astar=False)
    e.drawing_algorithm = "outline_and_fill"
    e.color_palette = [("#000000", 1, int(m.sum()), (0, 0, 0))]
    tools, clicks = [], []
    e._outline_fill_select_tool = lambda tool: tools.append(tool) or True
    e._click_abs = lambda x, y: clicks.append((x, y))
    e._input_click = lambda **k: None
    ring, inner = e._outline_ring(m)
    missed = np.zeros_like(m)
    missed[20, 5] = True                          # the bucket left one cell
    e._bucket_missed_cells = lambda filled, *a: missed & filled
    e._draw_cluster_with_algorithm(1, 0, 0)
    assert tools == ["brush", "fill", "brush"]
    assert len(clicks) == 1
    cx, cy = clicks[0]
    assert inner[cy, cx] and not ring[cy, cx]     # deep inside, never on the edge
    assert pen.canvas[ring].all()                 # the edge is drawn by the brush
    assert pen.canvas[20, 5]                      # the missed cell is repaired
    assert pen.stray(mask) == 0
    assert e.drawn_mask[m].all()


def test_small_regions_are_simply_drawn():
    m = np.zeros((12, 12), bool)
    m[2:6, 2:6] = True                            # 16 cells: no bucket
    mask = m.astype(np.uint8) * 255
    e, pen = engine_for(mask, runlen=True, astar=False)
    e.drawing_algorithm = "outline_and_fill"
    e.color_palette = [("#000000", 1, 16, (0, 0, 0))]
    tools = []
    e._outline_fill_select_tool = lambda tool: tools.append(tool) or True
    e._draw_cluster_with_algorithm(1, 0, 0)
    assert tools == ["brush"] and pen.missing(mask) == 0


def test_old_outline_and_paint_setting_becomes_dfs():
    from application.place_catalog import resolve_algorithm
    from engine.olegpainter.core import OlegPainter
    e = OlegPainter(status_callback=lambda m: None)
    e.load_config({"drawing_algorithm": "outline_and_paint"})
    assert e.drawing_algorithm == "dfs_4dir"
    assert resolve_algorithm("outline_and_paint") == "dfs_4dir"


def test_line_cover_handles_every_colour_of_a_noisy_picture():
    rng = np.random.default_rng(7)
    labels = rng.integers(0, 6, (24, 30))
    labels[:, 10] = 9                             # a colour that is one vertical line
    labels[5, :] = 8                              # and one horizontal line
    for colour in np.unique(labels):
        m = labels == colour
        mask = m.astype(np.uint8) * 255
        e, pen = engine_for(mask, runlen=True, astar=False)
        e.drawing_algorithm = "line_cover"
        e._draw_cluster_with_algorithm(1, 0, 0)
        assert pen.missing(mask) == 0 and pen.stray(mask) == 0, colour
