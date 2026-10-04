# -*- coding: utf-8 -*-
"""PROOF HARNESS for dynamic brush v2.

Builds a virtual paint app (numpy canvas + a brush whose REAL radius is a
non-linear function of the control value) and wires the REAL engine to it
through the same boundaries the real mouse uses (_move_abs / interception /
_grab_patch). Then:

  [CAL]  the real v2 calibration runs against the virtual app (real probes,
         real screenshot diffs, real measurement code);
  [A]    the app is then secretly "rescaled" x1.8 (stale calibration — the
         live-bug scenario) and the TIER pass runs WITH the draw-time closed
         loop (shipped code);
  [B]    same stale app, but the closed loop is replaced with "trust the
         curve" (v1 behaviour) — reproduces the user's spill;
  [C]    full pipeline on the stale app: base verify + tiers + base edge pass,
         two adjacent colors.

Outputs spill/coverage metrics and PNGs into _proof_dynamic_brush/.
"""
from __future__ import annotations

import math
import os
import sys
from unittest.mock import patch, MagicMock

import numpy as np
import cv2

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PIL import Image
from engine.olegpainter.core import OlegPainter

OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "_proof_dynamic_brush")
os.makedirs(OUT_DIR, exist_ok=True)

SCREEN_W, SCREEN_H = 2200, 660
REGION = (40, 40, 320, 320)          # draw region on the virtual screen (px)
SCRATCH = (700, 40, 1400, 560)       # scratch zone, far from the artwork (wide:
                                     # calibration stamps up to ~60px must not overlap)
DRIFT_HEAVY = 4.0                    # uncontrollable drift (A/B contrast)
DRIFT_MILD = 1.6                     # moderate drift (full pipeline C)
BRUSH_CELL = 4                       # engine grid step (px); base stamp ~4px wide
WHITE = (255, 255, 255)
COLOR0 = (180, 60, 30)               # BGR-ish; just distinct tuples
COLOR1 = (30, 140, 230)


class VirtualPaintApp:
    """A fake paint program: canvas + a brush with a NON-LINEAR real radius."""

    def __init__(self):
        self.canvas = np.full((SCREEN_H, SCREEN_W, 3), 255, np.uint8)
        self.value = 0.05
        self.scale = 1.0              # secret drift after calibration (stale curve)
        self.paint = (0, 0, 0)
        self.pos = (0, 0)
        self.pen_down = False
        self.stamps = 0

    def real_radius(self) -> float:
        # strongly non-linear control, like a real slider: 2px .. ~60px
        return (2.0 + 58.0 * (float(self.value) ** 2.4)) * self.scale

    def stamp(self):
        r = max(1, int(round(self.real_radius())))
        cv2.circle(self.canvas, (int(self.pos[0]), int(self.pos[1])), r, self.paint, -1)
        self.stamps += 1

    def move(self, x, y):
        x, y = int(x), int(y)
        if self.pen_down:
            r = max(1, int(round(self.real_radius())))
            cv2.line(self.canvas, self.pos, (x, y), self.paint, thickness=2 * r + 1)
            cv2.circle(self.canvas, self.pos, r, self.paint, -1)
            cv2.circle(self.canvas, (x, y), r, self.paint, -1)
        self.pos = (x, y)

    def grab(self, left, top, right, bottom) -> Image.Image:
        left = max(0, left); top = max(0, top)
        right = min(SCREEN_W, right); bottom = min(SCREEN_H, bottom)
        return Image.fromarray(self.canvas[top:bottom, left:right].copy())


def make_engine(app: VirtualPaintApp) -> OlegPainter:
    e = OlegPainter()
    e.brush_size = BRUSH_CELL
    e.draw_region = REGION
    e.dynamic_brush_scratch_zone = SCRATCH
    e.dynamic_brush_control_mode = "text"
    e.dynamic_brush_coord = (1, 1)  # control exists; apply itself is wired below
    e.update_dynamic_brush_settings(min_value=0.05, max_value=1.0, default_value=0.05, step=0.01)
    e.draw_delay = 0.0
    e.pen_settle_delay = 0.0
    e.pen_button_delay = 0.0
    e.mouse_release_settle = 0.0
    e.drawing_enabled = True
    e.stop_flag = False
    e.size_changes = 0

    def apply_value(value, force=False):
        target = e._quantize_dynamic_brush_value(float(value))
        app.value = target
        e.size_changes += 1
        e._last_dynamic_brush_value = target
        return True

    e._apply_dynamic_brush_value = apply_value                      # control boundary
    e._move_abs = lambda x, y: app.move(x, y)                       # pen movement boundary
    e._click_abs = lambda x, y: app.move(x, y)                      # UI-click positioning boundary
    e._grab_patch = lambda cx, cy, half: app.grab(cx - half, cy - half, cx + half, cy + half)
    return e


