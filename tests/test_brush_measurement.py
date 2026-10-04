"""Actual Paint regression and independent geometric measurement checks."""
from pathlib import Path
import math

import cv2
import numpy as np
from PIL import Image
import pytest

from engine.olegpainter.brush_measurement import measure_stamp


def test_paint_tiny_stamp_is_not_cursor_halo():
    root = Path(__file__).parent / "fixtures" / "brush_measurement"
    with Image.open(root / "tiny-before.png") as before, Image.open(root / "tiny-after.png") as after:
        probe = measure_stamp(before, after, anchor=(155, 155))
    # Independent black-on-white coverage of the central Paint mark is 5.8px².
    # The old morphology path erased it and returned radius 211.23 from the halo.
    assert probe is not None
    assert probe["area_px"] == pytest.approx(5.8)
    assert probe["radius_px"] == pytest.approx(math.sqrt(5.8 / math.pi))


@pytest.mark.parametrize("opacity", [1, .5, .2])
def test_uniform_opacity_does_not_change_antialiased_geometry(opacity):
    scale, radius = 8, 20
    coverage = np.zeros((120 * scale, 120 * scale), dtype=np.uint8)
    cv2.circle(coverage, (60 * scale, 60 * scale), radius * scale, 255, -1)
    coverage = cv2.resize(coverage, (120, 120), interpolation=cv2.INTER_AREA)
    canvas = np.repeat((255 - (coverage * opacity).astype(np.uint8))[:, :, None], 3, axis=2)
    probe = measure_stamp(Image.new("RGB", (120, 120), "white"), Image.fromarray(canvas))
    assert probe is not None
    assert probe["radius_px"] == pytest.approx(radius, abs=.3)


@pytest.mark.parametrize("mark", ["edge", "offcentre", "clipped", "unchanged"])
def test_unreliable_capture_is_rejected(mark):
    canvas = np.full((120, 120, 3), 255, np.uint8)
    if mark == "edge":
        canvas[:6, :6] = 0
    elif mark == "offcentre":
        cv2.circle(canvas, (20, 20), 8, (0, 0, 0), -1)
    elif mark == "clipped":
        cv2.circle(canvas, (60, 60), 65, (0, 0, 0), -1)
    assert measure_stamp(Image.new("RGB", (120, 120), "white"), Image.fromarray(canvas)) is None


def test_changed_edge_does_not_erase_one_pixel_stamp():
    canvas = np.full((120, 120, 3), 255, np.uint8)
    canvas[:6, :6] = 0
    canvas[60, 60] = 0
    probe = measure_stamp(Image.new("RGB", (120, 120), "white"), Image.fromarray(canvas))
    assert probe is not None
    assert probe["area_px"] == 1
