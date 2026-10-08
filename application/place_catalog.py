"""Target application capabilities shared by all presentation layers."""

PLACE_OPTIONS = [
        {"id": "speed_draw", "file": "Speed Draw!", "key": "settings_place_option_speed"},
        {"id": "spray_paint", "file": "Spray Paint!", "key": "settings_place_option_spray"},
        {"id": "draw_donate", "file": "Draw & Donate", "key": "settings_place_option_donate"},
        {"id": "gartic_phone", "file": "Gartic Phone", "key": "settings_place_option_gartic"},
        {"id": "draw_me", "file": "Draw Me!", "key": "settings_place_option_draw_me"},
        {"id": "rust_sign", "file": "Rust", "key": "settings_place_option_rust"},
        {"id": "universal", "file": "Универсальный", "key": "settings_place_option_universal"},
        {"id": "screen_palette", "file": "Палитра рамкой", "key": "settings_place_option_screen"},
    ]

# «Палитра рамкой» is a colour method, not a place: the main window offers it as
# a method of «Другая программа», and saved sessions move there (PLACES-002).
# The classic shell still lists it until it is removed.
LEGACY_SCREEN_PLACE = "screen_palette"

# One recommended start for every place (PRESET-BASE-001, owner 2026-10-06): a place
# chosen for the first time takes these, then its own "defaults"; its "settings" are
# kept on every selection. The engine defaults stay neutral for tests and old profiles.
RECOMMENDED_SETTINGS = {
    # Brush wider than a cell keeps every colour (ORDER-DETAILS-001: 79-82 % kept
    # instead of 47-67 %); with an exact brush the order changes nothing.
    "tone_sequence": "details_last",
    "area_sequence": "nearest",
    # Owner 2026-10-07 after a live Spray Paint! comparison: CIELAB kept the red
    # glasses and the blue shades apart where OKLab merged them.
    "prep_color_space": "cielab",
    # Paint: 100 % at 1-2 ms between moves and 5 ms after a move (ROUTE-PACING-001);
    # Spray Paint keeps turns from 1 ms and lifts from 2 ms. 5.5 / 30 / 10 ms of the
    # engine cost ~80 ms per pen lift.
    "draw_delay": 0.002,
    "pen_button_delay": 0.005,
    "pen_settle_delay": 0.005,
}

# Roblox reads the mouse once a frame: a press or release held shorter than a frame
# is missed (live Speed Draw! 2026-10-06 at 5 ms: white gaps, strokes running on across
# the eyes). 30 ms drew clean pictures there before. Moves between turns: a 10-turn
# zigzag at 2 ms a turn came out as one bent line, at 5 and 10 ms whole (live Speed
# Draw! probe 2026-10-06) — the game takes only a few moves a frame and joins what is
# left with straight lines: diagonals across the picture. 6 ms keeps a margin.
ROBLOX_FRAME_PRESS = {"pen_button_delay": 0.03, "draw_delay": 0.006}