def fake_interception(app: VirtualPaintApp) -> MagicMock:
    m = MagicMock()
    m.move_to.side_effect = lambda x, y: app.move(x, y)

    def _down(*a, **k):
        app.pen_down = True
        app.stamp()

    def _up(*a, **k):
        app.pen_down = False

    m.mouse_down.side_effect = _down
    m.mouse_up.side_effect = _up
    m.click.side_effect = lambda *a, **k: app.stamp()
    return m


def color_setup(e: OlegPainter):
    """Color 0: big square. Color 1: frame DIRECTLY around it (spill victim)."""
    grid = REGION[2] // BRUSH_CELL
    cm = np.full((grid, grid), -1, dtype=int)
    cm[4:76, 4:76] = 1     # 6-cell frame (the neighbour color)
    cm[10:70, 10:70] = 0   # 60x60-cell core
    e.cluster_map = cm
    e.drawn_mask = np.zeros_like(cm, dtype=bool)
    return cm


def region_px_mask(cm, cluster_id):
    px = np.kron(cm == cluster_id, np.ones((BRUSH_CELL, BRUSH_CELL), dtype=bool))
    full = np.zeros((SCREEN_H, SCREEN_W), dtype=bool)
    full[REGION[1]:REGION[1] + px.shape[0], REGION[0]:REGION[0] + px.shape[1]] = px
    return full


def changed_mask(before, after):
    return np.any(before != after, axis=2)


def spill_report(tag, before, app, cm, own_color_ids):
    ch = changed_mask(before, app.canvas)
    own = np.zeros((SCREEN_H, SCREEN_W), dtype=bool)
    for cid in own_color_ids:
        own |= region_px_mask(cm, cid)
    scratch = np.zeros((SCREEN_H, SCREEN_W), dtype=bool)
    scratch[SCRATCH[1]:SCRATCH[1] + SCRATCH[3], SCRATCH[0]:SCRATCH[0] + SCRATCH[2]] = True
    region = np.zeros((SCREEN_H, SCREEN_W), dtype=bool)
    region[REGION[1]:REGION[1] + REGION[3], REGION[0]:REGION[0] + REGION[2]] = True
    spill = ch & ~own & ~scratch
    outside_region = ch & ~region & ~scratch
    spill_px = int(spill.sum())
    out_px = int(outside_region.sum())
    max_dist = 0.0
    if spill_px:
        dt = cv2.distanceTransform((~own).astype(np.uint8), cv2.DIST_L2, 5)
        max_dist = float(dt[spill].max())
    covered = int((ch & own).sum())
    own_total = int(own.sum())
    print(f"[{tag}] painted-own: {covered}/{own_total}px ({covered / max(1, own_total):.1%})  "
          f"SPILL beyond own color: {spill_px}px (max reach {max_dist:.1f}px)  "
          f"beyond draw region: {out_px}px  size-changes: {getattr(app, '_e_sizes', '?')}")
    return spill, spill_px, out_px, max_dist, covered, own_total


def save_png(name, app, spill=None):
    img = app.canvas.copy()
    cv2.rectangle(img, (REGION[0], REGION[1]), (REGION[0] + REGION[2], REGION[1] + REGION[3]), (0, 180, 0), 1)
    cv2.rectangle(img, (SCRATCH[0], SCRATCH[1]), (SCRATCH[0] + SCRATCH[2], SCRATCH[1] + SCRATCH[3]), (160, 160, 160), 1)
    if spill is not None and spill.any():
        img[spill] = (0, 0, 255)  # highlight spill in pure red
    cv2.imwrite(os.path.join(OUT_DIR, name), img)


def draw_target_png(cm):
    app = VirtualPaintApp()
    px0 = region_px_mask(cm, 0); px1 = region_px_mask(cm, 1)
    app.canvas[px0] = COLOR0
    app.canvas[px1] = COLOR1
    save_png("01_target.png", app)


