"""Measure how the target program accepts pointer input, then pick the fastest
reliable pacing (BRUSH-004).

Programs differ: Paint connects consecutive pointer positions with a line, but
drops turns that arrive faster than it processes messages; many games stamp only
at the positions they sample once per frame. Instead of hand-tuned pauses the
engine draws small probes in the test zone and measures:

1. does a single long jump draw a continuous line (line interpolation)?
2. the shortest per-vertex dwell at which a dense zigzag keeps every corner;
3. the shortest pause around a pen lift at which a small test picture drawn
   with many lifts (thin lines at all angles, dots, a ring) comes out exact.
   Isolated lifts never failed in live Paint; what failed was sustained work
   (0 ms: streaks across the picture, 1 ms: thin diagonals lost), so the probe
   is filled by the engine's own route and the result gets a double margin.
   A hand-made order (one stroke per row run) failed up to 16 ms although real
   pictures were exact at 2 ms: the probe must lift the pen like a real fill.
"""
from __future__ import annotations

import math
import time

import cv2
import numpy as np

# Windows sleep adds ~0.45 ms to any wait, so 0.1 ms is really ~0.5 ms and 0 is
# no wait at all (live Paint: 0 broke, 0.1 ms exact on every test picture). Do not
# make the waits exact: a true 0.1 ms dwell broke Paint (99.3%) and the lift probe
# then failed up to 16 ms.
DWELL_LADDER = (0.0, 0.0001, 0.0005, 0.001, 0.002, 0.004, 0.008, 0.016, 0.033)
LIFT_LADDER = (0.0, 0.001, 0.002, 0.004, 0.008, 0.016, 0.033)
# The lift test picture must stay readable with the smallest stamp.
_LIFT_MAX_RADIUS_PX = 12
# Spots are 1.4 * 60 = 84 px apart: a wider picture overlaps its neighbour and
# repainting already dark pixels reads as a miss.
_LIFT_PATTERN_PX = 76
# Isolated probes pass sooner than a full picture (live Paint: probe 1 ms, picture 2 ms).
_LIFT_MARGIN = 2.0
_DIFF_THRESHOLD = 40
# Straight-stroke mode (browser games): pause after every release. Live Gartic
# Phone in Yandex Browser kept every stroke even at ~0 ms, the embedded Claude
# browser lost strokes below 12 ms: it depends on the machine and the browser.
GAP_LADDER = (0.0005, 0.002, 0.005, 0.01, 0.02, 0.035)
# Programs that stamp where they sample the cursor: rows a stamp-diameter apart
# left stripes (live Spray Paint! 2026-10-07: the 0.1 stamp is 5 x 4 px under the
# tilted camera, rows of 4 px came out striped at any pace, rows of 2 px whole).
STAMP_ROW_OVERLAP = 0.6
# The fill probe: a square filled by the engine's own route at the drawing's row
# pitch. A straight line hid 2-6 px holes between stamps (its check looks around
# every point), so 1 ms passed and real fills got gaps; the square read 94-98 % at
# 1 ms, 100 % from 1.5-2 ms.
_FILL_PROBE_PX = 40
_FILL_COVERAGE = 0.995
_FILL_LADDER_FACTOR = 1.5


def stamp_row_pitch(stamp_radius_px: float) -> int:
    """Row pitch (grid cell, px) for a stamping program: rows overlap by ~40 %."""
    return max(1, int(math.floor(2.0 * max(0.5, float(stamp_radius_px)) * STAMP_ROW_OVERLAP)))


def changed_mask(before, after) -> np.ndarray:
    a = np.asarray(before.convert("RGB"), dtype=np.int16)
    b = np.asarray(after.convert("RGB"), dtype=np.int16)
    return np.abs(a - b).max(axis=2) >= _DIFF_THRESHOLD


