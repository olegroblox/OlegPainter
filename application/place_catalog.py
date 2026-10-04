"""Target application capabilities shared by all presentation layers."""

PLACE_OPTIONS = [
        {"id": "speed_draw", "file": "Speed Draw!", "key": "settings_place_option_speed"},
        {"id": "spray_paint", "file": "Spray Paint!", "key": "settings_place_option_spray"},
        {"id": "draw_donate", "file": "Draw & Donate", "key": "settings_place_option_donate"},
        {"id": "gartic_phone", "file": "Gartic Phone", "key": "settings_place_option_gartic"},
        {"id": "draw_me", "file": "Draw Me!", "key": "settings_place_option_draw_me"},
        {"id": "universal", "file": "Универсальный", "key": "settings_place_option_universal"},
        {"id": "screen_palette", "file": "Палитра рамкой", "key": "settings_place_option_screen"},
    ]

# «Палитра рамкой» is a colour method, not a place: the main window offers it as
# a method of «Другая программа», and saved sessions move there (PLACES-002).
# The classic shell still lists it until it is removed.
LEGACY_SCREEN_PLACE = "screen_palette"

PLACE_PRESETS = {
        "speed_draw": {
            "algorithms": ["dfs_4dir", "line_cover", "outline_and_fill", "outline_only"],
            "algo_enabled": True,
            "default_algo": "dfs_4dir",
            "method_id": "hsv_palette",
            "method_text_key": "settings_method_hsv_label",
            # Live 2026-10-01: the game reads the mouse once a frame; without the
            # nudge every jump between areas was painted as a straight line.
            "settings": {"pen_press_nudge": True},
        },
        "spray_paint": {
            "algorithms": ["dfs_4dir", "line_cover", "outline_and_fill", "outline_only"],
            "algo_enabled": True,
            "default_algo": "dfs_4dir",
            "method_id": "hex_field",
            "method_text_key": "settings_method_hex_label",
            # The HEX box re-inserts "#" and moves the caret when a bare code is
            # typed, rotating the first character to the end (0040FF -> 040FF0).
            # The game reads the mouse once a frame: a press right after a jump
            # paints along the jump unless the cursor is nudged first.
            "settings": {"hex_add_hash": True, "pen_press_nudge": True},
            # Size box accepts 0.1..1.2 without a gamepass; integer auto ladder
            # would only ever try 1 and the clamped maximum.
            "brush": {"control_mode": "text", "text_auto": False, "min_value": 0.1, "max_value": 1.2,
                      "step_value": 0.1, "default_value": 0.1},
        },
        "draw_donate": {
            "algorithms": ["dfs_4dir", "line_cover", "outline_and_fill", "outline_only"],
            "algo_enabled": True,
            "default_algo": "outline_and_fill",
            "method_id": "hex_field",
            "method_text_key": "settings_method_hex_label",
        },
        "gartic_phone": {
            "algorithms": ["dfs_4dir", "line_cover", "outline_and_fill", "outline_only"],
            "algo_enabled": True,
            "default_algo": "dfs_4dir",
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
            # The picture is a framed screenshot of a player: cut the background off.
            "snapshot_cutout": True,
        },
        "universal": {
            "algorithms": ["dfs_4dir", "line_cover", "outline_and_fill", "outline_only"],
            "algo_enabled": True,
            "default_algo": "dfs_4dir",
            "method_id": "manual_palette",
            "method_text_key": "settings_method_manual_label",
        },
        "screen_palette": {
            "algorithms": ["dfs_4dir", "line_cover", "outline_and_fill", "outline_only"],
            "algo_enabled": True,
            "default_algo": "dfs_4dir",
            "method_id": "screen_palette",
            "method_text_key": "settings_method_screen_label",
        },
    }

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
