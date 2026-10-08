"""Measure a centred brush stamp, excluding disconnected edge changes.

The measured area is contrast-normalised coverage. For a solid stamp its
equivalent-area radius is independent of uniform opacity; spray uses extent.
Uncentred or clipped marks are rejected, not interpreted as a large spray.
"""
from __future__ import annotations

import logging
import math
import cv2
import numpy as np
from PIL import Image

log = logging.getLogger(__name__)


def measure_stamp(before_img: Image.Image, after_img: Image.Image, *, anchor=None):
    before = np.asarray(before_img.convert("RGB"))
    after = np.asarray(after_img.convert("RGB"))
    if before.shape != after.shape or before.size == 0:
        return None
    gray = cv2.cvtColor(cv2.absdiff(after, before), cv2.COLOR_RGB2GRAY)
    mask = (gray >= 16).astype(np.uint8)
    height, width = mask.shape
    ax, ay = anchor if anchor is not None else (width / 2, height / 2)
    if not (math.isfinite(ax) and math.isfinite(ay) and 0 <= ax < width and 0 <= ay < height):
        return None

    # A cursor halo just outside the patch can change edge pixels between
    # frames. Remove its disconnected components, preserving even a 1px dot.
    count, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=8)
    for label in range(1, count):
        x, y, w, h, _ = stats[label]
        if x == 0 or y == 0 or x + w == width or y + h == height:
            if x <= ax < x + w and y <= ay < y + h:
                log.info("Stamp not measured: bigger than the %dx%d test patch", width, height)
                return None  # the centred stamp itself is clipped
            mask[labels == label] = 0
    ys, xs = np.nonzero(mask)
    if not len(xs):
        log.info("Stamp not measured: nothing changed in the test patch")
        return None
    cx, cy = float(np.median(xs)), float(np.median(ys))
    distances = np.hypot(xs - cx, ys - cy)
    r95 = float(np.percentile(distances, 95))
    # A repaint elsewhere lies tens of pixels away; a game stamping where it sampled
    # the cursor lands 2-3 px off (live Spray Paint! 2026-10-07: 2.8 px at radius 7
    # failed the old 2 px limit and with it the whole brush learning).
    if math.hypot(cx - ax, cy - ay) > max(3.0, r95 * .4):
        log.info("Stamp not measured: the mark is %.1f px from the press (radius %.1f px)",
                 math.hypot(cx - ax, cy - ay), r95)
        return None  # a repaint elsewhere is not a stamp at the click position

    values = gray[ys, xs].astype(np.float64)
    contrast = float(np.percentile(values, 95))
    area = float(np.minimum(1.0, values / contrast).sum())
    density = min(1.0, len(xs) / max(1.0, math.pi * r95 ** 2))
    radius = math.sqrt(area / math.pi) if density >= .6 else float(np.percentile(distances, 90))
    if not math.isfinite(radius) or radius <= 0:
        return None
    span_x, span_y = int(xs.max() - xs.min() + 1), int(ys.max() - ys.min() + 1)
    fill = len(xs) / (span_x * span_y)
    shape = "square" if density >= .6 and fill >= .84 else "circle"
    confidence = (min(1.0, fill) if shape == "square" else
                  min(1.0, area / max(1.0, math.pi * (max(span_x, span_y) / 2) ** 2)))
    if density < .6:
        confidence = .5
    # Stamp centre relative to the cursor pixel. Programs anchor stamps
    # differently (Paint's smallest brush is 2x2 with the cursor on its
    # bottom-right pixel); the planner compensates instead of assuming centred.
    offset_x = (xs.min() + xs.max()) / 2.0 - ax
    offset_y = (ys.min() + ys.max()) / 2.0 - ay
    return dict(radius_px=radius, area_px=area, density=density, shape=shape, confidence=confidence,
                offset_x=float(offset_x), offset_y=float(offset_y))
