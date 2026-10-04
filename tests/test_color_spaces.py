import numpy as np

from engine.olegpainter.color_spaces import (
    rgb_to_cielab_numpy,
    cielab_to_rgb_numpy,
    rgb_to_oklab_numpy,
    oklab_to_rgb_numpy,
    perceptual_distance,
    lightness,
)


def test_lab_round_trip_is_close():
    rgb = np.array(
        [
            [0, 0, 0],
            [255, 255, 255],
            [255, 0, 0],
            [0, 255, 0],
            [0, 0, 255],
            [123, 45, 67],
            [12, 200, 180],
        ],
        dtype=np.uint8,
    )
    lab = rgb_to_cielab_numpy(rgb)
    restored = cielab_to_rgb_numpy(lab)
    assert restored.shape == rgb.shape
    assert np.max(np.abs(restored.astype(int) - rgb.astype(int))) <= 2


def test_oklab_round_trip_is_close():
    rgb = np.array(
        [
            [10, 20, 30],
            [64, 128, 192],
            [200, 150, 40],
            [250, 250, 10],
        ],
        dtype=np.uint8,
    )
    oklab = rgb_to_oklab_numpy(rgb)
    restored = oklab_to_rgb_numpy(oklab)
    assert restored.shape == rgb.shape
    assert np.max(np.abs(restored.astype(int) - rgb.astype(int))) <= 2


def test_perceptual_distance_and_lightness_are_reasonable():
    black = np.array([0, 0, 0], dtype=np.uint8)
    white = np.array([255, 255, 255], dtype=np.uint8)
    red = np.array([255, 0, 0], dtype=np.uint8)
    assert perceptual_distance(black, black, space="lab") == 0.0
    assert perceptual_distance(black, white, space="lab") == perceptual_distance(white, black, space="lab")
    assert lightness(white, space="lab") > lightness(black, space="lab")
    assert perceptual_distance(red, white, space="oklab") > 0.0
