"""Mixin extracted from engine/olegpainter/core.py.

Picking the current paint colour: hex field, HSV palette, manual palette.
"""
from __future__ import annotations

import logging

import math
from collections import deque
from pathlib import Path  # noqa: F401

from .common import *  # noqa: F401,F403
from .color_spaces import ciede2000
from infrastructure.screen_capture import capture_screen
from .translations import tr, LANGUAGES, current_lang  # noqa: F401
from .calibration import (  # noqa: F401
    calib_grab_screen,
    calib_show_fullscreen_top,
    calib_select_two_points,
    calib_select_region,
)
from .cluster_cleanup import apply_cleanup_mode  # noqa: F401
from .color_spaces import perceptual_lightness_from_rgb  # noqa: F401
from .prep_pipeline import build_perceptual_cluster_map  # noqa: F401
from .viewport_state import (  # noqa: F401
    VIEWPORT_UNSET,
    full_area_rect,
    normalize_area_rect,
    normalize_desktop_rect,
)


log = logging.getLogger("olegpainter.engine.olegpainter.color_picking")


class ColorPickingMixin:
    """Picking the current paint colour: hex field, HSV palette, manual palette."""

    # One game frame at 30 fps; typing faster lost repeated characters in Roblox.
    _HEX_CHAR_INTERVAL = 0.035

    def _hex_opens_by_actions(self) -> bool:
        return bool(getattr(self, "hex_field_opened_by_actions", False)) and self.hex_input_coord is not None

    def _hex_field_probe(self):
        """Small screenshot around the HEX field (None when it cannot be read)."""
        try:
            import numpy as np
            x, y = (int(v) for v in self.hex_input_coord)
            return np.asarray(capture_screen(bbox=(x - 12, y - 6, x + 12, y + 6)).convert("RGB"), dtype=np.int16)
        except Exception:
            log.debug("HEX field probe failed", exc_info=True)
            return None

    def _hex_field_changed(self, before) -> bool:
        after = self._hex_field_probe()
        if after is None or after.shape != before.shape:
            return True  # cannot tell: do not block drawing
        return float(abs(after - before).mean()) > 12.0

    def _pick_color_hex_field(self, hex_color: str):
        if self.hex_input_coord is None:
            err_msg = tr("status_error_hex_not_set");
            self._log(err_msg, True)
            self.status_callback("show_hex_error_popup:" + err_msg)
            self.stop_flag = True;
            self.drawing_enabled = False;
            return

        try:
            clean_hex = hex_color.lstrip('#');
            hex_to_write = f"#{clean_hex}" if getattr(self, 'hex_add_hash', False) else clean_hex;
            self._log(f"Выбор цвета (HEX): {hex_to_write}")
            self._click_abs(self.hex_input_coord[0], self.hex_input_coord[1]);
            time.sleep(0.05)
            self._input_click(button='left');
            time.sleep(0.05)
            self._input_click(button='left');
            time.sleep(0.15)
            self._input.send_keys("ctrl+a");
            time.sleep(0.1)
            # Type over the selection, one character per ~frame. Roblox Spray
            # Paint dropped repeated keys typed in one burst (#0040FF -> #00400F)
            # and an emptied field re-inserted "#" mid-typing.
            for char in hex_to_write:
                self._input.write_text(char)
                time.sleep(self._HEX_CHAR_INTERVAL)
            time.sleep(0.1)
            self._input.send_keys("enter");
            time.sleep(0.2)
        except Exception as e:
            self._log(f"Ошибка выбора цвета {hex_color} через HEX поле: {e}", True);
            self.stop_flag = True;
            self.drawing_enabled = False
            self.status_callback(tr("status_error_color_selection"))

    def _pick_color_hsv_palette(self, hex_color_str: str):
        if not self.circle_params_calib:
            self._log(tr("Круг цвета (HSV) не откалиброван!"), True)
            self.status_callback(
                "show_calibration_error_popup:" + tr("Круг цвета не откалиброван! Откалибруйте в Настройках."))
            self.stop_flag = True;
            self.drawing_enabled = False;
            return
        if not self.slider_params_calib:
            self._log(tr("Ползунок яркости (HSV) не откалиброван!"), True)
            self.status_callback(
                "show_calibration_error_popup:" + tr("Ползунок яркости не откалиброван! Откалибруйте в Настройках."))
            self.stop_flag = True;
            self.drawing_enabled = False;
            return

        hex_val = hex_color_str.lstrip('#').strip().upper()
        if not (len(hex_val) == 6 and all(c in "0123456789ABCDEF" for c in hex_val)):
            self._log(f"{tr('Неверный HEX формат для HSV палитры')}: {hex_val}", True);
            return

        try:
            r_int = int(hex_val[0:2], 16);
            g_int = int(hex_val[2:4], 16);
            b_int = int(hex_val[4:6], 16)
        except ValueError:
            self._log(f"{tr('Ошибка конвертации HEX в RGB для HSV')}: {hex_val}", True);
            return

        h_norm, s_norm, v_norm = colorsys.rgb_to_hsv(r_int / 255.0, g_int / 255.0, b_int / 255.0)
        # Clamp so FP/rounding can't push the click outside the wheel rim or slider track.
        s_norm = max(0.0, min(1.0, s_norm))
        v_norm = max(0.0, min(1.0, v_norm))

        cc_center_x, cc_center_y, cc_radius, angle_of_red_rad = self.circle_params_calib
        hue_angular_offset_rad = h_norm * (2 * math.pi)

        if self.palette_rotation_direction_calib == "ccw":
            final_target_angle_rad = angle_of_red_rad + hue_angular_offset_rad
        else:
            final_target_angle_rad = angle_of_red_rad - hue_angular_offset_rad

        target_radius_on_palette = s_norm * cc_radius
        click_x_circle = int(round(cc_center_x + target_radius_on_palette * math.cos(final_target_angle_rad)))
        click_y_circle = int(round(cc_center_y - target_radius_on_palette * math.sin(
            final_target_angle_rad)))

        orientation, fixed_coord, coord_one, coord_zero = self.slider_params_calib
        if orientation == "vertical":
            click_y_slider = int(round(coord_one * v_norm + coord_zero * (1.0 - v_norm)))
            click_x_slider = int(round(fixed_coord))
        else:
            click_x_slider = int(round(coord_zero * (1.0 - v_norm) + coord_one * v_norm))
            click_y_slider = int(round(fixed_coord))

        self._log(f"{tr('Выбор цвета (HSV)')}: {hex_color_str} -> H={h_norm:.2f} S={s_norm:.2f} V={v_norm:.2f}")
        self._log(
            f"  {tr('Координаты')}: {tr('Круг')}=({click_x_circle},{click_y_circle}), {tr('Слайдер')}=({click_x_slider},{click_y_slider})")

        CLICK_DELAY_AFTER_MOVE = 0.03
        PAUSE_BETWEEN_HSV_CLICKS = 0.1
        FINAL_DELAY_AFTER_HSV = 0.15

        try:
            # Wheel click: move -> settle -> click. The FIRST click can be eaten by the host
            # window's focus-follows-click, so click TWICE (focus insurance, mirroring the HEX
            # path) so the colour actually registers. The timing constants below are now used
            # (previously defined but dead) so the click never lands before the cursor settles.
            self._click_abs(click_x_circle, click_y_circle)
            time.sleep(CLICK_DELAY_AFTER_MOVE)
            self._input_click(button='left')
            time.sleep(0.03)
            self._input_click(button='left')
            time.sleep(PAUSE_BETWEEN_HSV_CLICKS)
            # Slider click: the window is focused now, a single click suffices.
            self._click_abs(click_x_slider, click_y_slider)
            time.sleep(CLICK_DELAY_AFTER_MOVE)
            self._input_click(button='left')
            time.sleep(FINAL_DELAY_AFTER_HSV)
            self._log(tr("Автовыбор цвета {hex_color} (HSV) завершен.", hex_color=hex_color_str))
        except Exception as e:
            self._log(f"{tr('Ошибка при клике (interception) для HSV палитры')}: {e}", True)
            self.stop_flag = True;
            self.drawing_enabled = False
            self.status_callback(tr("status_error_color_selection"))

    def _pick_color_manual_palette(self, hex_color_str: str, cluster_id: int | None = None):
        cache = self._get_manual_palette_cache()
        entries = cache.get("entries") if cache else []

        forced_idx = getattr(self, "_manual_palette_force_entry", None)
        if forced_idx is not None:
            entry = cache.get("by_index", {}).get(forced_idx) if cache else None
            alpha_override = getattr(self, "_manual_palette_force_alpha", None)
            self._manual_palette_force_entry = None
            self._manual_palette_force_alpha = None
            self._manual_palette_force_disable_mix = False
            if entry is None:
                self._log(f"Manual palette forced entry not found: {forced_idx}", True)
                return
            if alpha_override is not None:
                self._set_alpha_slider_value(alpha_override)
            self._activate_manual_palette_entry(entry)
            return

        if not entries:
            self._log(tr("status_error_manual_palette_not_set"), True)
            self.status_callback(tr("status_error_manual_palette_not_set"))
            self.stop_flag = True
            self.drawing_enabled = False
            return

        cleaned = hex_color_str.strip().lstrip('#')
        if len(cleaned) != 6:
            self._log(f"Manual palette invalid target color: {hex_color_str}", True)
            try:
                self.status_callback(tr("status_error_manual_palette_no_match", hex_color=hex_color_str))
            except Exception:
                log.debug("ignored exception in self.status_callback(tr('status_error_manual_palette_no_match', hex_color=hex...", exc_info=True)
            return
        try:
            target_rgb = tuple(int(cleaned[i:i+2], 16) for i in (0, 2, 4))
        except Exception:
            self._log(f"Manual palette cannot parse color: {hex_color_str}", True)
            return

        # Nearest entry in the configured match space (OKLab by default, "lab" =
        # legacy). The argmin is self-contained, entries carry both vectors.
        match_key = self._manual_match_key()
        target_vec = np.asarray(self._srgb_to_match_single(target_rgb), dtype=np.float32)
        # CIEDE2000 (perceptually accurate) for the final palette snap when
        # enabled; else the legacy squared-Euclidean in the match space. The
        # volume is tiny (one target x a few-dozen entries) so CIEDE2000 is free.
        _use_ciede = self._palette_match_metric_value() == "ciede2000"
        _target_lab = (np.asarray(self._srgb_to_lab_single(target_rgb), dtype=np.float64)
                       if _use_ciede else None)

        def _score(candidate) -> float:
            if _use_ciede:
                lab = candidate.get("lab")
                if lab is not None:
                    return float(ciede2000(_target_lab, np.asarray(lab, dtype=np.float64)))
            diff = target_vec - candidate[match_key]
            return float(np.dot(diff, diff))

        best_entry = None
        best_score = None

        cache_key = cleaned.upper()
        plan_idx = None
        if cluster_id is not None:
            plan = self._manual_palette_cluster_plan.get(cluster_id)
            if plan:
                plan_idx = plan.get("primary_idx")
        if plan_idx is not None and cache:
            candidate = cache["by_index"].get(plan_idx)
            if candidate is not None:
                best_score = _score(candidate)
                best_entry = candidate
        elif self._cluster_uses_manual_palette and cluster_id is not None and cache:
            candidate = cache["by_index"].get(cluster_id)
            if candidate is not None:
                best_score = _score(candidate)
                best_entry = candidate

        if best_entry is None and cache_key in self._manual_palette_match_cache:
            cached_idx = self._manual_palette_match_cache.get(cache_key)
            if cache and cached_idx is not None:
                candidate = cache["by_index"].get(cached_idx)
                if candidate is not None:
                    best_score = _score(candidate)
                    best_entry = candidate

        for entry in entries:
            score = _score(entry)
            if best_score is None or score < best_score:
                best_score = score
                best_entry = entry

        if not best_entry:
            self._log(tr("status_error_manual_palette_no_match", hex_color=hex_color_str), True)
            self.status_callback(tr("status_error_manual_palette_no_match", hex_color=hex_color_str))
            self.stop_flag = True
            self.drawing_enabled = False
            return

        primary_index = int(best_entry["index"])
        self._manual_palette_match_cache[cache_key] = primary_index

        x = int(best_entry.get('x', 0))
        y = int(best_entry.get('y', 0))
        try:
            if self._automation_cancelled():
                return
            self._click_abs(x, y)
            if not self._sleep_with_abort(0.05):
                return
            if self._automation_cancelled():
                return
            self._input_click(button='left')
            if not self._sleep_with_abort(0.05):
                return
        except Exception as exc:
            self._log(f"Manual palette pick error: {exc}", True)
            self.stop_flag = True
            self.drawing_enabled = False
            self.status_callback(tr("status_error_color_selection"))
            return

    _WHEEL_RENDER_WAIT = 0.12   # the square is redrawn for the new hue on the next frames
    _WHEEL_HUE_ATTEMPTS = 3     # ring clicks per colour: the first, then up to two corrections

    # s: games hide their UI now and then (live Draw Me! 2026-10-01: the whole panel
    # was gone for over 1.5 s mid-drawing, the sky showed through, twice in an hour)
    _WHEEL_VISIBLE_WAIT = 6.0
    _WHEEL_NOTICE_AFTER = 1.0

    def _wheel_picture(self, box, visible):
        """A fresh picture of `box` that passes `visible`, or None (the picker is not open)."""
        started = time.monotonic()
        deadline = started + self._WHEEL_VISIBLE_WAIT
        picture = None
        noticed = False
        while True:
            try:
                picture = np.asarray(capture_screen(bbox=box).convert("RGB"))
            except Exception:
                # Our own screen tools changing at that moment fail a capture
                # (live 2026-10-01: a drawing stopped at 95%): try again.
                log.warning("wheel capture failed; retrying", exc_info=True)
                picture = None
            if picture is not None and visible(picture):
                return picture
            if time.monotonic() >= deadline or self._automation_cancelled():
                break
            if not noticed and time.monotonic() - started >= self._WHEEL_NOTICE_AFTER:
                noticed = True
                self.status_callback("Цветовое колесо пропало с экрана — ждём, пока оно появится снова…")
            time.sleep(0.1)
        self._save_wheel_evidence(picture)
        return None

    def _save_wheel_evidence(self, picture):
        """What the screen showed when the wheel was missing: for support, next to the logs."""
        try:
            from app_paths import get_app_paths
            from PIL import Image
            folder = get_app_paths().app_root / "logs"
            folder.mkdir(parents=True, exist_ok=True)
            name = folder / time.strftime("wheel_not_visible_%Y%m%d_%H%M%S.png")
            if picture is not None:
                Image.fromarray(np.asarray(picture, dtype=np.uint8)).save(name)
            screen = capture_screen().convert("RGB")             # what covered it
            screen.thumbnail((1280, 1280))
            screen.save(name.with_name(name.stem + "_screen.png"))
            self._log(f"Снимок места колеса сохранён: {name}")
        except Exception:
            log.debug("wheel evidence not saved", exc_info=True)

    def _wheel_not_open(self, message):
        self._log(message, True)
        self._drawing_failure_message = message
        self.status_callback("error: " + message)
        self.stop_flag = True
        self.drawing_enabled = False

    def _pick_color_wheel_square(self, hex_color_str: str):
        """Ring for the hue, square for saturation and brightness (WHEEL-001)."""
        from engine.olegpainter import wheel_picker
        calib = getattr(self, "wheel_square_calib", None)
        if not wheel_picker.valid(calib):
            message = "Цветовое колесо не откалибровано: обведите его рамкой (кнопка «Настроить цвет»)."
            self._log(message, True)
            self.status_callback("show_calibration_error_popup:" + message)
            self.stop_flag = True
            self.drawing_enabled = False
            return
        cleaned = hex_color_str.strip().lstrip('#')
        try:
            rgb = tuple(int(cleaned[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            self._log(f"{tr('Неверный HEX формат для палитры')}: {hex_color_str}", True)
            return
        hidden = ("Цветовое колесо не видно на экране: откройте в программе вкладку с колесом "
                  "(в «Нарисуй меня!» — «Цвет» → «Колесо») и не сдвигайте его. Если окно сдвинулось, обведите колесо заново.")
        try:
            # Clicking blind would pick whatever lies there now (a grid of swatches, the canvas).
            if self._wheel_picture(wheel_picker.ring_box(calib),
                                   lambda picture: wheel_picker.ring_visible(calib, picture)) is None:
                self._wheel_not_open(hidden)
                return
            target = wheel_picker.target_hue(rgb)
            angle = wheel_picker.ring_angle(calib, rgb)
            left, top, right, bottom = calib["square"]
            ring, taken, picture = None, None, None
            for _attempt in range(self._WHEEL_HUE_ATTEMPTS):
                if angle is not None:
                    ring = wheel_picker.ring_xy(calib, angle)
                    self._click_abs(*ring)
                    time.sleep(0.03)
                    self._input_click(button='left')
                    time.sleep(self._WHEEL_RENDER_WAIT)
                picture = self._wheel_picture((left, top, right + 1, bottom + 1),
                                              lambda picture: wheel_picker.square_visible(calib, picture))
                if picture is None:
                    self._wheel_not_open(hidden)
                    return
                taken = wheel_picker.square_hue(calib, picture) if angle is not None else None
                if taken is None:
                    break
                # The square shows the hue the program took: learn it, correct a miss.
                calib = dict(calib)
                wheel_picker.remember(calib, angle, taken)
                self.wheel_square_calib = calib
                error = wheel_picker.hue_error(target, taken)
                if abs(error) <= wheel_picker.HUE_TOLERANCE:
                    break
                angle = wheel_picker.corrected_angle(calib, angle, error)
            guess = wheel_picker.square_point(calib, rgb)
            point = wheel_picker.refine(calib, picture, rgb, guess, avoid=self._wheel_last_square)
            self._click_abs(*point)
            time.sleep(0.03)
            self._input_click(button='left')
            time.sleep(0.08)
            self._wheel_last_square = point
            hue_note = "" if taken is None else f" (оттенок {taken * 360:.0f}°, нужен {target * 360:.0f}°)"
            self._log(f"Выбор цвета (кольцо и квадрат): {hex_color_str} -> кольцо {ring}{hue_note}, квадрат {point}")
        except Exception as e:
            self._log(f"Ошибка выбора цвета {hex_color_str} на колесе: {e}", True)
            self.stop_flag = True
            self.drawing_enabled = False
            self.status_callback(tr("status_error_color_selection"))

    def _pick_color_screen_palette(self, hex_color_str: str):
        """Empirical 'рамка' palette pick. Nearest sampled pixel (CIELAB) -> click the colour
        area for hue/saturation; if a value slider was calibrated, match hue/sat on the
        FULL-value version of the target (the wheel is rendered at V=1) and set brightness on
        the slider. Layout-agnostic (any wheel/ring+square/grid)."""
        calib = getattr(self, "screen_palette_calib", None)
        lab_arr = calib.get("lab") if isinstance(calib, dict) else None
        if lab_arr is None or len(lab_arr) == 0:
            self._log(tr("Палитра (рамкой) не откалибрована!"), True)
            self.status_callback(
                "show_calibration_error_popup:" + tr("Палитра не откалибрована! Откалибруйте в Настройках."))
            self.stop_flag = True
            self.drawing_enabled = False
            return

        cleaned = hex_color_str.strip().lstrip('#')
        if len(cleaned) != 6:
            self._log(f"{tr('Неверный HEX формат для палитры')}: {hex_color_str}", True)
            return
        try:
            target_rgb = tuple(int(cleaned[i:i + 2], 16) for i in (0, 2, 4))
        except Exception:
            self._log(f"{tr('Ошибка конвертации HEX')}: {hex_color_str}", True)
            return

        slider = calib.get("slider") if isinstance(calib, dict) else None
        mx = max(target_rgb) or 1
        # With a value slider the wheel is rendered at full value -> match hue/saturation on
        # the full-brightness version of the target so dark colours still pick the right hue.
        if slider:
            match_rgb = tuple(min(255, int(round(c * 255.0 / mx))) for c in target_rgb)
        else:
            match_rgb = target_rgb
        target_lab = np.asarray(self._srgb_to_lab_single(match_rgb), dtype=np.float32)
        if self._palette_match_metric_value() == "ciede2000":
            # Perceptually accurate nearest swatch over the calibrated palette
            # samples (one target vs <=3000 swatches -> still cheap).
            idx = int(np.argmin(ciede2000(target_lab.astype(np.float64),
                                          np.asarray(lab_arr, dtype=np.float64))))
        else:
            diffs = lab_arr - target_lab
            idx = int(np.argmin(np.einsum('ij,ij->i', diffs, diffs)))
        coords = calib["coords"]
        wx, wy = int(coords[idx][0]), int(coords[idx][1])

        slider_click = None
        if slider:
            try:
                orient, fixed, v1c, v0c = slider
                v = mx / 255.0   # HSV Value = max(R,G,B)
                pos = int(round(v1c * v + v0c * (1.0 - v)))
                slider_click = (int(fixed), pos) if orient == "vertical" else (pos, int(fixed))
            except Exception:
                slider_click = None

        self._log(tr("Выбор цвета (палитра): {hex} -> круг ({x},{y}){sl}",
                     hex=hex_color_str, x=wx, y=wy,
                     sl=(f" + ползунок {slider_click}" if slider_click else "")))
        try:
            if self._automation_cancelled():
                return
            # Colour-area click (hue, saturation). Focus insurance: the first click can be
            # eaten by host-window focus -> click twice.
            self._click_abs(wx, wy)
            time.sleep(0.03)
            self._input_click(button='left')
            time.sleep(0.03)
            self._input_click(button='left')
            time.sleep(0.1)
            # Brightness slider click (value), if calibrated.
            if slider_click is not None:
                if self._automation_cancelled():
                    return
                self._click_abs(slider_click[0], slider_click[1])
                time.sleep(0.03)
                self._input_click(button='left')
                time.sleep(0.1)
        except Exception as exc:
            self._log(f"Screen palette pick error: {exc}", True)
            self.stop_flag = True
            self.drawing_enabled = False
            self.status_callback(tr("status_error_color_selection"))

    def pick_color(self, hex_color_str: str, cluster_id: int | None = None):
        if self.stop_flag: return

        if self.color_picking_method == "manual_palette":
            cluster_ready = getattr(self, "_cluster_uses_manual_palette", False)
            mix_enabled = getattr(self, "manual_palette_mix_enabled", False)
            if mix_enabled and not cluster_ready:
                # Для режима смешивания обязательно требуется карта кластеров ручной палитры.
                err_msg = tr("status_error_cannot_define_manual_palette_drawing")
                self._log(err_msg + " (no manual-palette cluster map).", True)
                self.status_callback(err_msg)
                self.stop_flag = True
                self.drawing_enabled = False
                return

        if self._should_play_actions("pre"):
            guarded = self.color_picking_method == "hex_field" and self._hex_opens_by_actions()
            before = self._hex_field_probe() if guarded else None
            if not self._play_actions_sequence("pre"):
                return
            if before is not None and not self._hex_field_changed(before):
                # The picker did not open (page busy): once more, then refuse to type
                # the code into the program itself (Gartic Phone reads 1-9, E, B... as tools).
                if not self._play_actions_sequence("pre"):
                    return
                if not self._hex_field_changed(before):
                    message = tr("Окно выбора цвета не открылось: поле HEX не появилось после записанных действий. "
                                 "Проверьте «Записать открытие» на странице «Быстрый старт».")
                    self._log(message, True)
                    self._drawing_failure_message = message
                    self.status_callback("error: " + message)
                    self.stop_flag = True
                    self.drawing_enabled = False
                    return

        if self.color_picking_method == "hex_field":
            self._pick_color_hex_field(hex_color_str)
        elif self.color_picking_method == "hsv_palette":
            self._pick_color_hsv_palette(hex_color_str)
        elif self.color_picking_method == "manual_palette":
            self._pick_color_manual_palette(hex_color_str, cluster_id=cluster_id)
        elif self.color_picking_method == "screen_palette":
            self._pick_color_screen_palette(hex_color_str)
        elif self.color_picking_method == "wheel_square":
            self._pick_color_wheel_square(hex_color_str)
        else:
            self._log(f"{tr('Неизвестный метод выбора цвета')}: {self.color_picking_method}", True)
            self.status_callback(
                tr("Ошибка: Неизвестный метод выбора цвета '{method}'.", method=self.color_picking_method))
            self.stop_flag = True;
            self.drawing_enabled = False;
            return

        if self._should_play_actions("post"):
            if not self._play_actions_sequence("post"):
                return