PLACE_PRESETS = {
        "speed_draw": {
            "algorithms": ["dfs_4dir", "line_cover", "outline_and_fill", "outline_only"],
            "algo_enabled": True,
            # «Прямые отрезки» everywhere: the fewest strokes, every turn of a stroke
            # costs a frame of the game (cat 4007 strokes against 15099 of DFS).
            "default_algo": "line_cover",
            "method_id": "hsv_palette",
            "method_text_key": "settings_method_hsv_label",
            # Live 2026-10-01: the game reads the mouse once a frame; without the
            # nudge every jump between areas was painted as a straight line.
            "settings": {"pen_press_nudge": True},
            "defaults": dict(ROBLOX_FRAME_PRESS),
        },
        "spray_paint": {
            "algorithms": ["dfs_4dir", "line_cover", "outline_and_fill", "outline_only"],
            "algo_enabled": True,
            "default_algo": "line_cover",
            "method_id": "hex_field",
            "method_text_key": "settings_method_hex_label",
            # The HEX box re-inserts "#" and moves the caret when a bare code is
            # typed, rotating the first character to the end (0040FF -> 040FF0).
            # The game reads the mouse once a frame: a press right after a jump
            # paints along the jump unless the cursor is nudged first.
            "settings": {"hex_add_hash": True, "pen_press_nudge": True},
            # Measured in the game (rb_learn.py, 2026-09-29): one long move is ~6 separate
            # stamps, 2 px steps make a solid line, lifts pass from 2 ms (4 chosen).
            # Live 2026-10-07 under the usual top-down camera the 0.1 stamp is 5 x 4 px:
            # squares filled at 2 px rows were solid from 1.5-2 ms a hop (1 ms: 96-98 %),
            # 4 px rows striped at any pace. «Подобрать скорость» may change them.
            "defaults": {**ROBLOX_FRAME_PRESS, "draw_delay": 0.002, "pen_max_step": 2, "pen_settle_delay": 0.004,
                         "brush_size": 2},
            # Size box accepts 0.1..1.2 without a gamepass; integer auto ladder
            # would only ever try 1 and the clamped maximum.
            "brush": {"control_mode": "text", "text_auto": False, "min_value": 0.1, "max_value": 1.2,
                      "step_value": 0.1, "default_value": 0.1},
        },
        "draw_donate": {
            "algorithms": ["dfs_4dir", "line_cover", "outline_and_fill", "outline_only"],
            "algo_enabled": True,
            "default_algo": "line_cover",
            "method_id": "hex_field",
            "method_text_key": "settings_method_hex_label",
            # Like Speed Draw!, Spray Paint! and «Нарисуй меня!» (all Roblox, all read the
            # mouse once a frame). Live 2026-10-07, canvas «Detailed» 700 x 700 on a
            # 2560 x 1440 screen: game brush 1 stamps 4 px on screen (2: 7, 3: 9); squares
            # of 2-3 px rows were solid from 2 ms a hop (0.5 ms: 99.3 %).
            "settings": {"pen_press_nudge": True},
            "defaults": {**ROBLOX_FRAME_PRESS, "draw_delay": 0.002, "pen_max_step": 2, "brush_size": 2},
        },
        "gartic_phone": {
            "algorithms": ["dfs_4dir", "line_cover", "outline_and_fill", "outline_only"],
            "algo_enabled": True,
            # GARTIC-003: Spongebob 32.5 s against 121 s of DFS, every colour in place.
            "default_algo": "line_cover",
            # Any colour: the big colour tile opens a picker whose R/G/B row turns
            # into a HEX box after two clicks on its toggle (it reopens in RGB, so
            # the three clicks are recorded as actions before each colour).
            "method_id": "hex_field",
            "method_text_key": "settings_method_hex_label",
            # The page reads the mouse once a frame and smooths a held stroke into
            # curves (turns are cut), and drops strokes pressed sooner than ~2 frames
            # after a release (live, 2026-09-30). The pause itself is measured by the
            # speed step (pen_stroke_gap, 0 = automatic): the preset must not reset it.
            # Thinnest game brush (key 1) is 4 px of the 1516 px canvas: about 3 screen
            # px on a maximised browser, so 2 px cells close without seams.
            "settings": {"pen_split_strokes": True, "draw_delay": 0.0, "brush_size": 2,
                         "pen_press_nudge": False, "hex_add_hash": True, "hex_field_opened_by_actions": True,
                         # «Контур и заливка»: the page's own keys for pen and bucket
                         "outline_fill_tool_mode": "keys", "outline_fill_brush_key": "b",
                         "outline_fill_fill_key": "f"},
        },
        "draw_me": {
            "algorithms": ["dfs_4dir", "line_cover", "outline_and_fill", "outline_only"],
            "algo_enabled": True,
            # Live 2026-10-01, six routes on one picture: all 99.5-99.9 % covered,
            # «Прямые отрезки» 25 s against 41 s of DFS (simulated full pictures 1.2-3x).
            "default_algo": "line_cover",
            # Any colour through the hue ring + square of the «Колесо» tab (WHEEL-001).
            "method_id": "wheel_square",
            "method_text_key": "settings_method_wheel_label",
            # Live 2026-10-01: the game draws a segment only when the cursor moves on
            # (the release steps back, see _release_step_point) and keeps every turn of
            # a dense zigzag only from a 12 ms rest (4-8 ms lost corners: white streaks).
            # It joins the points with a Catmull-Rom curve: a U-turn after a long run
            # bulged out by 1/8 of the run (12-50 px "fingers"); 16 px steps keep it ~2 px.
            # «Размер кисти» 3 paints a 5 px trace: size 4 overlaps cell 4 rows safely.
            "settings": {"pen_press_nudge": True, "brush_size": 4, "draw_delay": 0.012,
                         "pen_max_step": 16},
            "defaults": dict(ROBLOX_FRAME_PRESS),
            # The picture is a framed screenshot of a player: cut the background off.
            "snapshot_cutout": True,
            # A round is 5 minutes with the preparation: a player counted 14 colours
            # and was still drawing when the round ended (live 2026-10-07); the clean
            # 100 % runs of 2026-10-01 had 6-8.
            "auto_colors_limit": 8,
        },
        "rust_sign": {
            "algorithms": ["dfs_4dir", "line_cover", "outline_and_fill", "outline_only"],
            "algo_enabled": True,
            "default_algo": "line_cover",
            # The colour panel switches from the swatches to a picker with a code box
            # (the roller button next to «ЦВЕТ»); a bare code is accepted, "#" is kept.
            "method_id": "hex_field",
            "method_text_key": "settings_method_hex_label",
            # Live 2026-10-08, sign editor on a 2560 x 1440 screen: Rust ignores SendInput
            # (driver input only). It stamps the brush along every held move: ~160 px/ms of
            # lines (one jump per 400 px fill line) queued up and froze the game for minutes,
            # 8-10 px/ms keeps its answer under 20 ms — 20 px hops at 2 ms. Game brush 2
            # stamps 3 x 5 px, so 2 px cells close (brush 1 is 2 px wide: stripes). Lifts
            # and presses need ~4 frames, or the travel to the next area is painted.
            "defaults": {"brush_size": 2, "pen_max_step": 20, "draw_delay": 0.002,
                         "pen_button_delay": 0.012, "pen_settle_delay": 0.012},
        },
        "universal": {
            "algorithms": ["dfs_4dir", "line_cover", "outline_and_fill", "outline_only"],
            "algo_enabled": True,
            "default_algo": "line_cover",
            "method_id": "manual_palette",
            "method_text_key": "settings_method_manual_label",
        },
        "screen_palette": {
            "algorithms": ["dfs_4dir", "line_cover", "outline_and_fill", "outline_only"],
            "algo_enabled": True,
            "default_algo": "line_cover",
            "method_id": "screen_palette",
            "method_text_key": "settings_method_screen_label",
        },
    }


