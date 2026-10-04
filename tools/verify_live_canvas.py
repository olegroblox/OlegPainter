"""Compare a saved, opaque test canvas with a prepared image; no device access.

The canvas must otherwise contain only the specified background. Without --origin,
alignment uses non-background bounding boxes and CANNOT verify screen positioning.
Use the target app's saved PNG, not a screenshot affected by cursor/overlays/zoom.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image


def compare_canvas(actual, expected, *, background=(255, 255, 255), origin=None):
    actual = np.asarray(actual)
    expected = np.asarray(expected)
    for name, pixels in (("actual", actual), ("expected", expected)):
        if pixels.ndim != 3 or pixels.shape[2] != 3 or pixels.dtype != np.uint8:
            raise ValueError(f"{name} must be an RGB uint8 image")
    bg = np.asarray(background, dtype=np.uint8)
    actual_ink = np.any(actual != bg, axis=2)
    expected_ink = np.any(expected != bg, axis=2)
    if not actual_ink.any() or not expected_ink.any():
        raise ValueError("Both images must contain non-background pixels")
    inferred = origin is None
    if inferred:
        ay, ax = np.where(actual_ink)
        ey, ex = np.where(expected_ink)
        origin = (int(ax.min() - ex.min()), int(ay.min() - ey.min()))
    x, y = map(int, origin)
    height, width = expected.shape[:2]
    if x < 0 or y < 0 or x + width > actual.shape[1] or y + height > actual.shape[0]:
        raise ValueError("Expected image does not fit inside the saved canvas at this origin")
    region = actual[y:y + height, x:x + width]
    mismatches = int(np.any(region != expected, axis=2).sum())
    outside = int(actual_ink.sum() - actual_ink[y:y + height, x:x + width].sum())
    return dict(
        matched=mismatches == 0 and outside == 0,
        alignment="inferred_from_ink" if inferred else "explicit_canvas_origin",
        origin_canvas_px=[x, y],
        screen_position_verified=False,
        actual_size=[actual.shape[1], actual.shape[0]],
        expected_size=[width, height],
        actual_non_background_pixels=int(actual_ink.sum()),
        expected_non_background_pixels=int(expected_ink.sum()),
        mismatched_pixels=mismatches,
        outside_non_background_pixels=outside,
    )


def read_opaque_rgb(path):
    with Image.open(path) as image:
        rgba = np.asarray(image.convert("RGBA"))
    if not np.all(rgba[:, :, 3] == 255):
        raise ValueError(f"Transparent images are not supported: {path}")
    return rgba[:, :, :3]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("actual", type=Path)
    parser.add_argument("expected", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--origin", type=int, nargs=2, metavar=("X", "Y"))
    args = parser.parse_args()
    result = compare_canvas(read_opaque_rgb(args.actual), read_opaque_rgb(args.expected), origin=args.origin)
    for name, path in (("actual", args.actual), ("expected", args.expected)):
        result[name] = dict(path=str(path.resolve()), sha256=hashlib.sha256(path.read_bytes()).hexdigest())
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["matched"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
