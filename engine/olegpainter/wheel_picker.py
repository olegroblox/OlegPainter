"""Colour pickers made of a hue ring with a saturation/brightness square inside (WHEEL-001).

Draw Me! (Roblox), Krita, many web painters: the ring chooses the hue, the square
the saturation and brightness of that hue. Nothing is assumed about the layout:
the calibration measures it from one picture of the picker —

* the ring is the vivid connected shape that contains (almost) every hue; its
  hue is tabulated per angle, because rings are often drawn from colour stops
  and are not linear in angle;
* the square is what differs from the panel inside the ring; the edge where the
  colours are brightest is brightness 1, the most saturated edge is saturation 1.

Picking: click the angle of the target hue, then the square point of its
saturation and brightness — refined on a fresh picture of the square, since the
square is redrawn for the new hue. The ring may be drawn unlike it chooses:
Draw Me! paints its ring from colour stops but takes the hue linearly from the
angle (live 2026-10-01: orange and pink came out 15-20° redder). So the hue the
program really took is read from the square's pure-hue corner, a wrong click is
corrected, and the measured offsets make the next first clicks right.
Pure numpy; screen access stays with the caller.
"""
from __future__ import annotations

import math

import numpy as np

RING_MIN_HUE_BINS = 30       # of 36: the ring shows practically every hue
SQUARE_INSET = 3             # px kept away from the square border when clicking
REFINE_RADIUS = 14           # px searched around the computed square point
HANDLE_RADIUS = 10           # px around the previous square click: the handle covers them
HUE_TOLERANCE = 0.008        # ~3°: closer than this the square already shows the right hue
MEASURED_REACH = 0.08        # a measured offset applies to target hues this close
MEASURED_LIMIT = 48          # measured offsets kept in the calibration


def _hsv(rgb: np.ndarray):
    a = rgb.astype(np.float32) / 255.0
    mx = a.max(axis=-1)
    mn = a.min(axis=-1)
    delta = mx - mn
    sat = np.where(mx > 1e-6, delta / np.maximum(mx, 1e-6), 0.0)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    safe = np.maximum(delta, 1e-6)
    hue = np.where(mx == r, ((g - b) / safe) % 6.0,
                   np.where(mx == g, (b - r) / safe + 2.0, (r - g) / safe + 4.0)) / 6.0
    hue = np.where(delta > 1e-6, hue, 0.0)
    return hue, sat, mx


def _hue_distance(a, b):
    d = np.abs(np.asarray(a) - b) % 1.0
    return np.minimum(d, 1.0 - d)