def recommended_profile(place_id: str) -> dict:
    """Settings a place starts with: the «Баланс» picture preparation, the shared
    recommended order and timings, then the place's own starting values."""
    from application.presets import QUALITY_PRESETS
    balanced = next(p for p in QUALITY_PRESETS if p["id"] == "balanced")["values"]
    return {**balanced, **RECOMMENDED_SETTINGS, **PLACE_PRESETS[place_id].get("defaults", {})}

ALGORITHM_DISPLAY_KEYS = {
        "dfs_4dir": "settings_algo_option_dfs4",
        "line_cover": "settings_algo_option_line_cover",
        "outline_and_fill": "settings_algo_option_outline_fill",
        "outline_only": "settings_algo_option_outline_only",
    }

def resolve_algorithm(code: str) -> str:
    normalized = str(code or "").strip()
    aliases = {
        "dfs (4-напр.)": "dfs_4dir", "dfs (4-dir)": "dfs_4dir",
        "dfs dynamic": "dfs_4dir", "dfs динамическая кисть": "dfs_4dir",
        "dfs_4dir_dynamic": "dfs_4dir", "outline & fill": "outline_and_fill",
        "контур и заливка": "outline_and_fill",
        # the old "outline + paint" drew the whole picture like DFS, only slower
        "outline + paint": "dfs_4dir", "outline_and_paint": "dfs_4dir",
        "контур + закрашивание": "dfs_4dir",
    }
    return aliases.get(normalized.lower(), normalized)
