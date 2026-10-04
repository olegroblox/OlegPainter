import numpy as np
import pytest

from tools.verify_live_canvas import compare_canvas


def fixture_images():
    expected = np.full((12, 16, 3), 255, dtype=np.uint8)
    expected[2:10, 3:13] = (0, 0, 0)
    expected[4:8, 5:9] = (255, 255, 255)
    actual = np.full((40, 50, 3), 255, dtype=np.uint8)
    actual[10:22, 20:36] = expected
    return actual, expected


def test_saved_canvas_matches_without_claiming_screen_position():
    actual, expected = fixture_images()
    result = compare_canvas(actual, expected)
    assert result["matched"]
    assert result["origin_canvas_px"] == [20, 10]
    assert result["alignment"] == "inferred_from_ink"
    assert not result["screen_position_verified"]


@pytest.mark.parametrize("defect", ["missing", "hole", "wrong_color", "stray", "shift"])
def test_rejects_incomplete_or_changed_drawing(defect):
    actual, expected = fixture_images()
    if defect == "missing":
        actual[12, 23] = 255
    elif defect == "hole":
        actual[15, 26] = 0
    elif defect == "wrong_color":
        actual[12, 23] = (0, 0, 20)
    elif defect == "stray":
        actual[30, 40] = 0
    elif defect == "shift":
        actual = np.roll(actual, 1, axis=1)
    assert not compare_canvas(actual, expected, origin=(20, 10))["matched"]


def test_empty_or_clipped_canvas_cannot_pass():
    actual, expected = fixture_images()
    with pytest.raises(ValueError, match="non-background"):
        compare_canvas(np.full_like(actual, 255), expected)
    with pytest.raises(ValueError, match="fit inside"):
        compare_canvas(actual, expected, origin=(45, 35))
