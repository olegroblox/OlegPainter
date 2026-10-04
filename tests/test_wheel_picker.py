"""Hue ring + saturation/brightness square pickers (WHEEL-001), on synthetic pictures."""
import colorsys
import math

import numpy as np
import pytest

from engine.olegpainter import wheel_picker as wp

CX, CY, R_IN, R_OUT = 160, 150, 100, 130
SQ = (100, 90, 220, 210)          # left, top, right, bottom


def ring_hue(angle_deg):
    # A ring drawn from colour stops: not linear in angle (like Draw Me!).
    t = ((90 - angle_deg) % 360) / 360.0          # red at the top, clockwise
    return (t ** 1.3) % 1.0


def square_colour(hue, x, y):
    s = 1.0 - (x - SQ[0]) / (SQ[2] - SQ[0])      # saturation 1 on the left
    v = 1.0 - (y - SQ[1]) / (SQ[3] - SQ[1])      # brightness 1 at the top
    return [int(round(c * 255)) for c in colorsys.hsv_to_rgb(hue, max(0.0, s), max(0.0, v))]


def picker(hue=0.0, sky=False):
    img = np.full((300, 340, 3), 255, np.uint8)
    if sky:
        img[:] = (90, 160, 240)
        return img
    yy, xx = np.mgrid[0:300, 0:340]
    dist = np.hypot(xx - CX, yy - CY)
    angle = np.degrees(np.arctan2(CY - yy, xx - CX)) % 360
    ring = (dist >= R_IN) & (dist <= R_OUT)
    for y, x in zip(*np.nonzero(ring)):
        img[y, x] = [int(c * 255) for c in colorsys.hsv_to_rgb(ring_hue(angle[y, x]), 1.0, 1.0)]
    for y in range(SQ[1], SQ[3] + 1):
        for x in range(SQ[0], SQ[2] + 1):
            img[y, x] = square_colour(hue, x, y)
    return img


def test_calibration_measures_ring_square_and_axes():
    calib = wp.analyse(picker(), origin=(1000, 200))
    assert abs(calib["centre"][0] - (CX + 1000)) <= 1.5 and abs(calib["centre"][1] - (CY + 200)) <= 1.5
    assert abs(calib["r_in"] - R_IN) <= 2 and abs(calib["r_out"] - R_OUT) <= 2
    assert calib["square"] == [SQ[0] + 1000, SQ[1] + 200, SQ[2] + 1000, SQ[3] + 200]
    assert calib["s_axis"] == ["x", -1] and calib["v_axis"] == ["y", -1]
    assert wp.valid(calib)


@pytest.mark.parametrize("hue", [0.0, 0.08, 0.33, 0.55, 0.7, 0.9])
def test_ring_point_lands_on_the_target_hue(hue):
    calib = wp.analyse(picker())
    rgb = [int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.8, 0.9)]
    x, y = wp.ring_point(calib, rgb)
    angle = math.degrees(math.atan2(CY - y, x - CX)) % 360
    got = ring_hue(angle)
    assert min(abs(got - hue), 1 - abs(got - hue)) < 0.02
    assert R_IN <= math.hypot(x - CX, y - CY) <= R_OUT
    assert wp.ring_point(calib, (128, 128, 128)) is None        # greys: any hue fits


def test_square_point_and_refinement_hit_the_colour():
    calib = wp.analyse(picker(hue=0.0))
    target_hue = 0.6
    rgb = [int(c * 255) for c in colorsys.hsv_to_rgb(target_hue, 0.35, 0.7)]
    gx, gy = wp.square_point(calib, rgb)
    assert gx == pytest.approx(SQ[0] + 3 + 0.65 * (SQ[2] - SQ[0] - 6), abs=1)
    assert gy == pytest.approx(SQ[1] + 3 + 0.3 * (SQ[3] - SQ[1] - 6), abs=1)
    redrawn = picker(hue=target_hue)
    square = redrawn[SQ[1]:SQ[3] + 1, SQ[0]:SQ[2] + 1]
    assert wp.square_visible(calib, square)
    px, py = wp.refine(calib, square, rgb, (gx, gy))
    assert np.abs(np.asarray(square_colour(target_hue, px, py)) - rgb).max() <= 4
    # The previous click is hidden under the handle: never chosen.
    px, py = wp.refine(calib, square, rgb, (gx, gy), avoid=(gx, gy))
    assert math.hypot(px - gx, py - gy) > wp.HANDLE_RADIUS