def main():
    print("=== PROOF: dynamic brush v2 on a virtual lying paint app ===")
    print(f"brush grid: {BRUSH_CELL}px cell; app brush radius(v) = 2 + 58*v^2.4 px (non-linear)")

    # ---------- [CAL] real calibration against the honest app ----------
    cal_app = VirtualPaintApp()
    cal_app.paint = (20, 20, 20)
    e_cal = make_engine(cal_app)
    fake_cal = fake_interception(cal_app)
    with patch("engine.olegpainter.core.interception", fake_cal), \
         patch("engine.olegpainter.dynamic_brush.interception", fake_cal), \
         patch("engine.olegpainter.core.time.sleep", lambda *_: None):
        payload = e_cal._run_dynamic_brush_calibration(
            calibration_region=SCRATCH, persist=False, status_messages=False
        )
    assert isinstance(payload, dict), "calibration failed"
    rows = [(r["value"], r["point_reach_cells"] * BRUSH_CELL) for r in payload["samples"]]
    errs = [abs(meas - (2.0 + 58.0 * (v ** 2.4))) / (2.0 + 58.0 * (v ** 2.4)) for v, meas in rows]
    print(f"[CAL] real v2 calibration on virtual app: {len(rows)} probes "
          f"(5 base + adaptive + verify), worst radius error {max(errs):.1%}, "
          f"verification passed={payload['validation']['passed']}")
    for v, r in sorted(rows):
        print(f"      value={v:<6} measured r={r:6.2f}px   true r={2.0 + 58.0 * (v ** 2.4):6.2f}px")

    def fresh(scale, verify=False):
        app = VirtualPaintApp()
        app.scale = scale
        app.paint = COLOR0
        e = make_engine(app)
        e.dynamic_brush_verify_at_draw = bool(verify)
        e.dynamic_brush_calibration = dict(payload)
        cm = color_setup(e)
        return app, e, cm

    # ---------- [A] DRIFTING app (x4 after calibration), opt-in closed loop ON ----------
    app_a, e_a, cm = fresh(scale=DRIFT_HEAVY, verify=True)
    before = app_a.canvas.copy()
    fake_a = fake_interception(app_a)
    with patch("engine.olegpainter.core.interception", fake_a), \
         patch("engine.olegpainter.dynamic_brush.interception", fake_a), \
         patch("engine.olegpainter.core.time.sleep", lambda *_: None):
        ran_a = e_a._dynamic_brush_v2_run_tiers(REGION[0], REGION[1], 0)
    app_a._e_sizes = e_a.size_changes
    spill_a, sp_a, out_a, dist_a, _, _ = spill_report("A: closed loop, app drifted", before, app_a, cm, (0,))
    save_png("02_A_closed_loop.png", app_a, spill_a)

    # ---------- [B] DRIFTING app, verify OFF (trust) — shows why a drifting target needs verify ----------
    app_b, e_b, _ = fresh(scale=DRIFT_HEAVY, verify=True)

    def blind_trust(value):
        e_b._apply_dynamic_brush_value(value, force=True)
        return True, e_b._dynamic_brush_v2_radius_px_for_value(value)  # believe the curve

    e_b._dynamic_brush_v2_apply_and_measure = blind_trust
    before = app_b.canvas.copy()
    fake_b = fake_interception(app_b)
    with patch("engine.olegpainter.core.interception", fake_b), \
         patch("engine.olegpainter.dynamic_brush.interception", fake_b), \
         patch("engine.olegpainter.core.time.sleep", lambda *_: None):
        ran_b = e_b._dynamic_brush_v2_run_tiers(REGION[0], REGION[1], 0)
    app_b._e_sizes = e_b.size_changes
    spill_b, sp_b, out_b, dist_b, _, _ = spill_report("B: BLIND TRUST (old way), same drift ", before, app_b, cm, (0,))
    save_png("03_B_blind_trust.png", app_b, spill_b)

    # ---------- [C] DETERMINISTIC app (MS Paint: calibration == reality), TRUST default ----------
    app_c, e_c, _ = fresh(scale=1.0)  # the user's actual case
    e_c._dyn2_base_value = None
    before = app_c.canvas.copy()
    fake_c = fake_interception(app_c)
    with patch("engine.olegpainter.core.interception", fake_c), \
         patch("engine.olegpainter.dynamic_brush.interception", fake_c), \
         patch("engine.olegpainter.core.time.sleep", lambda *_: None):
        for cid, paint in ((0, COLOR0), (1, COLOR1)):
            app_c.paint = paint
            mask_u8 = ((e_c.cluster_map == cid) & (~e_c.drawn_mask)).astype(np.uint8) * 255
            e_c.dfs_4dir_dynamic_fill_current_color(mask_u8, REGION[0], REGION[1], cid)
    app_c._e_sizes = e_c.size_changes
    spill_c, sp_c, out_c, dist_c, cov_c, tot_c = spill_report(
        "C: FULL fill (verify+tiers+edge pass) ", before, app_c, cm, (0, 1)
    )
    cells_done = bool(np.all(e_c.drawn_mask[e_c.cluster_map >= 0]))
    print(f"[C] all color cells covered: {cells_done}; total size-control touches: {e_c.size_changes} "
          f"(per-cell v1 would need thousands)")
    save_png("04_C_full_pipeline.png", app_c, spill_c)
    draw_target_png(cm)

    print()
    print("VERDICT:")
    print(f"  tiers ran: A={ran_a} B={ran_b}")
    print(f"  spill beyond own color  A(closed loop)={sp_a}px  vs  B(blind trust)={sp_b}px")
    print(f"  max spill reach          A={dist_a:.1f}px        vs  B={dist_b:.1f}px")
    print(f"  beyond draw region       A={out_a}px             vs  B={out_b}px")
    print(f"  full pipeline C: coverage {cov_c / max(1, tot_c):.1%}, spill {sp_c}px (max {dist_c:.1f}px, "
          f"= base-brush tolerance ring), beyond region {out_c}px")
    print(f"  PNGs: {OUT_DIR}")


if __name__ == "__main__":
    main()


