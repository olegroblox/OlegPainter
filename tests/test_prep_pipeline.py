import numpy as np

from engine.olegpainter.prep_pipeline import (
    alpha_aware_normalize,
    majority_vote_brush_grid,
    bilateral_preprocess,
    perceptual_kmeans_quantize,
)


def test_alpha_aware_normalize_respects_transparency():
    rgba = np.array(
        [
            [[255, 0, 0, 255], [0, 255, 0, 0]],
            [[0, 0, 255, 255], [10, 20, 30, 5]],
        ],
        dtype=np.uint8,
    )
    rgb, mask = alpha_aware_normalize(rgba, background_rgb=(255, 255, 255), alpha_threshold=10)
    assert rgb.shape == (2, 2, 3)
    assert mask.dtype == bool
    assert bool(mask[0, 0]) is True
    assert bool(mask[0, 1]) is False
    assert np.array_equal(rgb[0, 1], np.array([255, 255, 255], dtype=np.uint8))


def test_majority_vote_brush_grid_prefers_dominant_label():
    label_map = np.array(
        [
            [1, 1, 2, 2],
            [1, 2, 2, 2],
            [3, 3, 4, 4],
            [3, 3, 4, 4],
        ],
        dtype=np.int32,
    )
    reduced = majority_vote_brush_grid(label_map, brush_size=2)
    assert reduced.shape == (2, 2)
    assert reduced[0, 0] == 1
    assert reduced[0, 1] == 2
    assert reduced[1, 0] == 3
    assert reduced[1, 1] == 4


def test_perceptual_kmeans_quantize_returns_consistent_shapes():
    img = np.array(
        [
            [[250, 20, 20], [240, 30, 30], [20, 240, 20], [30, 250, 30]],
            [[245, 25, 25], [235, 35, 35], [25, 235, 25], [35, 245, 35]],
        ],
        dtype=np.uint8,
    )
    labels, centers, palette_rgb = perceptual_kmeans_quantize(
        img,
        k=2,
        color_space="oklab",
        mode="minibatch",
        sample_cap=4,
    )
    assert labels.shape == img.shape[:2]
    assert centers.shape == (2, 3)
    assert palette_rgb.shape == (2, 3)


def test_bilateral_preprocess_preserves_shape():
    img = np.zeros((4, 4, 3), dtype=np.uint8)
    filtered = bilateral_preprocess(img)
    assert filtered.shape == img.shape