def analyse(rgb: np.ndarray, origin=(0, 0)) -> dict:
    """Calibration from a picture of the picker (RGB array) taken at screen `origin`."""
    from scipy import ndimage
    if rgb.ndim != 3 or min(rgb.shape[:2]) < 40:
        raise ValueError("Рамка слишком маленькая: обведите цветовое колесо целиком.")
    hue, sat, val = _hsv(rgb)
    vivid = (sat > 0.55) & (val > 0.75)
    labels, count = ndimage.label(vivid)
    ring_label, ring_size = 0, 0
    for index in range(1, count + 1):
        component = labels == index
        size = int(component.sum())
        if size < 200 or size <= ring_size:
            continue
        bins = np.unique((hue[component] * 36).astype(int) % 36)
        if len(bins) >= RING_MIN_HUE_BINS:
            ring_label, ring_size = index, size
    if not ring_label:
        raise ValueError("Не нашлось цветное кольцо: обведите рамкой колесо с кольцом оттенков.")
    ys, xs = np.nonzero(labels == ring_label)
    cx, cy = (xs.min() + xs.max()) / 2.0, (ys.min() + ys.max()) / 2.0
    r_out = ((xs.max() - xs.min()) + (ys.max() - ys.min())) / 4.0
    ring_mask = labels == ring_label

    def at(x, y):
        xi, yi = int(round(x)), int(round(y))
        if 0 <= yi < rgb.shape[0] and 0 <= xi < rgb.shape[1]:
            return xi, yi
        return None

    inner = []
    for degree in range(0, 360, 3):
        t = math.radians(degree)
        entered = False
        for step in np.arange(r_out + 2, 0, -0.5):
            point = at(cx + step * math.cos(t), cy - step * math.sin(t))
            if point is None:
                continue
            on_ring = bool(ring_mask[point[1], point[0]])
            if on_ring:
                entered = True
            elif entered:
                inner.append(step)
                break
    if len(inner) < 60:
        raise ValueError("Кольцо найдено не полностью: обведите колесо целиком.")
    r_in = float(np.median(inner))
    if r_in < 0.35 * r_out:
        raise ValueError("Кольцо слишком толстое или внутри нет квадрата: это не колесо с квадратом.")
    r_mid = (r_in + r_out) / 2.0

    table = []
    for tenth in range(0, 3600, 5):
        t = math.radians(tenth / 10.0)
        point = at(cx + r_mid * math.cos(t), cy - r_mid * math.sin(t))
        if point is None or not ring_mask[point[1], point[0]]:
            continue
        table.append((tenth / 10.0, float(hue[point[1], point[0]])))
    if len(table) < 360:
        raise ValueError("Кольцо найдено не полностью: обведите колесо целиком.")

    # Inside the ring: the panel colour is the most frequent one next to the ring.
    yy, xx = np.mgrid[0:rgb.shape[0], 0:rgb.shape[1]]
    dist = np.hypot(xx - cx, yy - cy)
    rim = (dist > r_in - 6) & (dist < r_in - 2)
    quantized = (rgb[rim] // 8).astype(np.int32)
    keys, counts = np.unique(quantized[:, 0] * 1024 + quantized[:, 1] * 32 + quantized[:, 2], return_counts=True)
    key = int(keys[np.argmax(counts)])
    panel = np.array([key // 1024, (key // 32) % 32, key % 32], np.float32) * 8 + 4
    differs = (np.abs(rgb.astype(np.float32) - panel).sum(axis=-1) > 30) & (dist < r_in - 4)
    labels, count = ndimage.label(differs)
    if not count:
        raise ValueError("Внутри кольца не найден квадрат оттенков.")
    sizes = ndimage.sum(differs, labels, range(1, count + 1))
    square_mask = labels == (int(np.argmax(sizes)) + 1)
    # The handle sticks out of the square (at brightness 0 it hangs below it,
    # live 2026-10-01: the square came out 3 px taller): keep the rows and
    # columns the square fills at least half.
    rows, cols = square_mask.sum(axis=1), square_mask.sum(axis=0)
    full_rows, full_cols = np.nonzero(rows >= 0.5 * rows.max())[0], np.nonzero(cols >= 0.5 * cols.max())[0]
    left, top, right, bottom = int(full_cols.min()), int(full_rows.min()), int(full_cols.max()), int(full_rows.max())
    if right - left < 20 or bottom - top < 20:
        raise ValueError("Квадрат внутри кольца слишком маленький.")

    # Axes from the four edge strips (robust to the handle on one of them).
    def strip(x0, y0, x1, y1):
        block = rgb[y0:y1, x0:x1].reshape(-1, 3)
        h, s, v = _hsv(block)
        bright = v > 0.15
        return float(np.mean(v)), float(np.mean(s[bright])) if bright.any() else 0.0

    k = 4
    v_top, s_top = strip(left + k, top + 1, right - k, top + 1 + k)
    v_bottom, s_bottom = strip(left + k, bottom - k, right - k, bottom)
    v_left, s_left = strip(left + 1, top + k, left + 1 + k, bottom - k)
    v_right, s_right = strip(right - k, top + k, right, bottom - k)
    if abs(v_top - v_bottom) >= abs(v_left - v_right):
        v_axis = ("y", -1 if v_top > v_bottom else 1)     # brightness grows upwards / downwards
        s_axis = ("x", -1 if s_left > s_right else 1)
    else:
        v_axis = ("x", -1 if v_left > v_right else 1)
        s_axis = ("y", -1 if s_top > s_bottom else 1)
    ox, oy = origin
    return dict(
        centre=[round(cx + ox, 2), round(cy + oy, 2)],
        r_in=round(r_in, 2), r_out=round(r_out, 2),
        hue_table=[[angle, round(h, 4)] for angle, h in table],
        square=[left + ox, top + oy, right + ox, bottom + oy],
        s_axis=list(s_axis), v_axis=list(v_axis),
    )


def valid(calib) -> bool:
    try:
        return (len(calib["centre"]) == 2 and float(calib["r_out"]) > float(calib["r_in"]) > 0
                and len(calib["hue_table"]) >= 360 and len(calib["square"]) == 4
                and calib["s_axis"][0] in ("x", "y") and calib["v_axis"][0] in ("x", "y"))
    except Exception:
        return False


def square_visible(calib, picture: np.ndarray) -> bool:
    """The picture shows the square: its brightness-1 edge is bright and the opposite edge dark."""
    left, top, right, bottom = calib["square"]
    if picture is None or picture.shape[:2] != (bottom - top + 1, right - left + 1):
        return False
    name, direction = calib["v_axis"]
    # Strips a few pixels inside the edges, by median: the handle and a pixel or
    # two of the panel around the square do not decide.
    k = 4
    near, far = slice(k, 2 * k), slice(-2 * k, -k)
    edges = {("y", -1): (picture[near], picture[far]), ("y", 1): (picture[far], picture[near]),
             ("x", -1): (picture[:, near], picture[:, far]), ("x", 1): (picture[:, far], picture[:, near])}
    bright, dark = edges[(name, int(direction))]
    return float(np.median(bright.max(axis=-1))) > 140 and float(np.median(dark.max(axis=-1))) < 90


def ring_box(calib) -> tuple[int, int, int, int]:
    cx, cy = calib["centre"]
    r = float(calib["r_out"]) + 2
    return int(cx - r), int(cy - r), int(cx + r) + 1, int(cy + r) + 1


def ring_visible(calib, picture: np.ndarray) -> bool:
    """The calibrated ring is on the screen (picture of ring_box): its hues sit where they were."""
    left, top, right, bottom = ring_box(calib)
    if picture is None or picture.shape[:2] != (bottom - top, right - left):
        return False
    cx, cy = calib["centre"]
    radius = (float(calib["r_in"]) + float(calib["r_out"])) / 2.0
    table = calib["hue_table"]
    hits = 0
    for angle, expected in table[::max(1, len(table) // 12)][:12]:
        t = math.radians(angle)
        x = int(round(cx + radius * math.cos(t))) - left
        y = int(round(cy - radius * math.sin(t))) - top
        if not (0 <= y < picture.shape[0] and 0 <= x < picture.shape[1]):
            continue
        hue, sat, val = _hsv(picture[y:y + 1, x:x + 1])
        if sat[0, 0] > 0.4 and val[0, 0] > 0.6 and _hue_distance(hue[0, 0], expected) < 0.05:
            hits += 1
    return hits >= 9


def target_hue(rgb) -> float | None:
    """Hue of `rgb` (0..1), None for greys: any hue fits them."""
    r, g, b = (float(c) / 255.0 for c in rgb)
    mx, mn = max(r, g, b), min(r, g, b)
    if mx < 0.06 or (mx - mn) / mx < 0.06:
        return None
    hue, _, _ = _hsv(np.array([[[int(c) for c in rgb]]], np.uint8))
    return float(hue[0, 0])


def _table_angle(calib, hue: float) -> float:
    table = np.asarray(calib["hue_table"], np.float32)
    return float(table[int(np.argmin(_hue_distance(table[:, 1], hue)))][0])


def _orientation(calib) -> int:
    """+1 when the hue grows with the (counter-clockwise) angle, -1 when it falls."""
    table = np.asarray(calib["hue_table"], np.float32)
    step = (np.diff(table[:, 1]) + 0.5) % 1.0 - 0.5              # wrap-safe hue change per entry
    return 1 if float(np.median(step)) >= 0 else -1


def ring_angle(calib, rgb) -> float | None:
    """Ring angle (degrees, counter-clockwise from the right) for the hue of `rgb`."""
    hue = target_hue(rgb)
    if hue is None:
        return None
    angle = _table_angle(calib, hue)
    measured = calib.get("hue_measured") or []
    if measured:
        hues = np.asarray([m[0] for m in measured], np.float32)
        nearest = int(np.argmin(_hue_distance(hues, hue)))
        if float(_hue_distance(hues[nearest], hue)) <= MEASURED_REACH:
            angle += float(measured[nearest][1])
    return angle % 360.0


def ring_xy(calib, angle: float) -> tuple[int, int]:
    cx, cy = calib["centre"]
    radius = (float(calib["r_in"]) + float(calib["r_out"])) / 2.0
    t = math.radians(angle)
    return int(round(cx + radius * math.cos(t))), int(round(cy - radius * math.sin(t)))


def ring_point(calib, rgb) -> tuple[int, int] | None:
    """Where to click on the ring for the hue of `rgb`; None for greys (any hue fits)."""
    angle = ring_angle(calib, rgb)
    return None if angle is None else ring_xy(calib, angle)


def square_hue(calib, square_rgb: np.ndarray) -> float | None:
    """The hue the program took: the whole square shows one hue, read where it is
    saturated and bright (the handle over a corner is white and black: skipped)."""
    left, top, right, bottom = calib["square"]
    if square_rgb is None or square_rgb.shape[:2] != (bottom - top + 1, right - left + 1):
        return None
    hue, sat, val = _hsv(square_rgb)
    pure = (sat > 0.6) & (val > 0.6)
    if int(pure.sum()) < 30:
        return None
    turn = hue[pure] * 2 * math.pi
    return float(math.atan2(np.sin(turn).mean(), np.cos(turn).mean()) / (2 * math.pi)) % 1.0


def hue_error(target: float, actual: float) -> float:
    """Signed target - actual on the hue circle, in -0.5..0.5."""
    return (float(target) - float(actual) + 0.5) % 1.0 - 0.5


def corrected_angle(calib, angle: float, error: float) -> float:
    """One correction step: a ring turns the hue once around in 360°."""
    return (angle + _orientation(calib) * error * 360.0) % 360.0


def remember(calib, angle: float, actual: float) -> None:
    """Keep the measured offset of this angle from the drawn ring (for the next first click)."""
    offset = (float(angle) - _table_angle(calib, actual) + 180.0) % 360.0 - 180.0
    measured = [m for m in (calib.get("hue_measured") or [])
                if float(_hue_distance(float(m[0]), actual)) > 0.01]
    measured.append([round(float(actual), 4), round(offset, 2)])
    calib["hue_measured"] = measured[-MEASURED_LIMIT:]


def square_point(calib, rgb) -> tuple[int, int]:
    """Square position of the saturation and brightness of `rgb`."""
    r, g, b = (float(c) / 255.0 for c in rgb)
    v = max(r, g, b)
    s = 0.0 if v <= 1e-6 else (v - min(r, g, b)) / v
    left, top, right, bottom = calib["square"]
    span = {"x": (left + SQUARE_INSET, right - SQUARE_INSET), "y": (top + SQUARE_INSET, bottom - SQUARE_INSET)}

    def place(axis, value):
        name, direction = axis
        low, high = span[name]
        share = value if direction > 0 else 1.0 - value
        return low + share * (high - low)

    point = {calib["s_axis"][0]: place(calib["s_axis"], s), calib["v_axis"][0]: place(calib["v_axis"], v)}
    return int(round(point["x"])), int(round(point["y"]))


def refine(calib, square_rgb: np.ndarray, target_rgb, guess, avoid=None) -> tuple[int, int]:
    """The pixel of the freshly drawn square nearest in colour to the target, around `guess`.

    `square_rgb` is a picture of exactly calib["square"]; `avoid` is the previous square
    click, hidden under the picker's handle. Falls back to `guess`."""
    left, top, right, bottom = calib["square"]
    if square_rgb is None or square_rgb.shape[0] != bottom - top + 1 or square_rgb.shape[1] != right - left + 1:
        return guess
    gx, gy = guess[0] - left, guess[1] - top
    y0, y1 = max(SQUARE_INSET, gy - REFINE_RADIUS), min(square_rgb.shape[0] - SQUARE_INSET, gy + REFINE_RADIUS + 1)
    x0, x1 = max(SQUARE_INSET, gx - REFINE_RADIUS), min(square_rgb.shape[1] - SQUARE_INSET, gx + REFINE_RADIUS + 1)
    if y1 <= y0 or x1 <= x0:
        return guess
    window = square_rgb[y0:y1, x0:x1].astype(np.float32)
    error = np.linalg.norm(window - np.asarray(target_rgb, np.float32), axis=-1)
    yy, xx = np.mgrid[y0:y1, x0:x1]
    error += 0.5 * np.hypot(xx - gx, yy - gy)                 # prefer the computed point on ties
    if avoid is not None:
        hidden = np.hypot(xx + left - avoid[0], yy + top - avoid[1]) <= HANDLE_RADIUS
        if hidden.all():
            return guess
        error[hidden] = np.inf
    iy, ix = np.unravel_index(int(np.argmin(error)), error.shape)
    if not np.isfinite(error[iy, ix]):
        return guess
    return int(xx[iy, ix] + left), int(yy[iy, ix] + top)