def test_frames_without_the_picker_are_rejected():
    with pytest.raises(ValueError, match="кольцо"):
        wp.analyse(picker(sky=True))
    calib = wp.analyse(picker())
    sky = picker(sky=True)[SQ[1]:SQ[3] + 1, SQ[0]:SQ[2] + 1]
    assert not wp.square_visible(calib, sky)
    assert wp.refine(calib, None, (10, 20, 30), (150, 150)) == (150, 150)
    assert not wp.valid({"centre": [1, 2]})


def test_ring_check_tells_the_open_picker_from_anything_else():
    calib = wp.analyse(picker())
    left, top, right, bottom = wp.ring_box(calib)
    shown = picker()[max(0, top):bottom, max(0, left):right]
    assert shown.shape[:2] == (bottom - top, right - left)
    assert wp.ring_visible(calib, shown)
    assert not wp.ring_visible(calib, picker(sky=True)[max(0, top):bottom, max(0, left):right])
    swatches = np.zeros_like(shown)                    # another tab: a grid of colour buttons
    swatches[:, :] = (0, 230, 255)
    assert not wp.ring_visible(calib, swatches)


def program_hue(angle_deg):
    # Draw Me! takes the hue linearly from the angle, whatever its ring looks like.
    return ((90 - angle_deg) % 360) / 360.0


@pytest.mark.parametrize("hue", [0.04, 0.09, 0.3, 0.55, 0.9])
def test_a_ring_drawn_unlike_it_chooses_is_corrected_and_then_learned(hue):
    calib = wp.analyse(picker())
    rgb = [int(c * 255) for c in colorsys.hsv_to_rgb(hue, 0.8, 0.9)]
    angle = wp.ring_angle(calib, rgb)
    for clicks in range(1, 4):
        taken = program_hue(angle)
        square = picker(hue=taken)[SQ[1]:SQ[3] + 1, SQ[0]:SQ[2] + 1]
        measured = wp.square_hue(calib, square)
        assert abs(wp.hue_error(taken, measured)) < 0.01        # the square's corner tells the hue
        wp.remember(calib, angle, measured)
        error = wp.hue_error(hue, measured)
        if abs(error) <= wp.HUE_TOLERANCE:
            break
        angle = wp.corrected_angle(calib, angle, error)
    assert abs(error) <= wp.HUE_TOLERANCE and clicks <= 3
    near = (hue + 0.01) % 1.0                                     # the next colour: right at once
    rgb = [int(c * 255) for c in colorsys.hsv_to_rgb(near, 0.6, 0.7)]
    assert abs(wp.hue_error(near, program_hue(wp.ring_angle(calib, rgb)))) <= 0.015


def test_square_hue_ignores_a_handle_over_the_corner():
    calib = wp.analyse(picker())
    square = picker(hue=0.33)[SQ[1]:SQ[3] + 1, SQ[0]:SQ[2] + 1].copy()
    square[2:8, 2:8] = 255                                        # the white handle ring
    assert abs(wp.hue_error(0.33, wp.square_hue(calib, square))) < 0.01
    assert wp.square_hue(calib, np.zeros_like(square)) is None


