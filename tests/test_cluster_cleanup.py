import numpy as np

from engine.olegpainter.cluster_cleanup import apply_cleanup_mode, vectorized_cleanup, smart_stray_merge


def test_vectorized_cleanup_removes_tiny_island_and_fills_hole():
    label_map = np.array(
        [
            [1, 1, 1, 1, 1],
            [1, 1, 0, 1, 1],
            [1, 2, 2, 1, 1],
            [1, 1, 1, 1, 1],
            [1, 1, 1, 1, 1],
        ],
        dtype=np.int32,
    )
    cleaned = vectorized_cleanup(label_map, min_area=3, fill_holes=True, merge_strays=False)
    assert cleaned[1, 2] == 1
    assert np.all(cleaned == 1)


def test_smart_stray_merge_prefers_closest_neighbor_color():
    label_map = np.array(
        [
            [1, 1, 1, 2, 2],
            [1, 3, 3, 2, 2],
            [1, 3, 3, 2, 2],
            [1, 1, 1, 2, 2],
        ],
        dtype=np.int32,
    )
    center_colors = {
        1: np.array([10.0, 0.0, 0.0]),
        2: np.array([11.0, 0.2, 0.0]),
        3: np.array([11.2, 0.1, 0.0]),
    }
    merged = smart_stray_merge(label_map, center_colors, min_area=5, space="lab")
    assert np.all(merged[1:3, 1:3] == 2)


def test_apply_cleanup_mode_vectorized_does_not_backgroundize_small_color_region():
    label_map = np.array(
        [
            [1, 1, 1, 2, 2],
            [1, 3, 3, 2, 2],
            [1, 3, 3, 2, 2],
            [1, 1, 1, 2, 2],
        ],
        dtype=np.int32,
    )
    rgb = np.zeros((4, 5, 3), dtype=np.uint8)
    rgb[label_map == 1] = (255, 0, 0)
    rgb[label_map == 2] = (250, 0, 0)
    rgb[label_map == 3] = (252, 0, 0)

    cleaned, meta = apply_cleanup_mode(
        label_map,
        rgb,
        mode="vectorized",
        min_area=5,
        background=0,
        space="lab",
    )

    assert meta["changed_cells"] == 0
    assert np.all(cleaned[1:3, 1:3] == 3)
    assert not np.any(cleaned == 0)


def test_apply_cleanup_mode_vectorized_plus_stray_merge_merges_to_neighbor_not_background():
    label_map = np.array(
        [
            [1, 1, 1, 2, 2],
            [1, 3, 3, 2, 2],
            [1, 3, 3, 2, 2],
            [1, 1, 1, 2, 2],
        ],
        dtype=np.int32,
    )
    rgb = np.zeros((4, 5, 3), dtype=np.uint8)
    rgb[label_map == 1] = (255, 0, 0)
    rgb[label_map == 2] = (250, 0, 0)
    rgb[label_map == 3] = (252, 0, 0)

    cleaned, meta = apply_cleanup_mode(
        label_map,
        rgb,
        mode="vectorized_plus_stray_merge",
        min_area=5,
        background=0,
        space="lab",
    )

    assert meta["changed_cells"] == 4
    assert np.all(cleaned[1:3, 1:3] == 2)
    assert not np.any(cleaned == 0)


def test_cleanup_keeps_large_objects_enclosed_by_one_colour():
    # Диск на фоне неба: для неба он «дырка», но крупный — не мусор.
    label_map = np.full((60, 80), 1, dtype=np.int32)
    yy, xx = np.mgrid[:60, :80]
    label_map[(yy - 30) ** 2 + (xx - 30) ** 2 <= 15 ** 2] = 2
    label_map[5:20, 55:75] = 3
    label_map[30, 30] = 4  # одиночный мусор внутри диска
    rgb = np.zeros((60, 80, 3), dtype=np.float64)
    for label, colour in {1: (0.8, 0.0, 0.0), 2: (0.6, 0.1, 0.1), 3: (0.9, 0.0, 0.2), 4: (0.61, 0.1, 0.1)}.items():
        rgb[label_map == label] = colour
    cleaned, _meta = apply_cleanup_mode(label_map, rgb, mode="vectorized_plus_stray_merge", min_area=4,
                                        background=0, space="oklab", max_merge_distance=0.10)
    assert set(np.unique(cleaned)) == {1, 2, 3}
    assert np.count_nonzero(cleaned == 2) == np.count_nonzero(label_map == 2) + 1
    assert np.array_equal(cleaned == 3, label_map == 3)