def line_is_continuous(mask: np.ndarray, start: tuple[int, int], end: tuple[int, int], reach: float) -> bool:
    """True when paint covers (almost) every point of the segment interior."""
    (x0, y0), (x1, y1) = start, end
    n = int(max(abs(x1 - x0), abs(y1 - y0)))
    if n < 4:
        return False
    h, w = mask.shape
    k = int(math.ceil(reach))
    hits = 0
    samples = 0
    for i in range(n // 5, n - n // 5):
        x = round(x0 + (x1 - x0) * i / n)
        y = round(y0 + (y1 - y0) * i / n)
        window = mask[max(0, y - k):min(h, y + k + 1), max(0, x - k):min(w, x + k + 1)]
        samples += 1
        hits += bool(window.any())
    return samples > 0 and hits / samples >= 0.95


def trail_along(mask: np.ndarray, hover: tuple[int, int], start: tuple[int, int], reach: float) -> int:
    """Painted pixels on the interior of the hover->start travel (the pen was up)."""
    (x0, y0), (x1, y1) = hover, start
    n = int(max(abs(x1 - x0), abs(y1 - y0)))
    h, w = mask.shape
    k = int(math.ceil(reach))
    margin = k + 3
    hits = 0
    for i in range(margin, n - margin):
        x = round(x0 + (x1 - x0) * i / n)
        y = round(y0 + (y1 - y0) * i / n)
        hits += int(mask[max(0, y - 1):min(h, y + 2), max(0, x - 1):min(w, x + 2)].any())
    return hits


def zigzag(origin: tuple[int, int], *, count: int = 13, pitch: int = 6, height: int = 20) -> list[tuple[int, int]]:
    x0, y0 = origin
    return [(x0 + i * pitch, y0 + (0 if i % 2 == 0 else height)) for i in range(count)]


def corners_kept(mask: np.ndarray, vertices, reach: float) -> tuple[int, int]:
    """How many zigzag corners got paint within `reach` px (a dropped turn is
    drawn as a chord that misses the corner)."""
    h, w = mask.shape
    k = int(math.ceil(reach))
    kept = 0
    for x, y in vertices:
        window = mask[max(0, y - k):min(h, y + k + 1), max(0, x - k):min(w, x + k + 1)]
        kept += bool(window.any())
    return kept, len(vertices)


def lift_pattern(size: int = _LIFT_PATTERN_PX) -> np.ndarray:
    """Thin lines at many angles, a ring and small squares: the shapes whose
    fills need the most pen lifts, including presses right next to a release."""
    img = np.zeros((size, size), np.uint8)
    c = size // 2
    for k in range(8):
        angle = math.pi * k / 8
        dx, dy = math.cos(angle) * (c - 6), math.sin(angle) * (c - 6)
        cv2.line(img, (int(c - dx), int(c - dy)), (int(c + dx), int(c + dy)), 1, 1)
    cv2.circle(img, (c, c), c // 3, 1, 1)
    for x in range(8, size - 8, 17):
        img[4:7, x:x + 3] = 1
        img[size - 7:size - 4, x + 5:x + 7] = 1
    return img.astype(bool)


def pattern_errors(painted: np.ndarray, expected: np.ndarray, reach: float) -> tuple[int, int]:
    """(missing, stray): expected pixels with no paint within `reach`, painted
    pixels farther than `reach` from any expected pixel."""
    k = 2 * int(math.ceil(reach)) + 1
    kernel = np.ones((k, k), np.uint8)
    near_paint = cv2.dilate(painted.astype(np.uint8), kernel) > 0
    near_expected = cv2.dilate(expected.astype(np.uint8), kernel) > 0
    return int(np.count_nonzero(expected & ~near_paint)), int(np.count_nonzero(painted & ~near_expected))


def first_reliable(results: list[tuple[float, bool]], ladder, margin: float = 1.5, floor: float = 0.0005) -> float:
    """The first value that passed twice in a row, else the last pass, else the
    slowest rung; with a safety margin on top."""
    value = None
    for (d, ok), (_d2, ok2) in zip(results, results[1:]):
        if ok and ok2:
            value = d
            break
    passed = [d for d, ok in results if ok]
    if value is None and passed:
        value = passed[-1]
    if value is None:
        value = ladder[-1]
    return round(max(value * margin, value + floor) if value > 0 else 0.0003, 4)


def recommend(interpolates: bool, dwell_results: list[tuple[float, bool]], brush_radius_px: float) -> dict:
    """Settings from measurements: the first dwell that passed twice in a row
    (plus a safety margin), and the pen step for programs that do not connect."""
    # The zigzag turns every 6 px, denser than any fill: passing it is the margin
    # (live Paint: 7 full pictures exact at the zigzag's first passing dwell, 0 ms broke).
    dwell = first_reliable(dwell_results, DWELL_LADDER, margin=1.0, floor=0.0)
    step = 0 if interpolates else max(1, int(math.floor(max(0.5, brush_radius_px) * 1.2)))
    return {"interpolates": bool(interpolates), "draw_delay": dwell, "pen_max_step": step}


def _stroke(engine, points, dwell: float, step: int) -> None:
    saved_delay, saved_step = engine.draw_delay, engine.pen_max_step
    saved_fill = getattr(engine, "area_fill_delay", 0.0)
    saved_enabled = engine.drawing_enabled
    engine.draw_delay, engine.pen_max_step, engine.area_fill_delay = dwell, step, 0.0
    # draw_line dwells only while a drawing is enabled; probes run outside one
    engine.drawing_enabled = True
    try:
        engine._move_abs(*points[0])
        time.sleep(0.05)
        engine._pen_down()
        time.sleep(0.03)
        for a, b in zip(points, points[1:]):
            if engine._automation_cancelled():
                break
            engine.draw_line(a[0], a[1], b[0], b[1])
        time.sleep(0.05)
    finally:
        engine._mouse_up_with_settle()
        engine.draw_delay, engine.pen_max_step, engine.area_fill_delay = saved_delay, saved_step, saved_fill
        engine.drawing_enabled = saved_enabled


def _fill_with_engine_route(engine, pattern: np.ndarray, x0: int, y0: int, pause: float, dwell: float,
                            step: int) -> None:
    """Fill `pattern` (one colour, 1 px cells) at (x0, y0) through the engine's
    regular DFS fill, so lifts come exactly as in a real picture."""
    names = ("cluster_map", "drawn_mask", "brush_size", "_last_pen_cell", "_route_exit_cell",
             "pen_settle_delay", "mouse_release_settle", "pen_button_delay",
             "draw_delay", "pen_max_step", "area_fill_delay", "drawing_enabled")
    saved = {name: getattr(engine, name, None) for name in names}
    try:
        engine.cluster_map = pattern.astype(np.int32)
        engine.drawn_mask = np.zeros(pattern.shape, dtype=bool)
        engine.brush_size = 1
        engine._last_pen_cell = engine._route_exit_cell = None
        engine.pen_settle_delay, engine.mouse_release_settle, engine.pen_button_delay = pause, pause, 0.0
        engine.draw_delay, engine.pen_max_step, engine.area_fill_delay = dwell, step, 0.0
        engine.drawing_enabled = True
        engine.dfs_4dir_fill_current_color(pattern.astype(np.uint8) * 255, x0, y0, 1)
        time.sleep(0.05)
    finally:
        engine._mouse_up_with_settle()
        for name, value in saved.items():
            setattr(engine, name, value)


def _settled_patch(engine, cx: int, cy: int, half: int, *, quiet: float = 1.0, timeout: float = 6.0):
    """Grab the patch once the program has finished drawing. A busy program may
    queue the strokes and show them all at once: live Paint kept the probe blank
    for 0.6 s and then painted it whole, so the screen must stay unchanged for
    `quiet` seconds before it counts as final."""
    previous = engine._grab_patch(cx, cy, half)
    stable_since = time.perf_counter()
    deadline = stable_since + timeout
    while previous is not None and time.perf_counter() < deadline:
        time.sleep(0.1)
        current = engine._grab_patch(cx, cy, half)
        if current is None:
            break
        if changed_mask(previous, current).any():
            stable_since = time.perf_counter()
        elif time.perf_counter() - stable_since >= quiet:
            return current
        previous = current
    return previous


def learn(engine, zone, *, brush_radius_px: float = 1.0, status=None, fill_cell: int | None = None) -> dict | None:
    """Probe the target inside `zone` (x, y, w, h). Returns the recommended
    pacing or None when stamps were not visible (then nothing is changed)."""
    reach = max(2.0, brush_radius_px + 1.5)
    need = 60  # spots 1.4 * 60 = 84 px apart hold a 72 px probe without overlap
    # All spots at once from one capture: a thin probe barely changes a spot's
    # average, so asking again after each probe returned the same place and the
    # zigzags were drawn on top of each other.
    # + jump probe, two press-trail probes, two last-move probes, up to six fill checks
    spots = list(engine._dynamic_brush_v2_clean_spots(zone, need, len(DWELL_LADDER) + len(LIFT_LADDER) + 11))

    def probe(draw):
        if not spots:
            return None, None
        cx, cy = spots.pop(0)
        before = engine._grab_patch(cx, cy, need)
        draw(cx, cy)
        engine._click_abs(zone[0] + 1, zone[1] + 1)
        after = _settled_patch(engine, cx, cy, need)
        if before is None or after is None:
            return None, None
        return changed_mask(before, after), (cx - need, cy - need)

    if status:
        status("Проверяем, как программа принимает движения мыши…")
    start_end = {}
    saved_nudge = bool(getattr(engine, "pen_press_nudge", False))
    nudge = _learn_press_nudge(engine, probe, reach, status)
    engine.pen_press_nudge = bool(nudge)
    try:
        return _learn_rest(engine, probe, reach, brush_radius_px, status, start_end, nudge, fill_cell)
    finally:
        engine.pen_press_nudge = saved_nudge


def _learn_press_nudge(engine, probe, reach, status):
    """True when a press right after a jump paints along the jump (frame-sampled
    cursor), confirmed by a second probe with the nudge on, or when a stroke keeps
    its last move only with the nudge's step back before the release; None
    without paint."""
    trails = []
    for nudge in (False, True):
        engine.pen_press_nudge = nudge
        geometry = {}

        def press_after_jump(cx, cy):
            hover, start = (cx - 34, cy + 30), (cx + 24, cy - 30)
            geometry.update(hover=hover, start=start)
            engine._move_abs(*hover)
            time.sleep(0.3)
            _stroke(engine, [start, (start[0] + 10, start[1])], 0.01, 0)

        mask, origin = probe(press_after_jump)
        if mask is None or not mask.any():
            return None
        local = lambda p: (p[0] - origin[0], p[1] - origin[1])
        trails.append(trail_along(mask, local(geometry["hover"]), local(geometry["start"]), reach))
        if nudge is False and trails[0] == 0:
            return _last_move_needs_step(engine, probe, reach)
    return trails[0] > 0 and trails[1] < trails[0]


def _last_move_needs_step(engine, probe, reach) -> bool:
    """«Нарисуй меня!» draws a segment only when the cursor moves on: a press, one
    move and a release left a dot. Such a stroke here would also fail the jump and
    zigzag probes and slow every fill down for nothing."""
    ends = {}

    def single_move(cx, cy):
        ends["line"] = ((cx - 30, cy), (cx + 30, cy))
        _stroke(engine, list(ends["line"]), 0.03, 0)

    for nudge in (False, True):
        engine.pen_press_nudge = nudge
        mask, origin = probe(single_move)
        if mask is None:
            return False
        (ax, ay), (bx, by) = ends["line"]
        if line_is_continuous(mask, (ax - origin[0], ay - origin[1]), (bx - origin[0], by - origin[1]), reach):
            return nudge
    return False


def _learn_rest(engine, probe, reach, brush_radius_px, status, start_end, nudge, fill_cell=None):

    def jump(cx, cy):
        a, b = (cx - 40, cy), (cx + 40, cy)
        start_end["line"] = (a, b)
        _stroke(engine, [a, b], 0.03, 0)

    mask, origin = probe(jump)
    if mask is None or not mask.any():
        return None
    (ax, ay), (bx, by) = start_end["line"]
    interpolates = line_is_continuous(mask, (ax - origin[0], ay - origin[1]), (bx - origin[0], by - origin[1]), reach)
    step = 0 if interpolates else max(1, int(math.floor(max(0.5, brush_radius_px) * 1.2)))

    results: list[tuple[float, bool]] = []
    for dwell in DWELL_LADDER:
        if engine._automation_cancelled():
            return None
        if status:
            status(f"Подбираем скорость: пауза на повороте {dwell * 1000:.1f} мс…")
        shape = {}

        def zig(cx, cy, dwell=dwell):
            pts = zigzag((cx - 36, cy - 10))
            shape["pts"] = pts
            _stroke(engine, pts, dwell, step)

        mask, origin = probe(zig)
        if mask is None:
            break  # no free spot left: decide from the trials so far
        local = [(x - origin[0], y - origin[1]) for x, y in shape["pts"]]
        kept, total = corners_kept(mask, local, reach)
        results.append((dwell, kept == total))
        if len(results) >= 2 and results[-1][1] and results[-2][1]:
            break
    if not results:
        return None
    choice = recommend(interpolates, results, brush_radius_px)
    choice["trials"] = [{"dwell": d, "ok": ok} for d, ok in results]
    choice["press_nudge"] = bool(nudge)
    if not interpolates:
        # the pitch the drawing will use: the user's own grid, or overlapping stamp rows
        _check_fill_speed(engine, probe, choice, fill_cell or stamp_row_pitch(brush_radius_px), reach, status)
    if brush_radius_px <= _LIFT_MAX_RADIUS_PX:
        lifts = _learn_lift(engine, probe, choice, brush_radius_px, reach, status)
        if lifts:
            choice["lift_pause"] = first_reliable(lifts, LIFT_LADDER, _LIFT_MARGIN)
            choice["lift_trials"] = [{"pause": p, "ok": ok} for p, ok in lifts]
    return choice


def fill_coverage(mask: np.ndarray, corner: tuple[int, int], side: int, inset: int) -> float:
    """Painted share of the square's interior (`inset` px in from every edge)."""
    x0, y0 = corner
    inner = mask[y0 + inset:y0 + side - inset, x0 + inset:x0 + side - inset]
    return float(inner.mean()) if inner.size else 0.0


def _fill_square_with_route(engine, x0: int, y0: int, side: int, cell: int, dwell: float, step: int) -> None:
    """A solid square drawn by the engine's «Прямые отрезки» route at the given
    row pitch, as a real fill of one colour."""
    n = max(2, side // cell)
    names = ("cluster_map", "drawn_mask", "brush_size", "_last_pen_cell", "_route_exit_cell",
             "draw_delay", "pen_max_step", "area_fill_delay", "drawing_enabled")
    saved = {name: getattr(engine, name, None) for name in names}
    try:
        engine.cluster_map = np.zeros((n, n), np.int32)
        engine.drawn_mask = np.zeros((n, n), dtype=bool)
        engine.brush_size = cell
        engine._last_pen_cell = engine._route_exit_cell = None
        engine.draw_delay, engine.pen_max_step, engine.area_fill_delay = dwell, step, 0.0
        engine.drawing_enabled = True
        engine.line_cover_fill_current_color(np.full((n, n), 255, np.uint8), x0, y0, 0)
        time.sleep(0.05)
    finally:
        engine._mouse_up_with_settle()
        for name, value in saved.items():
            setattr(engine, name, value)


def _check_fill_speed(engine, probe, choice, cell, reach, status, attempts: int = 6) -> None:
    """Programs that only stamp where they sample the cursor drop stamps on fast
    strokes. Fill a small square at the drawing's row pitch and slow the per-hop
    dwell down until it comes out solid twice in a row."""
    side = (_FILL_PROBE_PX // cell) * cell
    inset = int(math.ceil(reach))
    dwell = max(0.001, float(choice["draw_delay"]))
    checks, passed = [], None
    for _ in range(attempts):
        if engine._automation_cancelled():
            break
        if status:
            status(f"Проверяем заливку: пауза {dwell * 1000:.1f} мс на шаг…")
        geometry = {}

        def square(cx, cy, dwell=dwell):
            geometry["corner"] = (cx - side // 2, cy - side // 2)
            _fill_square_with_route(engine, cx - side // 2, cy - side // 2, side, cell, dwell, choice["pen_max_step"])

        mask, origin = probe(square)
        if mask is None:
            break
        corner = (geometry["corner"][0] - origin[0], geometry["corner"][1] - origin[1])
        coverage = fill_coverage(mask, corner, side, inset)
        ok = coverage >= _FILL_COVERAGE
        checks.append({"dwell": dwell, "coverage": round(coverage, 4), "ok": ok})
        if ok and passed == dwell:
            break                      # solid twice at this pace
        if ok:
            passed = dwell             # confirm once more before trusting it
            continue
        passed = None
        dwell = round(dwell * _FILL_LADDER_FACTOR, 4)
    choice["draw_delay"] = dwell
    choice["fill_cell"] = cell
    choice["fill_checks"] = checks


def _learn_lift(engine, probe, choice, brush_radius_px, reach, status) -> list[tuple[float, bool]]:
    expected = lift_pattern()
    half = _LIFT_PATTERN_PX // 2
    results: list[tuple[float, bool]] = []
    for pause in LIFT_LADDER:
        if engine._automation_cancelled():
            return []
        if status:
            status(f"Подбираем скорость: пауза на отрыве пера {pause * 1000:.0f} мс…")
        shape = {}

        def picture(cx, cy, pause=pause):
            shape["corner"] = (cx - half, cy - half)
            _fill_with_engine_route(engine, expected, cx - half, cy - half, pause,
                                    choice["draw_delay"], choice["pen_max_step"])

        mask, origin = probe(picture)
        if mask is None:
            break
        x0, y0 = shape["corner"][0] - origin[0], shape["corner"][1] - origin[1]
        full = np.zeros_like(mask)
        full[y0:y0 + expected.shape[0], x0:x0 + expected.shape[1]] = expected
        missing, stray = pattern_errors(mask, full, reach)
        results.append((pause, missing == 0 and stray == 0))
        if len(results) >= 2 and results[-1][1] and results[-2][1]:
            break
    return results


def learn_stroke_gap(engine, zone, *, brush_radius_px: float = 1.5, status=None) -> dict | None:
    """Straight-stroke mode: the shortest pause after a release at which the
    program keeps every stroke. The lift test picture is filled by the engine's
    own route with every straight segment pressed separately, as in a real
    drawing; the first pause that is exact twice in a row wins, with a margin."""
    reach = max(2.0, brush_radius_px + 1.5)
    need = 60
    spots = list(engine._dynamic_brush_v2_clean_spots(zone, need, 2 * len(GAP_LADDER)))
    expected = lift_pattern()
    half = _LIFT_PATTERN_PX // 2
    saved = (engine.pen_split_strokes, engine.pen_stroke_gap)
    results: list[tuple[float, bool]] = []
    try:
        engine.pen_split_strokes = True
        for gap in GAP_LADDER:
            if engine._automation_cancelled() or not spots:
                break
            if status:
                status(f"Подбираем скорость: пауза между штрихами {gap * 1000:.1f} мс…")
            cx, cy = spots.pop(0)
            before = engine._grab_patch(cx, cy, need)
            engine.pen_stroke_gap = gap
            _fill_with_engine_route(engine, expected, cx - half, cy - half, 0.0, 0.0, 0)
            engine._click_abs(zone[0] + 1, zone[1] + 1)
            after = _settled_patch(engine, cx, cy, need)
            if before is None or after is None:
                break
            mask = changed_mask(before, after)
            if not mask.any():
                return None  # nothing visible: no colour or not a canvas
            full = np.zeros_like(mask)
            x0, y0 = need - half, need - half
            full[y0:y0 + expected.shape[0], x0:x0 + expected.shape[1]] = expected
            missing, stray = pattern_errors(mask, full, reach)
            results.append((gap, missing == 0 and stray == 0))
            if len(results) >= 2 and results[-1][1] and results[-2][1]:
                break
    finally:
        engine.pen_split_strokes, engine.pen_stroke_gap = saved
    if not results or not any(ok for _gap, ok in results):
        return None
    return {"stroke_gap": first_reliable(results, GAP_LADDER, margin=1.5, floor=0.001),
            "gap_trials": [{"gap": g, "ok": ok} for g, ok in results]}