def test_the_handle_hanging_below_the_square_is_not_part_of_it():
    # Live 2026-10-01: with black chosen the handle sat on the bottom edge and stuck
    # out; the square came out 3 px taller and the panel under it read as "not dark".
    img = picker(hue=0.08)
    yy, xx = np.mgrid[0:img.shape[0], 0:img.shape[1]]
    ring = np.hypot(xx - (SQ[0] + SQ[2]) // 2, yy - SQ[3])
    img[(ring >= 5) & (ring <= 7)] = 0                                 # black outline
    img[(ring >= 3) & (ring < 5)] = 255                                # white ring
    calib = wp.analyse(img)
    assert calib["square"] == list(SQ)
    shown = img[SQ[1]:SQ[3] + 1, SQ[0]:SQ[2] + 1]
    assert wp.square_visible(calib, shown)
    taller = dict(calib, square=[SQ[0], SQ[1], SQ[2], SQ[3] + 3])      # an older calibration
    assert wp.square_visible(taller, img[SQ[1]:SQ[3] + 4, SQ[0]:SQ[2] + 1])


def test_square_hue_is_read_even_with_the_handle_on_the_pure_corner():
    calib = wp.analyse(picker())
    square = picker(hue=0.6)[SQ[1]:SQ[3] + 1, SQ[0]:SQ[2] + 1].copy()
    square[0:22, 0:22] = 255                                      # a big handle over the whole corner
    assert abs(wp.hue_error(0.6, wp.square_hue(calib, square))) < 0.01


class _Program:
    """A Draw Me!-like picker on the virtual screen: a click on the ring sets the hue
    linearly from the angle, a click in the square chooses the colour shown there."""

    def __init__(self, engine, hidden=0):
        self.engine, self.hidden, self.hue, self.chosen = engine, hidden, 0.0, None
        self.seen = 0

    def _apply_clicks(self):
        events = self.engine._input.backend.events
        for i in range(self.seen, len(events)):
            if events[i][0] == "down":
                moves = [e for e in events[:i] if e[0] == "move"]
                x, y = moves[-1][1], moves[-1][2]
                if R_IN <= math.hypot(x - CX, y - CY) <= R_OUT:
                    self.hue = program_hue(math.degrees(math.atan2(CY - y, x - CX)) % 360)
                elif SQ[0] <= x <= SQ[2] and SQ[1] <= y <= SQ[3]:
                    self.chosen = tuple(square_colour(self.hue, x, y))
        self.seen = len(events)

    def capture(self, bbox=None, **_kwargs):
        from PIL import Image
        self._apply_clicks()
        if self.hidden:
            self.hidden -= 1
            full = picker(sky=True)
        else:
            full = picker(hue=self.hue)
        left, top, right, bottom = bbox if bbox else (0, 0, full.shape[1], full.shape[0])
        return Image.fromarray(full[top:bottom, left:right])


def _wheel_engine(monkeypatch, hidden):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from types import SimpleNamespace
    import engine.olegpainter.color_picking as cp
    import engine.olegpainter.core as core
    from engine.olegpainter.core import OlegPainter
    clock = {"t": 0.0}

    def advance(seconds=0.0, *_a, **_k):
        clock["t"] += max(0.0, float(seconds)) + 0.001

    fake = SimpleNamespace(sleep=advance, monotonic=lambda: clock["t"], perf_counter=lambda: clock["t"],
                           time=lambda: clock["t"], strftime=lambda *a: "t")
    monkeypatch.setattr(cp, "time", fake)
    monkeypatch.setattr(core.time, "sleep", advance)
    engine = OlegPainter(status_callback=lambda _m: None)
    engine.pen_button_delay = engine.mouse_release_settle = 0.0
    engine.wheel_square_calib = wp.analyse(picker())
    program = _Program(engine, hidden)
    monkeypatch.setattr(cp, "capture_screen", program.capture)
    evidence = []
    monkeypatch.setattr(engine, "_save_wheel_evidence", evidence.append)
    return engine, program, evidence


def test_wheel_pick_corrects_the_hue_and_waits_out_a_hidden_panel(monkeypatch):
    engine, program, evidence = _wheel_engine(monkeypatch, hidden=20)   # ~2 s of sky first
    target = [int(c * 255) for c in colorsys.hsv_to_rgb(0.09, 0.7, 0.85)]
    engine._pick_color_wheel_square("#%02X%02X%02X" % tuple(target))
    assert not engine.stop_flag and not evidence
    assert abs(wp.hue_error(0.09, program.hue)) <= wp.HUE_TOLERANCE
    program._apply_clicks()                                             # the final square click
    assert np.abs(np.asarray(program.chosen) - target).max() <= 6
    assert engine.wheel_square_calib["hue_measured"]                    # learned for the next colour


def test_wheel_pick_stops_without_clicking_when_the_panel_stays_hidden(monkeypatch):
    engine, program, evidence = _wheel_engine(monkeypatch, hidden=10 ** 6)
    engine._pick_color_wheel_square("#3366CC")
    assert engine.stop_flag and evidence
    assert not [e for e in engine._input.backend.events if e[0] == "down"]


def test_a_failed_capture_is_retried_not_taken_for_a_hidden_wheel(monkeypatch):
    # Live 2026-10-01: our own screen tools changed during a capture and the drawing
    # stopped at 95% as if the wheel were gone.
    engine, program, evidence = _wheel_engine(monkeypatch, hidden=0)
    real, failures = program.capture, [2]

    def flaky(bbox=None, **kwargs):
        if failures[0]:
            failures[0] -= 1
            raise RuntimeError("Screen tools changed during capture; repeat calibration")
        return real(bbox=bbox, **kwargs)

    import engine.olegpainter.color_picking as cp
    monkeypatch.setattr(cp, "capture_screen", flaky)
    engine._pick_color_wheel_square("#3366CC")
    assert not engine.stop_flag and not evidence
