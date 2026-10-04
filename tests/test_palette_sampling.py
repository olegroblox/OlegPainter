from dataclasses import replace
import json
import sys
from threading import Event

import numpy as np
import pytest

from infrastructure.automation_target import WindowIdentity
from infrastructure.palette_sampling import (
    PaletteSampleRequest, palette_from_frame, sample_grid, sample_window_palette, without_panel_background,
)
from infrastructure.window_sampling import WindowSampleError


@pytest.fixture
def palette_request():
    return PaletteSampleRequest(WindowIdentity(12, 13, 14, "Test"), (-10, -10, 30, 30),
                                (-10, -10, 30, 30), (0, 0), (-4, -3, 8, 8))


def frame():
    result = np.full((40, 40, 4), 255, dtype=np.uint8)
    result[:, :, :3] = [11, 22, 33]
    return result


def test_palette_exact_rgb_and_negative_coordinates(palette_request):
    result = palette_from_frame(frame(), palette_request)
    assert result["coords"][0] == [-4, -3]
    assert result["coords"][-1] == [3, 4]
    assert result["rgb"] == [[33, 22, 11]] * 64
    assert result["slider"] is None


@pytest.mark.parametrize("region", [(0, 0, 4, 30000), (0, 0, 30000, 4), (-4000, 0, 7680, 4320)])
def test_sampling_stays_bounded_and_inside_region(region):
    coords = sample_grid(region)
    x, y, w, h = region
    assert 0 < len(coords) <= 3000
    assert np.all((coords[:, 0] >= x) & (coords[:, 0] < x + w))
    assert np.all((coords[:, 1] >= y) & (coords[:, 1] < y + h))


@pytest.mark.parametrize("rect", [(-11, 0, 4, 4), (28, 0, 4, 4), (0, 0, 2, 4)])
def test_outside_or_small_region_rejected_without_clamping(palette_request, rect):
    with pytest.raises(WindowSampleError):
        palette_from_frame(frame(), replace(palette_request, palette=rect))


@pytest.mark.parametrize("vertical,reverse", [(True, False), (True, True), (False, False), (False, True)])
def test_slider_direction_and_endpoints_are_inside_frame(palette_request, vertical, reverse):
    rect = (10, 10, 4, 12) if vertical else (10, 10, 12, 4)
    data = frame()
    values = np.linspace(0, 255, 12).astype(np.uint8)
    if reverse:
        values = values[::-1]
    gradient = values[:, None, None] if vertical else values[None, :, None]
    data[20:20 + rect[3], 20:20 + rect[2], :3] = gradient
    result = palette_from_frame(data, replace(palette_request, slider=rect))
    assert result["slider"] == ["vertical" if vertical else "horizontal", 12,
                                 10 if reverse else 21, 21 if reverse else 10]


def test_flat_slider_and_transparent_palette_rejected(palette_request):
    with pytest.raises(WindowSampleError, match="светлый"):
        palette_from_frame(frame(), replace(palette_request, slider=(10, 10, 4, 12)))
    data = frame()
    data[7, 6, 3] = 0
    with pytest.raises(WindowSampleError, match="прозрач"):
        palette_from_frame(data, palette_request)


@pytest.mark.parametrize("corrupt", [None, "rgb", "coords", "slider"])
def test_palette_child_protocol_validates_result(palette_request, corrupt):
    result = palette_from_frame(frame(), palette_request)
    if corrupt == "rgb":
        result["rgb"][0][0] = True
    elif corrupt == "coords":
        result["coords"][0][0] += 1
    elif corrupt == "slider":
        result["slider"] = ["vertical", 1, 2, 3]
    command = [sys.executable, "-c", "import sys; sys.stdin.read(); print(" + repr(json.dumps(result)) + ")"]
    if corrupt:
        with pytest.raises(WindowSampleError):
            sample_window_palette(palette_request, Event(), command=command)
    else:
        assert sample_window_palette(palette_request, Event(), command=command) == result


def test_panel_between_swatches_is_not_a_colour(palette_request):
    # Live Paint (dark theme) 2026-10-01: the toolbar between the 20 swatches was
    # most of the frame and a valid near-black "colour" to click.
    data = frame()
    data[:, :, :3] = [32, 32, 32]
    colours = ([0, 0, 255], [0, 255, 0], [255, 0, 0], [0, 0, 0])
    for i, bgr in enumerate(colours):
        data[12:16, 12 + i * 5:16 + i * 5, :3] = bgr
    result = palette_from_frame(data, replace(palette_request, palette=(0, 0, 23, 8)))
    keep = without_panel_background(np.asarray(result["rgb"]), np.asarray(result["coords"]))
    kept = np.asarray(result["rgb"])[keep].tolist()
    assert [32, 32, 32] not in kept
    assert {tuple(c) for c in kept} == {(255, 0, 0), (0, 255, 0), (0, 0, 255), (0, 0, 0)}


def test_gradient_picker_keeps_every_sample():
    xs, ys = np.meshgrid(np.arange(60), np.arange(50))
    coords = np.column_stack((xs.ravel(), ys.ravel()))
    rgb = np.column_stack((xs.ravel() * 4, ys.ravel() * 5, np.full(xs.size, 128)))
    assert without_panel_background(rgb, coords).all()
