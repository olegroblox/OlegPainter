"""ORDER-DETAILS-001: «Мелкие детали последними» keeps thin colours visible
when the program's brush is wider than a picture cell."""
import numpy as np
from scipy import ndimage

from engine.olegpainter.core import OlegPainter


def _engine_with_lines():
    engine = OlegPainter()
    cluster_map = np.zeros((40, 40), dtype=int)       # 0: dark background
    cluster_map[20, 4:36] = 1                          # 1: thin light line
    cluster_map[4:36, 20] = 1
    engine.cluster_map = cluster_map
    entries = []
    for cid, rgb in ((0, (20, 20, 60)), (1, (250, 240, 120))):
        entries.append(dict(hex="#%02X%02X%02X" % rgb, id=cid, count=int((cluster_map == cid).sum()),
                            rgb=rgb, luminance=engine._palette_lightness(rgb)))
    return engine, cluster_map, entries


def _survival(cluster_map, order, radius):
    """Share of each colour left after stamping its cells with a disk of `radius` cells."""
    canvas = np.full(cluster_map.shape, -1)
    r = int(np.ceil(radius))
    y, x = np.ogrid[-r:r + 1, -r:r + 1]
    footprint = x * x + y * y <= radius * radius
    for cid in order:
        canvas[ndimage.binary_dilation(cluster_map == cid, structure=footprint)] = cid
    return {cid: float((canvas[cluster_map == cid] == cid).mean()) for cid in order}


def test_thin_colours_are_drawn_after_large_areas():
    engine, cluster_map, entries = _engine_with_lines()
    engine.tone_sequence = "light_to_dark"
    lightness_order = [e["id"] for e in engine._sort_palette_entries(entries)]
    assert lightness_order == [1, 0]  # the light line first: the background then covers it
    engine.tone_sequence = "details_last"
    details_order = [e["id"] for e in engine._sort_palette_entries(entries)]
    assert details_order == [0, 1]
    # With a brush 3 cells wide the line vanishes in the lightness order and survives here.
    assert _survival(cluster_map, lightness_order, 1.5)[1] < 0.1
    assert _survival(cluster_map, details_order, 1.5)[1] == 1.0


def test_setting_accepts_the_new_order_and_falls_back_without_a_map():
    engine, _map, entries = _engine_with_lines()
    engine.drawing_enabled = False
    assert engine.set_tone_sequence("details_last")
    assert engine.tone_sequence == "details_last"
    engine.cluster_map = None  # before preparation: lightness order is kept
    assert [e["id"] for e in engine._sort_palette_entries(entries)] == [1, 0]
