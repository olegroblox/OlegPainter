import contextlib
import logging

log = logging.getLogger("olegpainter.engine.olegpainter.core")
import math
from collections import deque
from pathlib import Path
from threading import Event

from .common import *  # noqa
from infrastructure.screen_capture import capture_screen
from infrastructure.automation_transport import AutomationTransport, InputCancelled
from infrastructure.capture_session import capture_start
from .translations import tr, LANGUAGES, current_lang  # noqa
from .cluster_cleanup import apply_cleanup_mode, distance_relabel_specks
from .color_spaces import perceptual_lightness_from_rgb, rgb_to_oklab_numpy
from .prep_pipeline import build_perceptual_cluster_map
from .fermat_spiral import fermat_cell_order
from .motion_profile import junction_speed_factor, trapezoid_dwells
from .route_kernels import pop_order_fast, snake_order_fast
from .route_polish import polish_cell_order
from .semantic_order import SemanticOrderMixin
from .path_planning import astar_bridge, cells_adjacent, collinear_vertices_mask
from .perf_stats import PerfStats
from .space_filling import gilbert_order_for_cells
from .telemetry import TelemetrySession, route_metrics
from .dynamic_brush import DynamicBrushMixin
from .drawing_events import DrawingEvent, DrawingPhase, DrawingProgress
from .outline_fill import OutlineFillMixin
from .extra_actions import ExtraActionsMixin
from .input_emulation import InputEmulationMixin
from .color_picking import ColorPickingMixin
from .post_draw_repair import PostDrawRepairMixin
from .viewport_state import VIEWPORT_UNSET, full_area_rect, normalize_area_rect, normalize_desktop_rect
from engine.ai import (
    DisabledAiBackend,
    ai_deferred_message,
)

class OlegPainter(DynamicBrushMixin, PostDrawRepairMixin, OutlineFillMixin, ExtraActionsMixin, ColorPickingMixin, InputEmulationMixin, SemanticOrderMixin):
    IMAGE_PATH = r""
    _POST_DRAW_REPAIR_MIN_EXPECTED_EUCLID_DELTA = 24.0
    _POST_DRAW_REPAIR_MIN_EXPECTED_MAX_CHANNEL_DELTA = 14.0
    _POST_DRAW_REPAIR_SAME_BG_EUCLID_DELTA = 14.0
    _POST_DRAW_REPAIR_SAME_BG_MAX_CHANNEL_DELTA = 9.0
    _POST_DRAW_REPAIR_BG_RELATIVE_EUCLID_RATIO = 0.35
    _POST_DRAW_REPAIR_BG_EXPECTED_DISTANCE_RATIO = 1.35
    _POST_DRAW_REPAIR_BG_RELATIVE_MAX_CHANNEL_RATIO = 0.45
    _POST_DRAW_REPAIR_BG_RELATIVE_MAX_CHANNEL_MIN = 12.0
    _POST_DRAW_REPAIR_COLOR_MISMATCH_EUCLID_DELTA = 26.0
    _POST_DRAW_REPAIR_COLOR_MISMATCH_MAX_CHANNEL_DELTA = 16.0
    _POST_DRAW_REPAIR_COLOR_MISMATCH_EXPECTED_RATIO = 0.34
    _POST_DRAW_REPAIR_COLOR_MISMATCH_EXPECTED_MAX_RATIO = 0.30
    _POST_DRAW_REPAIR_PARTIAL_COLOR_MISMATCH_EXPECTED_RATIO = 0.24
    _POST_DRAW_REPAIR_PARTIAL_COLOR_MISMATCH_EXPECTED_MAX_RATIO = 0.22
    _POST_DRAW_REPAIR_PARTIAL_COLOR_RATIO_THRESHOLD = 0.16
    _POST_DRAW_REPAIR_CAPTURE_SETTLE = 0.12
    _POST_DRAW_REPAIR_DENSE_PASSES = 2
    _POST_DRAW_REPAIR_CELL_DILATION = 1
    _DYNAMIC_BRUSH_PROFILE_VERSION = 2
    _DYNAMIC_BRUSH_MEASUREMENT_REVISION = 1
    _DYNAMIC_BRUSH_DEFAULT_SAFETY_MARGIN_CELLS = 0.18
    _DYNAMIC_BRUSH_VALIDATION_COLOR = "#121212"
    _DYNAMIC_BRUSH_LIGHT_PROBE_COLOR = "#F2F2F2"
    # Dynamic brush v2 (rest-machining tier ladder): the color's deep interior is
    # painted with a few DISCRETE big-brush sizes (largest first, halving), one
    # size change per tier per color, then the static base brush finishes the
    # edge band. Changing the size in the target app is the expensive operation,
    # so it must scale with tiers, never with cells.
    _DYNAMIC_BRUSH_V2_MAX_TIERS = 3
    _DYNAMIC_BRUSH_V2_STEP_OVER = 1.2           # stroke spacing as a fraction of tier radius
    _DYNAMIC_BRUSH_V2_MARK_RATIO = 0.85         # claimed capsule radius vs painted radius
    _DYNAMIC_BRUSH_V2_MIN_TIER_BRUSH_FACTOR = 2.5   # smallest worthwhile tier radius vs base brush
    _DYNAMIC_BRUSH_V2_TIER_MIN_GAIN_CELLS = 150     # est. cells saved that justify one size change
    _DYNAMIC_BRUSH_V2_VERIFY_TOLERANCE = 0.15       # closed-loop calibration check (rel. radius error)
    _DYNAMIC_BRUSH_CALIBRATION_COLORS = (
        "#FF3B30",
        "#FFD60A",
        "#34C759",
        "#0A84FF",
        "#64D2FF",
        "#BF5AF2",
        "#FF9F0A",
        "#FF375F",
    )

    def __init__(self, status_callback=None, pixel_update_callback=None, preview_update_callback=None,
                 drawing_event_callback=None):
        self.draw_region = None; self.stop_flag = False; self.drawing_enabled = False
        # Focus the target window before drawing so its first interaction (the color
        # selection click) isn't eaten by window activation when control just came from
        # olegpainter — that left the color unselected → drew the previous/default color
        # (e.g. yellow) and needed a 2nd manual click. This controls activation only;
        # the target guard still requires the correct foreground window when false.
        self.focus_target_window_enabled = True
        self.crop_norm = (0.0, 0.0, 1.0, 1.0); self.flip_horizontal = False; self.flip_vertical = False
        self.color_palette = []; self.current_color_index = 0
        self.drawn_mask = None; self.cluster_map = None; self.image_rgb = None
        self.quantized_preview_image = None; self.small_components_preview_mask = None
        self.drawing_thread = None
        self.status_callback = status_callback
        self.pixel_update_callback = pixel_update_callback
        self.preview_update_callback = preview_update_callback
        self.drawing_event_callback = drawing_event_callback
        self.drawing_run_id = 0
        self._drawing_cancel = Event()
        self._input = AutomationTransport(self._automation_cancelled)
        self._reset_progress_on_next_start = False
        self.main_hwnd = None

        self._background_cluster_id = -1
        # Custom freeform "draw-zone" mask painted in the Alt+F2 stencil: a 2D
        # boolean array (draw-region orientation, any resolution) where True =
        # draw here. None = no restriction (the whole rectangle is drawable).
        self._region_mask_src = None

        self.k_clusters = 25
        # Cap the amount of pixels used for KMeans fitting to avoid UI stalls on large areas.
        self.kmeans_fit_max_samples = 220000
        # Cap the clustering prediction grid size; large maps are clustered on a reduced grid and upscaled back.
        self.kmeans_predict_max_pixels = 900000
        self.draw_delay = 0.0055
        self.mouse_release_settle = 0.003
        # Per-area pen overhead (user-tunable). Defaults reproduce prior timing.
        # pen_button_delay: settle after EACH mouse button down AND up (the lib
        #   previously forced 0.03 here) -> dominates time on many tiny regions.
        # pen_settle_delay: settle after moving to a region start + after pen-down.
        self.pen_button_delay = 0.03
        self.pen_settle_delay = 0.01
        # Roblox Spray Paint started a stroke from where the cursor was BEFORE the
        # last jump (up to 5 stray stamps along it) and cut the last segment short.
        # A 1 px nudge before the press and a repeated point before the release fix
        # both. Off by default (costs ~60 ms per lift); input_timing turns it on
        # when its probe sees the trail.
        self.pen_press_nudge = False
        self._pen_pos = None
        self._pen_prev = None       # the stroke's previous vertex: where a release step goes back to
        # Fill speed of LARGE areas: pause per pixel of travel along a long stroke
        # (sub-steps the move so the pen paints gradually instead of an instant
        # A->B drag). 0 = instant (default). Small areas/strokes are unaffected.
        self.area_fill_delay = 0.0
        # Max pen step (px) for CONTINUOUS strokes: when > 0, draw_line walks A->B in
        # steps of at most this many px instead of one instant jump. Needed for targets
        # that stamp at the sampled cursor position (e.g. Roblox spray) and don't connect
        # mouse events like MS Paint does — otherwise a long jump leaves a gap. 0 = off
        # (instant jump, unchanged behavior for MS Paint).
        self.pen_max_step = 0
        # Browser games (Gartic Phone) read the mouse once a frame and smooth a held
        # stroke into curves: every turn inside a stroke is cut and fills become
        # blobs. Straight-stroke mode draws each straight segment as its own press;
        # the page loses strokes pressed sooner than ~2 frames after a release
        # (pen_stroke_gap 0 = automatic, see _split_gap).
        self.pen_split_strokes = False
        self.pen_stroke_gap = 0.0
        self._split_state = None
        self.brush_size = 2
        self.drawing_algorithm = "dfs_4dir"
        self.layer_sort_strategy = "luminance"
        self.tone_sequence = "light_to_dark"
        self.area_sequence = "large_to_small"
        # Centroid (x, y) of the last drawn region, in cluster-map pixel coords. Seeds the
        # `nearest` area order so each color's first region is the one closest to where the
        # pen just finished (continuity across regions and colors). None = no history yet.
        self._last_pen_cell = None
        self.mode = "color"
        self.eulerization_max_odd_vertices = 12
        self.stretch_to_area = True
        self.manual_image_rect = None  # (x, y, width, height) in area coordinates

        self.source_pil_image = None

        self.circle_params_calib = None
        self.slider_params_calib = None
        # Empirical "screen palette" (рамка) mode: {"lab": (N,3) float32, "coords": (N,2) int}
        # sampled from a calibrated rectangle; picking clicks the nearest on-screen pixel for
        # any target colour — no geometry assumptions, works for ANY palette layout.
        self.screen_palette_calib = None
        # «Кольцо и квадрат» (WHEEL-001): hue ring + saturation/brightness square,
        # measured from one framed picture; see engine/olegpainter/wheel_picker.py.
        self.wheel_square_calib = None
        self._wheel_last_square = None
        self.palette_rotation_direction_calib = "ccw"
        self.color_picking_method = "hex_field"

        self.hex_input_coord = None
        # The HEX box exists only after the recorded "before colour" clicks
        # (Gartic Phone's picker): check it appeared before typing the code.
        self.hex_field_opened_by_actions = False
        self.is_waiting_for_hex_click = False
        self.mouse_listener_hex = None
        self.hex_coord_set_callback = None
        self.hex_add_hash = False

        self.dynamic_brush_coord = None
        self.dynamic_brush_control_mode = "text"
        self.dynamic_brush_slider_params = None
        # "points" control mode: fixed on-screen presets, each {"x", "y", "value"} —
        # the bot clicks the preset whose value is requested. The most robust mode:
        # zero interpolation, every size is a measured, clickable reality.
        self.dynamic_brush_points = []
        self._dynamic_brush_point_capture_value = None
        # Drag instead of click for slider/points controls: press at the current
        # value's position, glide to the target, release (grab-style sliders).
        self.dynamic_brush_drag_enabled = False
        # Re-measure each size with a scratch-zone test stamp DURING the draw
        # (closed loop). OFF by default: calibration already measured every size,
        # and re-measuring on a deterministic target (MS Paint) only added a
        # fragile gate that demoted big brushes to the smallest one.
        self.dynamic_brush_verify_at_draw = False
        self.dynamic_brush_scratch_zone = None
        self.dynamic_brush_enabled = False
        self.dynamic_brush_profile = None
        self.dynamic_brush_profile_state = "needs_learning"
        self.dynamic_brush_runtime_state = "idle"
        self.dynamic_brush_session_active = False
        self.is_capturing_dynamic_brush_coord = False
        self.mouse_listener_dynamic_brush = None
        self.dynamic_brush_min_value = 0.05
        self.dynamic_brush_max_value = 1.2
        self.dynamic_brush_default_value = 0.1
        self.dynamic_brush_step_value = 0.01
        self._last_dynamic_brush_value = None
        self.dynamic_brush_calibration = None
        self.dynamic_brush_calibration_updated_at = None
        self.is_calibrating_dynamic_brush = False
        self.dynamic_brush_settings_changed_callback = None

        self.outline_fill_tool_mode = "keys"
        self.outline_fill_brush_key = "1"
        self.outline_fill_fill_key = "3"
        self.outline_fill_brush_coord = None
        self.outline_fill_fill_coord = None
        self.is_capturing_outline_fill_coords = False
        self._outline_fill_capture_step = None
        self.mouse_listener_outline_fill = None
        self.outline_fill_settings_changed_callback = None
        self.outline_fill_bucket_clicks = 5
        self.outline_fill_seed_margin = 0.3
        self.outline_fill_tiny_area_factor = 1.8
        self.outline_fill_small_area_factor = 3.3
        self.outline_fill_brush_small_cap = 360
        # A bucket-fill click must land at least this many cells deep inside the
        # region (distance-to-edge) so it never lands on/over the outline. When
        # no point is that deep (thin/tiny/complex), the region is brush-filled
        # via the main DFS instead of a missed bucket click.
        self.outline_fill_min_interior_cells = 2.0

        self.manual_palette_coords = []
        self.is_capturing_manual_palette = False
        self.max_manual_palette_colors = 64
        self.mouse_listener_manual_palette = None
        self.manual_palette_changed_callback = None
        self._manual_palette_last_click_at = 0.0
        self._manual_palette_last_click_pos = None
        self._manual_palette_cache = None
        self._manual_palette_match_cache = {}
        self.manual_palette_mix_enabled = False
        self.manual_palette_mix_alpha = 1.0
        self.alpha_slider_params = None
        self._manual_palette_target_rgb = {}
        self._manual_palette_mix_solution_cache = {}
        self.manual_mix_canvas_rgb = (255, 255, 255)
        self.manual_mix_canvas_changed_callback = None
        self._manual_palette_cluster_plan = {}
        self._manual_palette_force_entry = None
        self._manual_palette_force_alpha = None
        self._manual_palette_force_disable_mix = False
        self._manual_mix_warned_alpha = False
        self._manual_mix_warned_alpha = False
        self._cluster_uses_manual_palette = False
        self.extra_actions_changed_callback = None
        self.extra_actions_state = {
            "pre": {"events": [], "enabled": False},
            "post": {"events": [], "enabled": False},
        }
        self.max_extra_action_events = 160
        self.extra_action_default_delay = 0.0
        self.extra_action_click_press_delay = 0.01
        self.is_capturing_extra_actions = False
        self.active_extra_actions_slot = None
        self.mouse_listener_extra_actions = None
        self.keyboard_hook_extra_actions = None
        self._capture_events_buffer = []
        self._capture_last_event_time = None
        self._capture_ignore_until = 0.0
        self.max_extra_action_delay = 2.0

        self.stencil_enabled = False
        self.show_stencil_callback = None
        self.hide_stencil_callback = None
        self.skipped_regions_callback = None
        self.post_draw_repair_callback = None
        self.is_capturing_manual_mix_background = False
        self.mouse_listener_manual_mix_background = None

        self.image_original_rgba = None
        self.background_mask = None
        self.has_transparency = False

        self.bw_draw_mode = "both"
        self.remove_background = False
        self.bg_tolerance = 25
        self.alpha_threshold = 10
        self.background_removal_enabled = False
        self.background_removal_mode = "corner"
        self.background_color_tolerance = 25
        self.background_alpha_threshold = 10
        self.background_reference_rgb = None

        # UI window rect (x1,y1,x2,y2) to ignore clicks inside app
        self.app_window_rect = None
        self.ignore_clicks_until = 0.0

        self.ai_logger = logging.getLogger("OlegPainter.AI")
        self.ai_backend = DisabledAiBackend()
        self.ai_registry = None
        self.ai_gpu_available = False
        self.ai_use_gpu = False
        self._ai_provider_summary = "disabled"
        self._ai_status = self.ai_backend.status

        self.use_ai_background = False
        self.bg_ai_threshold = 0.5
        self.bg_ai_softness = 6
        self.bg_ai_model_id = "bg.u2netp"
        self.bg_ai_model_path = "u2netp.onnx"
        self.bg_ai_model_alias = "u2netp.onnx"

        self.ai_upscale_model_id = "upscale.realesrgan.x4plus-anime"
        self.ai_upscale_model_path = "realesrgan-x4plus-anime"
        self.ai_upscale_model_alias = "realesrgan-x4plus-anime"
        self.ai_upscale_scale = 4
        self.ai_upscale_tile = 0
        self.ai_upscale_denoise = None
        self.ai_upscale_face_enhance = False

        self.ai_outpaint_model_id = "outpaint.lama"
        self.ai_outpaint_model_alias = "LaMa ONNX"
        self.ai_outpaint_mode = "lama"
        self.ai_outpaint_sd_license = False
        self.ai_outpaint_padding = 160
        self.use_ai_outpaint = False

        self.draw_with_layers_enabled = False
        self.target_app_layer_coords = []
        self.is_capturing_app_layer_coords = False
        self.max_definable_app_layers = 20
        self.mouse_listener_app_layer = None

        self.layers_changed_callback = None

        self.is_shutting_down = False
        self.disable_stencil_autorestart = False


        self.last_upscale_original = None
        self.last_upscale_result = None

        self.drawing_start_time = None
        self.pause_start_time = None
        self.paused_time_accumulated = 0
        self._runtime_initialized = False

        self.dfs_4dir_neighbor_offsets = [(-1,0), (0,1), (1,0), (0,-1)]
        self.fast_min_region_area = 4
        self.post_draw_repair_enabled = False
        self.post_draw_repair_passes = 1
        self.post_draw_repair_mode = "conservative"
        self.post_draw_repair_sensitivity = 100
        self.post_draw_repair_color_mismatch_sensitivity = 100
        # Color determination defaults to OKLab (perceptual): clustering in
        # gamma-sRGB ("legacy_rgb") averages light non-linearly -> muddy, dark
        # midtones and non-perceptual palette gaps. OKLab fixes this at the root
        # (linear-light, perceptually uniform). User-switchable; legacy kept as
        # a fallback and an explicit choice.
        self.prep_color_space = "oklab"
        self.color_quality_migrated_v1 = False   # set True once load_config migrates legacy->oklab
        # Accent boost: chroma-weight the k-means so palette centroids are pulled
        # toward SATURATED pixels — vivid colours come out less washed-out and
        # saturated regions get better-placed centres. Perceptual path only.
        # Opt-in (changes the palette), default off.
        self.prep_preserve_accents = False
        # Final fixed-palette match metric: "ciede2000" (perceptually accurate,
        # ~1.6x better than Euclidean Lab; negligible cost at K-centroids x
        # palette volume) or "euclidean" (legacy fast). Affects manual_palette /
        # screen_palette picking only (hex/HSV reproduce exactly).
        self.palette_match_metric = "ciede2000"
        self.prep_quantization_mode = "kmeans"
        self.prep_cleanup_mode = "off"
        self.prep_cleanup_max_cells = 250000
        # --- Toggleable quality/speed features (still switchable in the UI) ---
        # Dithering: error-diffuse the palette assignment (perceptual path only).
        # Stays OFF by default (changes the look; opt-in creative choice).
        self.prep_dither_enabled = False
        # Merge perceptually near-identical palette colors (OKLab dE) AFTER k-means
        # so the color count acts as a MAX, not a forced count (a ~2-color image is
        # not split into N near-duplicate shades). 0 = off. Self-regulating: only
        # merges when k is higher than the image actually needs.
        self.color_merge_threshold = 0.05
        # Run-length merge: collapse straight runs into single pen drags (identical
        # pixels, far fewer mouse moves -> faster). ON by default (pure win).
        self.run_length_merge_enabled = True
        # Cells whose canvas color already matches the target are counted as drawn
        # before the run (white on a white canvas, the done part after a stop).
        self.skip_matching_canvas = True
        # Replace a pen lift with a short in-colour detour when the detour is
        # cheaper at the current pauses (see _cheap_bridge). Independent of the
        # always-bridge A* mode.
        self.cheap_bridges_enabled = True
        # Measured by input_timing: None = unknown, True = the target joins pointer
        # positions with a line itself (Paint). Then big coarse strokes never paid
        # off in live tests, so the dynamic brush draws with its detail size only.
        self.target_connects_points = None
        # A*-bridge mode: traverse fills with DFS + A* bridges so the pen returns
        # over already-painted cells instead of cutting stray lines. ON by default.
        self.astar_bridge_enabled = True
        # Nearest-palette-entry matching space for the manual palette: OKLab ranks
        # hues (especially blues/violets) closer to how the eye does than CIELAB.
        # Self-contained argmin (no cross-feature thresholds), so switching is safe;
        # "lab" restores the legacy behaviour.
        self.manual_match_space = "oklab"
        # --- Route-engine experiments ---
        # Greedy eulerization beyond the exact-DP odd-vertex limit. OFF by default:
        # with A* off it would make Euler routes (which retrace padded edges) win
        # over the shipped no-overdraw greedy snake on most regions.
        self.euler_greedy_pairing_enabled = False
        # Fill visit order: "auto" = current behaviour (DFS pop / greedy snake),
        # "gilbert" = generalized-Hilbert order (locality-preserving, ~linear progress).
        self.fill_traversal_mode = "auto"
        # 2-opt pass over the greedy nearest-neighbour region order: same regions,
        # strictly shorter pen travel between them. ON (flag kept for rollback).
        self.area_order_2opt_enabled = True
        # Numba route kernels (phase 5 / WS7-2): the snake / pop-DFS planners
        # compiled to machine code — BIT-IDENTICAL orders (pinned by tests),
        # only the planning time changes. Off / numba missing => Python path.
        self.route_kernels_numba_enabled = True
        # Semantic color order (phase 5 / WS8-2): group palette colors into
        # painter's planes — backdrop -> objects -> details (Intelli-Paint
        # heuristic over cluster features). Off = exactly the legacy tone order.
        self.semantic_order_mode = "off"
        # Saliency cutoff backdrop/object (slider in UI), clamped 0.05..0.95.
        self.semantic_threshold = 0.35
        # Which AI matting model classifies the planes (registry id).
        self.semantic_model_id = "bg.u2netp"
        # Per-REGION planes (two-pass drawing): a mixed color whose regions sit
        # in both zones is drawn twice — backdrop part in the backdrop phase,
        # object part in the object phase. Opt-in, needs saliency.
        self.semantic_region_split_enabled = False
        self._semantic_active_phase = None
        self._brush_pass = None
        self._color_draw_order = None
        # Drop one semantic plane from drawing: "off" / "backdrop" (don't draw
        # the background plane) / "object" (don't draw the object). Post-quant,
        # opt-in. A semantic alternative to corner/alpha/picked bg removal.
        self.semantic_background_drop = "off"
        # Aggressive despeckle (opt-in): relabel EVERY sub-threshold component to
        # the nearest large region (distance transform) so no tiny dots survive —
        # the costly-to-draw specks at edges / colour transitions. Off = legacy.
        self.aggressive_despeckle_enabled = False
        # Contrast-aware cleanup (DETAILS-001): a small region far from its
        # neighbour's colour (a pupil, a highlight, a mouth line) is a detail, not
        # noise — merge-small and despeckle keep it. Specks between two close
        # shades still merge (owner 2026-10-01: «мелкие детали испаряются»).
        self.prep_keep_details = True
        self._semantic_depth_planes_cache = None
        # Plotter-style speed profile for PACED fills (area_fill_delay > 0):
        # trapezoid dwell along strokes — cruise faster than the configured base
        # speed on straights, slow into corners. Pixels are bit-identical (only
        # sleep durations change). Off = constant dwell, the legacy behaviour.
        self.motion_profile_enabled = False
        self.motion_vmax_factor = 1.6
        # Corner SPEED in multiples of the base pace. 1.0 = exactly the legacy
        # constant pace at corners (proven safe) — the profile then can only be
        # FASTER than legacy, never slower.
        self.motion_corner_factor = 1.0
        self.motion_accel_px = 6
        # Route polish (LS-MCPP-lite): re-chain the lift-separated runs of any
        # built visit order — same cells, cheaper lifts, never worse by guard.
        self.fill_route_polish_enabled = False
        # Snake axis by strip count (TMSTC* idea) instead of bbox aspect.
        self.snake_turn_minimize_enabled = False
        # Or-opt pass (segment relocation, 1-3 regions) after 2-opt: same class of
        # change as the user-approved 2-opt — only the region ORDER gets shorter.
        self.area_order_or_opt_enabled = True
        # Asymmetric region entry/exit (phase 1, opt-in): anchor = the pen's REAL
        # exit cell (not the centroid) and the next region is entered at its cell
        # nearest to that anchor (not at coords[0]). Same picture, shorter hops,
        # different stroke start points -> default OFF until the live verdict.
        self.area_entry_exit_routing_enabled = False
        self._route_exit_cell = None  # ((row, col), grid_shape) of the last real exit
        # Pause after re-positioning the pen on resume/re-press, before pressing
        # again (was a hardcoded 0.05 in six draw loops). Same default; now
        # tunable for fast targets like MS Paint.
        self.pen_repress_settle = 0.05
        # Smart ETA (live feedback fix): the remaining time is computed from
        # MEASURED per-pixel and per-region rates (least squares over the running
        # session) instead of config delays — config-derived weights lie when the
        # OS sleep granularity dwarfs draw_delay. Display-only; off = legacy ETA.
        self.smart_eta_enabled = True
        self._eta_total_regions = 0
        self.last_drawing_duration = None
        # Session telemetry (phase 0): JSONL metrics per session/color/component,
        # written to %LOCALAPPDATA%. Pure diagnostics — drawing is untouched.
        self.telemetry_enabled = True
        self._telemetry = None
        self._perf = None
        self._tm_counters: dict[str, float] = {}

    def initialize_runtime(self) -> None:
        if self._runtime_initialized:
            return
        self._runtime_initialized = True
        if os.name != 'nt':
            return
        try:
            interception.auto_capture_devices(mouse=True, keyboard=False)
            self._log("Mouse successfully captured via Interception.")
        except Exception as exc:
            self._log(f"Interception initialization error: {exc}. Check driver and permissions.", True)

    def _report_ai_deferred(self, action: str) -> str:
        message = ai_deferred_message()
        self.ai_logger.info("%s requested while AI is disabled: %s", action, message)
        self._log(f"{action}: {message}", True)
        return message

    # --- extra actions helpers ---





    def get_extra_actions_summary(self) -> dict:
        return self._extra_actions_summary()






























    def _normalize_dynamic_brush_slider_profile(self, profile: dict) -> dict:
        normalized = dict(profile)
        legacy_range = self._dynamic_brush_range_payload(
            profile.get("range"),
            fallback_min=float(getattr(self, "dynamic_brush_min_value", 0.05)),
            fallback_max=float(getattr(self, "dynamic_brush_max_value", 1.2)),
            fallback_default=float(getattr(self, "dynamic_brush_default_value", 0.1)),
            fallback_step=float(getattr(self, "dynamic_brush_step_value", 0.01)),
        )
        target_range = self._dynamic_brush_slider_auto_range()
        normalized["range"] = dict(target_range)
        normalized["value_scale"] = "normalized"
        cached_calibration = profile.get("cached_calibration")
        if isinstance(cached_calibration, dict):
            normalized["cached_calibration"] = self._dynamic_brush_convert_calibration_range(
                cached_calibration,
                source_range=legacy_range,
                target_range=target_range,
            )
        return normalized



    def _normalize_dynamic_brush_calibration_payload(self, calibration):
        if not isinstance(calibration, dict):
            return None
        try:
            version = int(calibration.get("version", 1) or 1)
        except Exception:
            version = 1
        if version < self._DYNAMIC_BRUSH_PROFILE_VERSION:
            return None

        raw_samples = calibration.get("samples")
        if not isinstance(raw_samples, (list, tuple)):
            return None
        try:
            measurement_invalid = bool(calibration.get("measurement_invalid")) or not self._dynamic_brush_samples_consistent(
                raw_samples, calibration.get("required_values") or [])
        except (TypeError, ValueError, OverflowError):
            measurement_invalid = True
        normalized_rows: list[dict[str, float | str]] = []
        for item in raw_samples:
            if not isinstance(item, dict):
                continue
            try:
                value = float(item.get("value"))
            except Exception:
                continue
            point_reach = item.get("point_reach_cells", item.get("reach_cells"))
            stroke_h = item.get("stroke_reach_h_cells", point_reach)
            stroke_v = item.get("stroke_reach_v_cells", point_reach)
            half_width = item.get("half_width_cells", stroke_v)
            half_height = item.get("half_height_cells", stroke_h)
            area_cells = item.get("area_cells", 0.0)
            confidence = item.get("confidence", item.get("shape_confidence", 0.0))
            try:
                # v2 extras: stamp fill density (spray detection / step-over choice) and
                # the brush_size the sample was measured at (so a later brush change
                # does not silently rescale the calibrated radii).
                density = float(item.get("density", 1.0) or 0.0)
                if not (0.0 < density <= 1.0):
                    density = 1.0
                brush_px = max(0.0, float(item.get("brush_px", 0.0) or 0.0))
                row = {
                    "value": float(value),
                    "point_reach_cells": max(1e-4, float(point_reach or 0.0)),
                    "stroke_reach_h_cells": max(1e-4, float(stroke_h or 0.0)),
                    "stroke_reach_v_cells": max(1e-4, float(stroke_v or 0.0)),
                    "half_width_cells": max(1e-4, float(half_width or 0.0)),
                    "half_height_cells": max(1e-4, float(half_height or 0.0)),
                    "area_cells": max(1e-4, float(area_cells or 0.0)),
                    "shape_hint": str(item.get("shape_hint", item.get("shape", "unknown")) or "unknown").strip().lower(),
                    "confidence": max(0.0, min(1.0, float(confidence or 0.0))),
                    "density": density,
                    "brush_px": brush_px,
                    "offset_x_px": float(item.get("offset_x_px", 0.0) or 0.0),
                    "offset_y_px": float(item.get("offset_y_px", 0.0) or 0.0),
                }
            except Exception:
                continue
            if not all(math.isfinite(float(row[key])) for key in (
                "value",
                "point_reach_cells",
                "stroke_reach_h_cells",
                "stroke_reach_v_cells",
                "half_width_cells",
                "half_height_cells",
                "area_cells",
                "confidence",
                "offset_x_px",
                "offset_y_px",
            )):
                measurement_invalid = True
                continue
            normalized_rows.append(row)
        if not normalized_rows:
            return None

        # Keep observations intact. A running maximum hides reversed controls
        # and contaminated probes; readiness validates the complete curve.
        normalized_rows.sort(key=lambda item: (float(item["value"]), float(item["point_reach_cells"])))

        try:
            timestamp = float(calibration.get("timestamp")) if calibration.get("timestamp") is not None else None
        except Exception:
            timestamp = None

        validation = calibration.get("validation")
        normalized_validation = None
        if isinstance(validation, dict):
            target_rect = validation.get("target_rect")
            if isinstance(target_rect, (list, tuple)) and len(target_rect) == 4:
                try:
                    target_rect = [int(round(float(v))) for v in target_rect]
                except Exception:
                    target_rect = None
            else:
                target_rect = None
            try:
                inside_coverage = float(validation.get("inside_coverage", 0.0) or 0.0)
            except Exception:
                inside_coverage = 0.0
            try:
                outside_pixels = int(validation.get("outside_pixels", 0) or 0)
            except Exception:
                outside_pixels = 0
            normalized_validation = {
                "target_rect": target_rect,
                "outside_pixels": max(0, outside_pixels),
                "inside_coverage": max(0.0, min(1.0, inside_coverage)),
                "passed": (validation.get("passed") is True and math.isfinite(inside_coverage)
                           and 0 <= inside_coverage <= 1 and outside_pixels >= 0),
            }

        runtime_correction = calibration.get("runtime_correction")
        normalized_correction = self._dynamic_brush_identity_runtime_correction()
        if isinstance(runtime_correction, dict):
            for key in ("anchor_low_value", "anchor_high_value"):
                try:
                    value = runtime_correction.get(key)
                    normalized_correction[key] = None if value is None else float(value)
                except Exception:
                    normalized_correction[key] = None
            for key, fallback in (("reach_scale", 1.0), ("reach_bias", 0.0)):
                try:
                    value = float(runtime_correction.get(key, fallback))
                except Exception:
                    value = fallback
                if not math.isfinite(value):
                    value = fallback
                normalized_correction[key] = value

        try:
            safety_margin = float(
                calibration.get("safety_margin_cells", self._DYNAMIC_BRUSH_DEFAULT_SAFETY_MARGIN_CELLS)
            )
        except Exception:
            safety_margin = self._DYNAMIC_BRUSH_DEFAULT_SAFETY_MARGIN_CELLS
        if not math.isfinite(safety_margin):
            safety_margin = self._DYNAMIC_BRUSH_DEFAULT_SAFETY_MARGIN_CELLS
        safety_margin = max(0.0, safety_margin)

        try:
            shape_confidence = float(calibration.get("shape_confidence", 0.0) or 0.0)
        except Exception:
            shape_confidence = 0.0

        payload = {
            "version": version,
            "measurement_revision": calibration.get("measurement_revision", 0),
            "measurement_invalid": measurement_invalid,
            "required_values": list(calibration.get("required_values") or []) if isinstance(calibration.get("required_values"), (list, tuple)) else [],
            "samples": normalized_rows,
            "sample_count": len(normalized_rows),
            "shape": str(calibration.get("shape", "unknown") or "unknown").strip().lower(),
            "shape_confidence": max(0.0, min(1.0, shape_confidence)),
            "validation": normalized_validation,
            "runtime_correction": normalized_correction,
            "safety_margin_cells": safety_margin,
            "timestamp": timestamp,
            "sample_points": [
                {"reach_cells": float(row["point_reach_cells"]), "value": float(row["value"])}
                for row in normalized_rows
            ],
        }
        return payload

    def _update_dynamic_brush_profile_state(self):
        profile = getattr(self, "dynamic_brush_profile", None)
        if not bool(getattr(self, "dynamic_brush_enabled", False)):
            self.dynamic_brush_profile_state = "needs_learning"
            return self.dynamic_brush_profile_state
        if isinstance(profile, dict) and bool(profile.get("valid")) and not self._dynamic_brush_profile_issue(profile):
            self.dynamic_brush_profile_state = "ready"
        elif profile is None:
            self.dynamic_brush_profile_state = "needs_learning"
        else:
            self.dynamic_brush_profile_state = "invalid"
        return self.dynamic_brush_profile_state

    def _set_dynamic_brush_runtime_state(self, state: str):
        normalized = str(state or "idle").strip().lower()
        if normalized == getattr(self, "dynamic_brush_runtime_state", "idle"):
            return
        self.dynamic_brush_runtime_state = normalized
        self._notify_dynamic_brush_settings_changed()

    def _normalize_dynamic_brush_profile(self, profile):
        if not isinstance(profile, dict):
            return None
        control_mode = str(profile.get("control_mode") or "text").strip().lower()
        if control_mode not in ("text", "slider", "points"):
            control_mode = "text"
        try:
            version = int(profile.get("version", 1) or 1)
        except Exception:
            version = 1
        control_params = profile.get("control_params")
        scratch_zone = profile.get("scratch_zone")
        range_data = profile.get("range")
        cached_calibration = self._normalize_dynamic_brush_calibration_payload(profile.get("cached_calibration"))
        if control_mode == "points":
            normalized_params = [dict(p) for p in control_params if isinstance(p, dict)] if isinstance(control_params, (list, tuple)) else None
            normalized_params = normalized_params or None
        else:
            normalized_params = dict(control_params) if isinstance(control_params, dict) else None
        normalized = {
            "version": version,
            "valid": profile.get("valid") is True,
            "control_mode": control_mode,
            "control_params": normalized_params,
            "scratch_zone": dict(scratch_zone) if isinstance(scratch_zone, dict) else None,
            "range": {
                "min_value": float((range_data or {}).get("min_value", self.dynamic_brush_min_value)),
                "max_value": float((range_data or {}).get("max_value", self.dynamic_brush_max_value)),
                "default_value": float((range_data or {}).get("default_value", self.dynamic_brush_default_value)),
                "step_value": float((range_data or {}).get("step_value", self.dynamic_brush_step_value)),
            },
            "cached_calibration": dict(cached_calibration) if isinstance(cached_calibration, dict) else None,
            "learned_at": profile.get("learned_at"),
        }
        if normalized["control_params"] is None or normalized["scratch_zone"] is None:
            normalized["valid"] = False
        if version < self._DYNAMIC_BRUSH_PROFILE_VERSION:
            normalized["valid"] = False
            normalized["cached_calibration"] = None
        if control_mode == "slider":
            normalized = self._normalize_dynamic_brush_slider_profile(normalized)
        if self._dynamic_brush_profile_issue(normalized):
            normalized["valid"] = False
        return normalized

    def _sync_dynamic_brush_profile_runtime(self):
        profile = getattr(self, "dynamic_brush_profile", None)
        resolved_coord = None
        resolved_slider = None
        resolved_points = None
        resolved_scratch = None
        if isinstance(profile, dict):
            control_mode = str(profile.get("control_mode") or "text").strip().lower()
            control_params = profile.get("control_params")
            if control_mode == "slider":
                resolved_slider = self._dynamic_brush_resolve_slider_params(control_params)
            elif control_mode == "points":
                resolved_points = self._dynamic_brush_resolve_points(control_params)
            else:
                resolved_coord = self._dynamic_brush_resolve_point(control_params)
            resolved_scratch = self._dynamic_brush_resolve_rect(profile.get("scratch_zone"))
            range_data = profile.get("range")
            if isinstance(range_data, dict):
                normalized_range = self._dynamic_brush_range_payload(
                    range_data,
                    fallback_min=float(self.dynamic_brush_min_value),
                    fallback_max=float(self.dynamic_brush_max_value),
                    fallback_default=float(self.dynamic_brush_default_value),
                    fallback_step=float(self.dynamic_brush_step_value),
                )
                self.dynamic_brush_min_value = normalized_range["min_value"]
                self.dynamic_brush_max_value = normalized_range["max_value"]
                self.dynamic_brush_default_value = normalized_range["default_value"]
                self.dynamic_brush_step_value = normalized_range["step_value"]
            if bool(profile.get("valid")) and not self._dynamic_brush_profile_issue(profile):
                cached = self._normalize_dynamic_brush_calibration_payload(self._dynamic_brush_cached_calibration())
                if isinstance(cached, dict) and (
                    control_mode == "slider" or not isinstance(getattr(self, "dynamic_brush_calibration", None), dict)
                ):
                    self.dynamic_brush_calibration = dict(cached)
        # The profile stores captures NORMALIZED to the draw region (a legacy
        # portability idea). The control/scratch live in the TARGET APP though —
        # they don't move when the draw region moves. So directly-captured
        # ABSOLUTE coordinates (persisted in the config) always win; the profile
        # only fills the gaps for old configs that never saved them directly.
        if resolved_coord is not None and self.dynamic_brush_coord is None:
            self.dynamic_brush_coord = resolved_coord
        if resolved_slider is not None and self.dynamic_brush_slider_params is None:
            self.dynamic_brush_slider_params = resolved_slider
        if resolved_points and not self.dynamic_brush_points:
            self.dynamic_brush_points = resolved_points
        if resolved_scratch is not None and not isinstance(self.dynamic_brush_scratch_zone, (tuple, list)):
            self.dynamic_brush_scratch_zone = resolved_scratch
        self._update_dynamic_brush_profile_state()



    def apply_viewport_state(
        self,
        *,
        draw_region_desktop_px=VIEWPORT_UNSET,
        stretch_to_area=VIEWPORT_UNSET,
        manual_rect_area_px=VIEWPORT_UNSET,
        reset_manual: bool = False,
    ) -> bool:
        changed = False

        if draw_region_desktop_px is not VIEWPORT_UNSET:
            normalized_draw_region = normalize_desktop_rect(draw_region_desktop_px)
            if self.draw_region != normalized_draw_region:
                self.draw_region = normalized_draw_region
                changed = True

        if stretch_to_area is not VIEWPORT_UNSET:
            normalized_stretch = bool(stretch_to_area)
            if self.get_stretch_to_area() != normalized_stretch:
                self.stretch_to_area = normalized_stretch
                changed = True

        previous_manual = self.get_manual_image_rect()
        if draw_region_desktop_px is not VIEWPORT_UNSET and self.draw_region is None:
            if self.manual_image_rect is not None:
                self.manual_image_rect = None
                changed = True
            return changed

        if manual_rect_area_px is not VIEWPORT_UNSET:
            normalized_manual = normalize_area_rect(manual_rect_area_px)
            if normalized_manual is None:
                if self.manual_image_rect is not None:
                    self.manual_image_rect = None
                    changed = True
            else:
                self.set_manual_image_rect(normalized_manual)
                if self.get_manual_image_rect() != previous_manual:
                    changed = True
        elif reset_manual:
            new_manual = full_area_rect(self.draw_region)
            if self.get_manual_image_rect() != new_manual:
                self.reset_manual_image_rect()
                changed = True
        elif self.draw_region and not self.get_stretch_to_area() and self.manual_image_rect is None:
            self.ensure_manual_image_rect()
            if self.get_manual_image_rect() != previous_manual:
                changed = True

        if draw_region_desktop_px is not VIEWPORT_UNSET and changed:
            self._sync_dynamic_brush_profile_runtime()
            self._notify_dynamic_brush_settings_changed()

        return changed

    def compute_background_mask_ai(self, pil_rgba_image):
        self._report_ai_deferred("AI background mask")
        return None

    def upscale_image_ai(self, pil_rgb_image=None):
        self._report_ai_deferred("AI upscale")
        return None

    def outpaint_image_ai(
        self,
        pil_rgb_image=None,
        *,
        prompt: str | None = None,
        negative_prompt: str | None = None,
        steps: int | None = None,
        cfg_scale: float | None = None,
        seed: int | None = None,
        padding: int | None = None,
        pad_left: int | None = None,
        pad_right: int | None = None,
        pad_top: int | None = None,
        pad_bottom: int | None = None,
        anchor_x: float | None = None,
        anchor_y: float | None = None,
        target_width: int | None = None,
        target_height: int | None = None,
    ):
        self._report_ai_deferred("AI outpaint")
        return None

    def _build_outpaint_mask(
        self,
        rgba_image: Image.Image,
        pad_left: int,
        pad_top: int,
        pad_right: int,
        pad_bottom: int,
    ) -> Image.Image:
        arr = np.array(rgba_image)
        alpha = arr[:, :, 3]
        mask = np.zeros_like(alpha, dtype=np.uint8)
        mask[alpha < 250] = 255
        if pad_top > 0:
            mask[:pad_top, :] = 255
        if pad_bottom > 0:
            mask[-pad_bottom:, :] = 255
        if pad_left > 0:
            mask[:, :pad_left] = 255
        if pad_right > 0:
            mask[:, -pad_right:] = 255
        mask_img = Image.fromarray(mask)
        return mask_img

    def _apply_basic_bg_removal(self, rgb_map_small, alpha_map_small):
        mode = str(getattr(self, "background_removal_mode", "corner") or "corner").strip().lower()
        alpha_threshold = int(getattr(self, "background_alpha_threshold", self.alpha_threshold))
        tolerance = int(getattr(self, "background_color_tolerance", self.bg_tolerance))
        reference_rgb = getattr(self, "background_reference_rgb", None)

        if mode == "alpha":
            self._log(f"Using alpha channel for background mask (threshold: {alpha_threshold}).")
            self.background_mask |= alpha_map_small < alpha_threshold
            return

        if rgb_map_small.size <= 0:
            self._log("Image map for background removal is empty (np_img_small_rgb_map is empty).")
            return

        if mode == "picked":
            picked_rgb = None
            if isinstance(reference_rgb, (list, tuple)) and len(reference_rgb) >= 3:
                try:
                    picked_rgb = tuple(int(max(0, min(255, int(round(float(v)))))) for v in reference_rgb[:3])
                except Exception:
                    picked_rgb = None
            if picked_rgb is None:
                raise ValueError("Для удаления фона выберите цвет на изображении или введите HEX.")
            else:
                self._log(f"Using picked color for background mask (tolerance: {tolerance}).")
                color_diff = np.sqrt(
                    np.sum((rgb_map_small.astype(float) - np.array(picked_rgb).astype(float)) ** 2, axis=2)
                )
                self.background_mask |= color_diff < tolerance
                self._log(
                    f"Picked color: {picked_rgb}. Found {np.count_nonzero(self.background_mask)} background pixels on map."
                )
                return

        self._log(f"Using corner color for background mask (tolerance: {tolerance}).")
        corner_color_rgb = tuple(int(v) for v in rgb_map_small[0, 0])
        color_diff = np.sqrt(
            np.sum((rgb_map_small.astype(float) - np.array(corner_color_rgb).astype(float)) ** 2, axis=2)
        )
        self.background_mask |= color_diff < tolerance
        self._log(f"Corner color: {corner_color_rgb}. Found {np.count_nonzero(self.background_mask)} background pixels on map.")

    def get_config(self):
        manual_rect = self.get_manual_image_rect()
        config = {
            "IMAGE_PATH": self.IMAGE_PATH,
            "source_is_clipboard_placeholder": self.IMAGE_PATH == tr(
                "clipboard_source_name") and self.source_pil_image is not None,
            "draw_region": self.draw_region,
            "stretch_to_area": self.get_stretch_to_area(),
            "manual_image_rect": list(manual_rect) if manual_rect else None,
            "crop_norm": list(self.get_crop_norm()),
            "flip_horizontal": bool(getattr(self, "flip_horizontal", False)),
            "flip_vertical": bool(getattr(self, "flip_vertical", False)),
            "k_clusters": self.k_clusters,
            "draw_delay": self.draw_delay,
            "mouse_release_settle": float(getattr(self, "mouse_release_settle", 0.003)),
            "pen_button_delay": float(getattr(self, "pen_button_delay", 0.03)),
            "pen_settle_delay": float(getattr(self, "pen_settle_delay", 0.01)),
            "pen_press_nudge": bool(getattr(self, "pen_press_nudge", False)),
            "area_fill_delay": float(getattr(self, "area_fill_delay", 0.0)),
            "pen_max_step": int(getattr(self, "pen_max_step", 0)),
            "pen_split_strokes": bool(getattr(self, "pen_split_strokes", False)),
            "hex_field_opened_by_actions": bool(getattr(self, "hex_field_opened_by_actions", False)),
            "pen_stroke_gap": float(getattr(self, "pen_stroke_gap", 0.0)),
            "focus_target_window_enabled": bool(getattr(self, "focus_target_window_enabled", True)),
            "brush_size": self.brush_size,
            "dynamic_brush_enabled": bool(getattr(self, "dynamic_brush_enabled", False)),
            "dynamic_brush_control_mode": str(self.dynamic_brush_control_mode or "text"),
            "dynamic_brush_min_value": float(self.dynamic_brush_min_value),
            "dynamic_brush_max_value": float(self.dynamic_brush_max_value),
            "dynamic_brush_default_value": float(self.dynamic_brush_default_value),
            "dynamic_brush_step_value": float(self.dynamic_brush_step_value),
            "dynamic_brush_coord": list(self.dynamic_brush_coord) if self.dynamic_brush_coord else None,
            "dynamic_brush_slider_params": list(self.dynamic_brush_slider_params) if self.dynamic_brush_slider_params else None,
            "dynamic_brush_points": [dict(p) for p in self.dynamic_brush_points if isinstance(p, dict)] if isinstance(getattr(self, "dynamic_brush_points", None), list) else [],
            "dynamic_brush_scratch_zone": list(self.dynamic_brush_scratch_zone) if isinstance(self.dynamic_brush_scratch_zone, (tuple, list)) else None,
            "dynamic_brush_drag_enabled": bool(getattr(self, "dynamic_brush_drag_enabled", False)),
            "dynamic_brush_verify_at_draw": bool(getattr(self, "dynamic_brush_verify_at_draw", False)),
            "dynamic_brush_text_auto": bool(getattr(self, "dynamic_brush_text_auto", True)),
            "dynamic_brush_calibration": dict(self.dynamic_brush_calibration) if isinstance(self.dynamic_brush_calibration, dict) else None,
            "dynamic_brush_profile": dict(self.dynamic_brush_profile) if isinstance(self.dynamic_brush_profile, dict) else None,
            "drawing_algorithm": self.drawing_algorithm,
            "layer_sort_strategy": self.layer_sort_strategy,
            "tone_sequence": self.tone_sequence,
            "area_sequence": self.area_sequence,
            "mode": self.mode,
            "hex_input_coord": self.hex_input_coord,
            "bw_draw_mode": self.bw_draw_mode,
            "remove_background": self.remove_background,
            "bg_tolerance": self.bg_tolerance,
            "alpha_threshold": self.alpha_threshold,
            "background_removal_enabled": self.background_removal_enabled,
            "background_removal_mode": self.background_removal_mode,
            "background_color_tolerance": self.background_color_tolerance,
            "background_alpha_threshold": self.background_alpha_threshold,
            "background_reference_rgb": list(self.background_reference_rgb) if isinstance(self.background_reference_rgb, (list, tuple)) and len(self.background_reference_rgb) >= 3 else None,
            "draw_with_layers_enabled": self.draw_with_layers_enabled,
            "target_app_layer_coords": self.target_app_layer_coords,
            "manual_palette_coords": self.manual_palette_coords,
            "manual_mix_enabled": bool(self.manual_palette_mix_enabled),
            "manual_mix_alpha": float(self.manual_palette_mix_alpha),
            "manual_mix_canvas_rgb": list(self.manual_mix_canvas_rgb),
            "alpha_slider_params": self.alpha_slider_params,
            "pre_color_actions": self._serialize_actions("pre"),
            "post_color_actions": self._serialize_actions("post"),
            "pre_actions_enabled": bool(self.extra_actions_state["pre"]["enabled"]),
            "post_actions_enabled": bool(self.extra_actions_state["post"]["enabled"]),
            "color_picking_method": self.color_picking_method,
            "hex_add_hash": bool(getattr(self, "hex_add_hash", False)),
            "circle_params_calib": self.circle_params_calib,
            "slider_params_calib": self.slider_params_calib,
            "screen_palette_calib": self._serialize_screen_palette_calib(),
            "wheel_square_calib": self.wheel_square_calib,
            "palette_rotation_direction_calib": self.palette_rotation_direction_calib,
            "stencil_enabled": self.stencil_enabled,
            "fast_min_region_area": self.fast_min_region_area,
            "outline_fill_tool_mode": self.outline_fill_tool_mode,
            "outline_fill_brush_key": self.outline_fill_brush_key,
            "outline_fill_fill_key": self.outline_fill_fill_key,
            "outline_fill_brush_coord": list(self.outline_fill_brush_coord)
            if isinstance(self.outline_fill_brush_coord, (list, tuple))
            else None,
            "outline_fill_fill_coord": list(self.outline_fill_fill_coord)
            if isinstance(self.outline_fill_fill_coord, (list, tuple))
            else None,
            "outline_fill_tiny_area_factor": float(self.outline_fill_tiny_area_factor),
            "outline_fill_small_area_factor": float(self.outline_fill_small_area_factor),
            "outline_fill_brush_small_cap": int(self.outline_fill_brush_small_cap),
            "post_draw_repair_enabled": bool(self.post_draw_repair_enabled),
            "post_draw_repair_passes": int(self.post_draw_repair_passes),
            "post_draw_repair_mode": self._normalize_post_draw_repair_mode(
                getattr(self, "post_draw_repair_mode", "conservative")
            ),
            "post_draw_repair_sensitivity": int(getattr(self, "post_draw_repair_sensitivity", 100)),
            "post_draw_repair_color_mismatch_sensitivity": int(
                getattr(self, "post_draw_repair_color_mismatch_sensitivity", 100)
            ),
            "prep_color_space": self._normalize_prep_color_space(getattr(self, "prep_color_space", "oklab")),
            "color_quality_migrated_v1": bool(getattr(self, "color_quality_migrated_v1", False)),
            "prep_preserve_accents": bool(getattr(self, "prep_preserve_accents", True)),
            "palette_match_metric": self._palette_match_metric_value(),
            "prep_quantization_mode": self._normalize_prep_quantization_mode(
                getattr(self, "prep_quantization_mode", "kmeans")
            ),
            "prep_cleanup_mode": self._normalize_prep_cleanup_mode(getattr(self, "prep_cleanup_mode", "off")),
            "prep_dither_enabled": bool(getattr(self, "prep_dither_enabled", False)),
            "color_merge_threshold": float(getattr(self, "color_merge_threshold", 0.05)),
            "run_length_merge_enabled": bool(getattr(self, "run_length_merge_enabled", False)),
            "skip_matching_canvas": bool(getattr(self, "skip_matching_canvas", True)),
            "cheap_bridges_enabled": bool(getattr(self, "cheap_bridges_enabled", True)),
            "target_connects_points": getattr(self, "target_connects_points", None),
            "astar_bridge_enabled": bool(getattr(self, "astar_bridge_enabled", False)),
            "manual_match_space": self._normalize_manual_match_space(getattr(self, "manual_match_space", "oklab")),
            "euler_greedy_pairing_enabled": bool(getattr(self, "euler_greedy_pairing_enabled", False)),
            "fill_traversal_mode": self._normalize_fill_traversal_mode(getattr(self, "fill_traversal_mode", "auto")),
            "area_order_2opt_enabled": bool(getattr(self, "area_order_2opt_enabled", True)),
            "route_kernels_numba_enabled": bool(getattr(self, "route_kernels_numba_enabled", True)),
            "semantic_order_mode": self._normalize_semantic_order_mode(getattr(self, "semantic_order_mode", "off")),
            "semantic_threshold": self._semantic_threshold_value(),
            "semantic_model_id": self._semantic_model_id_value(),
            "semantic_region_split_enabled": bool(getattr(self, "semantic_region_split_enabled", False)),
            "semantic_background_drop": self._normalize_semantic_background_drop(getattr(self, "semantic_background_drop", "off")),
            "aggressive_despeckle_enabled": bool(getattr(self, "aggressive_despeckle_enabled", False)),
            "prep_keep_details": bool(getattr(self, "prep_keep_details", True)),
            "motion_profile_enabled": bool(getattr(self, "motion_profile_enabled", False)),
            "motion_vmax_factor": float(getattr(self, "motion_vmax_factor", 1.6)),
            "motion_corner_factor": float(getattr(self, "motion_corner_factor", 0.25)),
            "motion_accel_px": int(getattr(self, "motion_accel_px", 6)),
            "fill_route_polish_enabled": bool(getattr(self, "fill_route_polish_enabled", False)),
            "snake_turn_minimize_enabled": bool(getattr(self, "snake_turn_minimize_enabled", False)),
            "area_order_or_opt_enabled": bool(getattr(self, "area_order_or_opt_enabled", True)),
            "area_entry_exit_routing_enabled": bool(getattr(self, "area_entry_exit_routing_enabled", False)),
            "telemetry_enabled": bool(getattr(self, "telemetry_enabled", True)),
            "smart_eta_enabled": bool(getattr(self, "smart_eta_enabled", True)),
            "pen_repress_settle": float(getattr(self, "pen_repress_settle", 0.05)),
        }
        return config
    def load_config(self, data):
        self._log("Загрузка конфигурации...")
        try:
            if not isinstance(data, dict):
                data = {}
            from ui.helpers.config_migration import sanitize_painter_config

            data, _changed = sanitize_painter_config(data)
            if data.get("source_is_clipboard_placeholder", False):
                self.IMAGE_PATH = tr("clipboard_source_name")
                self.source_pil_image = None
            else:
                self.IMAGE_PATH = data.get("IMAGE_PATH", r"")
                self.source_pil_image = None
            loaded_draw_region = data.get("draw_region")
            manual_rect_cfg = data.get("manual_image_rect")
            self.apply_viewport_state(
                draw_region_desktop_px=loaded_draw_region,
                stretch_to_area=data.get("stretch_to_area", getattr(self, "stretch_to_area", True)),
                manual_rect_area_px=manual_rect_cfg if isinstance(manual_rect_cfg, (list, tuple)) else VIEWPORT_UNSET,
                reset_manual=not isinstance(manual_rect_cfg, (list, tuple)),
            )
            crop_cfg = data.get("crop_norm")
            if isinstance(crop_cfg, (list, tuple)) and len(crop_cfg) == 4:
                self.set_crop_norm(tuple(crop_cfg), reprocess=False)
            else:
                self.reset_crop(reprocess=False)
            self.set_source_flip(
                horizontal=bool(data.get("flip_horizontal", False)),
                vertical=bool(data.get("flip_vertical", False)),
                reprocess=False,
            )
            self.k_clusters = int(data.get("k_clusters", self.k_clusters))
            self.draw_delay = float(data.get("draw_delay", self.draw_delay))
            self.mouse_release_settle = max(0.0, float(data.get("mouse_release_settle", getattr(self, "mouse_release_settle", 0.003))))
            self.pen_button_delay = max(0.0, float(data.get("pen_button_delay", getattr(self, "pen_button_delay", 0.03))))
            self.pen_settle_delay = max(0.0, float(data.get("pen_settle_delay", getattr(self, "pen_settle_delay", 0.01))))
            self.pen_press_nudge = bool(data.get("pen_press_nudge", getattr(self, "pen_press_nudge", False)))
            self.area_fill_delay = max(0.0, float(data.get("area_fill_delay", getattr(self, "area_fill_delay", 0.0))))
            self.pen_max_step = max(0, int(data.get("pen_max_step", getattr(self, "pen_max_step", 0))))
            self.pen_split_strokes = bool(data.get("pen_split_strokes", getattr(self, "pen_split_strokes", False)))
            self.hex_field_opened_by_actions = bool(data.get("hex_field_opened_by_actions",
                                                             getattr(self, "hex_field_opened_by_actions", False)))
            self.pen_stroke_gap = min(1.0, max(0.0, float(data.get("pen_stroke_gap", getattr(self, "pen_stroke_gap", 0.0)))))
            self.focus_target_window_enabled = bool(data.get("focus_target_window_enabled", getattr(self, "focus_target_window_enabled", True)))
            self.brush_size = int(data.get("brush_size", self.brush_size))
            self.dynamic_brush_enabled = bool(data.get("dynamic_brush_enabled", getattr(self, "dynamic_brush_enabled", False)))
            self.set_dynamic_brush_control_mode(data.get("dynamic_brush_control_mode", self.dynamic_brush_control_mode))
            self.update_dynamic_brush_settings(
                min_value=data.get("dynamic_brush_min_value", self.dynamic_brush_min_value),
                max_value=data.get("dynamic_brush_max_value", self.dynamic_brush_max_value),
                default_value=data.get("dynamic_brush_default_value", self.dynamic_brush_default_value),
                step=data.get("dynamic_brush_step_value", self.dynamic_brush_step_value),
            )
            dyn_coord = data.get("dynamic_brush_coord")
            if isinstance(dyn_coord, (list, tuple)) and len(dyn_coord) >= 2:
                try:
                    self.dynamic_brush_coord = (int(dyn_coord[0]), int(dyn_coord[1]))
                except Exception:
                    self.dynamic_brush_coord = None
            elif dyn_coord is None:
                self.dynamic_brush_coord = None
            slider_params = data.get("dynamic_brush_slider_params")
            if isinstance(slider_params, (list, tuple)) and len(slider_params) == 4:
                try:
                    orientation = str(slider_params[0] or "vertical").strip().lower()
                    if orientation not in ("vertical", "horizontal"):
                        orientation = "vertical"
                    self.dynamic_brush_slider_params = (
                        orientation,
                        float(slider_params[1]),
                        float(slider_params[2]),
                        float(slider_params[3]),
                    )
                except Exception:
                    self.dynamic_brush_slider_params = None
            elif slider_params is None:
                self.dynamic_brush_slider_params = None
            raw_points = data.get("dynamic_brush_points")
            if isinstance(raw_points, (list, tuple)):
                parsed_points = []
                for item in raw_points:
                    if not isinstance(item, dict):
                        continue
                    try:
                        parsed_points.append(
                            {"x": int(item["x"]), "y": int(item["y"]), "value": float(item["value"])}
                        )
                    except Exception:
                        continue
                parsed_points.sort(key=lambda p: p["value"])
                self.dynamic_brush_points = parsed_points
            raw_scratch = data.get("dynamic_brush_scratch_zone")
            if isinstance(raw_scratch, (list, tuple)) and len(raw_scratch) == 4:
                try:
                    self.dynamic_brush_scratch_zone = tuple(int(v) for v in raw_scratch)
                except Exception:
                    log.debug("ignored exception parsing dynamic_brush_scratch_zone", exc_info=True)
            self.dynamic_brush_drag_enabled = bool(data.get("dynamic_brush_drag_enabled", getattr(self, "dynamic_brush_drag_enabled", False)))
            self.dynamic_brush_verify_at_draw = bool(data.get("dynamic_brush_verify_at_draw", getattr(self, "dynamic_brush_verify_at_draw", False)))
            # Old profiles explicitly store their manual range. Do not switch
            # them to integer probing, nor inherit another profile's flag.
            legacy_range = any(key in data for key in (
                "dynamic_brush_min_value", "dynamic_brush_max_value", "dynamic_brush_step_value"))
            self.dynamic_brush_text_auto = bool(data.get("dynamic_brush_text_auto", not legacy_range))
            calib_data = data.get("dynamic_brush_calibration")
            calibration_payload = self._normalize_dynamic_brush_calibration_payload(calib_data)
            if isinstance(calibration_payload, dict):
                self.dynamic_brush_calibration = calibration_payload
                self.dynamic_brush_calibration_updated_at = calibration_payload.get("timestamp")
            else:
                self.dynamic_brush_calibration = None
                self.dynamic_brush_calibration_updated_at = None
            profile_data = self._normalize_dynamic_brush_profile(data.get("dynamic_brush_profile"))
            self.dynamic_brush_profile = profile_data
            algo_value = data.get("drawing_algorithm", self.drawing_algorithm)
            algo_normalized = str(algo_value or "").strip().lower()
            legacy_algos = {"dfs_fill_current_color", "dfs_4dir_fast", "custom_neighbor"}
            if algo_normalized in legacy_algos or not algo_normalized:
                algo_normalized = "dfs_4dir"
            if algo_normalized == "dfs_4dir_dynamic":
                self.dynamic_brush_enabled = True
                algo_normalized = "dfs_4dir"
            if algo_normalized == "outline_and_paint":
                # the old "outline + paint" drew the whole picture: DFS does the same, faster
                algo_normalized = "dfs_4dir"
            allowed_algos = {"dfs_4dir", "line_cover", "outline_and_fill", "outline_only"}
            if algo_normalized not in allowed_algos:
                algo_normalized = "dfs_4dir"
            self.drawing_algorithm = algo_normalized
            self.dynamic_brush_session_active = False
            self._sync_dynamic_brush_profile_runtime()
            self._set_dynamic_brush_runtime_state("idle")
            self.layer_sort_strategy = data.get("layer_sort_strategy", self.layer_sort_strategy)
            self.tone_sequence = data.get("tone_sequence", getattr(self, "tone_sequence", "light_to_dark"))
            self.area_sequence = data.get("area_sequence", getattr(self, "area_sequence", "large_to_small"))
            if "tone_sequence" not in data:
                if self.layer_sort_strategy == "luminance":
                    self.tone_sequence = "light_to_dark"
            if "area_sequence" not in data:
                if self.layer_sort_strategy == "count":
                    self.area_sequence = "large_to_small"
            self.mode = data.get("mode", self.mode)
            loaded_hex_coord = data.get("hex_input_coord")
            self.hex_input_coord = tuple(loaded_hex_coord) if isinstance(loaded_hex_coord,
                                                                         list) else loaded_hex_coord
            self.bw_draw_mode = data.get("bw_draw_mode", self.bw_draw_mode)
            background_enabled = data.get("background_removal_enabled")
            if background_enabled is None:
                background_enabled = data.get("remove_background", self.remove_background)
            self.background_removal_enabled = bool(background_enabled)
            self.remove_background = self.background_removal_enabled
            self.use_ai_background = False
            self.ai_use_gpu = False
            self.use_ai_outpaint = False
            manual_mix_enabled = data.get("manual_mix_enabled")
            if manual_mix_enabled is not None:
                self.manual_palette_mix_enabled = bool(manual_mix_enabled)
            manual_mix_alpha = data.get("manual_mix_alpha")
            if manual_mix_alpha is not None:
                try:
                    parsed_alpha = float(manual_mix_alpha)
                except Exception:
                    parsed_alpha = 1.0
                if not (0.0 <= parsed_alpha <= 1.0):
                    parsed_alpha = 1.0
                if parsed_alpha < 0.99:
                    parsed_alpha = 1.0
                self.manual_palette_mix_alpha = parsed_alpha
            else:
                self.manual_palette_mix_alpha = 1.0
            manual_mix_canvas_rgb = data.get("manual_mix_canvas_rgb")
            if isinstance(manual_mix_canvas_rgb, (list, tuple)) and len(manual_mix_canvas_rgb) == 3:
                try:
                    self.manual_mix_canvas_rgb = tuple(
                        int(max(0, min(255, int(round(float(v)))))) for v in manual_mix_canvas_rgb
                    )
                except Exception:
                    self.manual_mix_canvas_rgb = (255, 255, 255)
            if "alpha_slider_params" in data:
                from application.color_mixing import valid_alpha_slider
                alpha_slider = data["alpha_slider_params"]
                self.alpha_slider_params = tuple(alpha_slider) if valid_alpha_slider(alpha_slider) else None
            self.background_color_tolerance = int(
                data.get("background_color_tolerance", data.get("bg_tolerance", self.background_color_tolerance))
            )
            self.bg_tolerance = self.background_color_tolerance
            self.background_alpha_threshold = int(
                data.get("background_alpha_threshold", data.get("alpha_threshold", self.background_alpha_threshold))
            )
            self.alpha_threshold = self.background_alpha_threshold
            mode_value = str(data.get("background_removal_mode", "") or "").strip().lower()
            if mode_value not in ("alpha", "corner", "picked"):
                if bool(data.get("use_ai_background", False)):
                    mode_value = "corner"
                elif self.background_removal_enabled:
                    source = getattr(self, "source_pil_image", None) or getattr(self, "image_original_rgba", None)
                    has_alpha = False
                    if isinstance(source, Image.Image):
                        try:
                            extrema = source.convert("RGBA").getchannel("A").getextrema()
                            has_alpha = bool(extrema) and int(extrema[0]) < 255
                        except Exception:
                            has_alpha = False
                    mode_value = "alpha" if has_alpha else "corner"
                else:
                    mode_value = "corner"
            self.background_removal_mode = mode_value
            ref_rgb = data.get("background_reference_rgb")
            if isinstance(ref_rgb, (list, tuple)) and len(ref_rgb) >= 3:
                try:
                    self.background_reference_rgb = tuple(
                        int(max(0, min(255, int(round(float(v)))))) for v in ref_rgb[:3]
                    )
                except Exception:
                    self.background_reference_rgb = None
            else:
                self.background_reference_rgb = None
            self.stencil_enabled = bool(data.get("stencil_enabled", self.stencil_enabled))
            self.fast_min_region_area = int(data.get("fast_min_region_area", self.fast_min_region_area))
            mode_value = str(data.get("outline_fill_tool_mode", self.outline_fill_tool_mode) or "").strip().lower()
            self.outline_fill_tool_mode = mode_value if mode_value in ("keys", "coords") else "keys"
            brush_key_val = data.get("outline_fill_brush_key", self.outline_fill_brush_key)
            self.outline_fill_brush_key = str(brush_key_val).strip() or self.outline_fill_brush_key
            fill_key_val = data.get("outline_fill_fill_key", self.outline_fill_fill_key)
            self.outline_fill_fill_key = str(fill_key_val).strip() or self.outline_fill_fill_key
            brush_coord_raw = data.get("outline_fill_brush_coord")
            if isinstance(brush_coord_raw, (list, tuple)) and len(brush_coord_raw) >= 2:
                try:
                    self.outline_fill_brush_coord = (int(brush_coord_raw[0]), int(brush_coord_raw[1]))
                except Exception:
                    self.outline_fill_brush_coord = None
            elif brush_coord_raw is None:
                self.outline_fill_brush_coord = None
            fill_coord_raw = data.get("outline_fill_fill_coord")
            if isinstance(fill_coord_raw, (list, tuple)) and len(fill_coord_raw) >= 2:
                try:
                    self.outline_fill_fill_coord = (int(fill_coord_raw[0]), int(fill_coord_raw[1]))
                except Exception:
                    self.outline_fill_fill_coord = None
            elif fill_coord_raw is None:
                self.outline_fill_fill_coord = None
            tiny_factor_raw = data.get("outline_fill_tiny_area_factor")
            if tiny_factor_raw is not None:
                try:
                    parsed_tiny = float(tiny_factor_raw)
                except Exception:
                    parsed_tiny = None
                if parsed_tiny is not None and math.isfinite(parsed_tiny) and parsed_tiny >= 1.0:
                    self.outline_fill_tiny_area_factor = parsed_tiny
            small_factor_raw = data.get("outline_fill_small_area_factor")
            if small_factor_raw is not None:
                try:
                    parsed_small = float(small_factor_raw)
                except Exception:
                    parsed_small = None
                if parsed_small is not None and math.isfinite(parsed_small):
                    min_small = self.outline_fill_tiny_area_factor + 0.25
                    if parsed_small <= self.outline_fill_tiny_area_factor:
                        parsed_small = min_small
                    self.outline_fill_small_area_factor = parsed_small
            cap_raw = data.get("outline_fill_brush_small_cap")
            if cap_raw is not None:
                try:
                    parsed_cap = int(cap_raw)
                except Exception:
                    parsed_cap = None
                if parsed_cap is not None:
                    self.outline_fill_brush_small_cap = max(0, min(4096, parsed_cap))
            self.post_draw_repair_enabled = bool(
                data.get("post_draw_repair_enabled", getattr(self, "post_draw_repair_enabled", False))
            )
            try:
                repair_passes = int(data.get("post_draw_repair_passes", getattr(self, "post_draw_repair_passes", 1)))
            except Exception:
                repair_passes = 1
            self.post_draw_repair_passes = max(1, min(3, repair_passes))
            self.post_draw_repair_mode = self._normalize_post_draw_repair_mode(
                data.get("post_draw_repair_mode", getattr(self, "post_draw_repair_mode", "conservative"))
            )
            try:
                repair_sensitivity = int(
                    data.get("post_draw_repair_sensitivity", getattr(self, "post_draw_repair_sensitivity", 100))
                )
            except Exception:
                repair_sensitivity = 100
            self.post_draw_repair_sensitivity = max(50, min(200, repair_sensitivity))
            try:
                color_mismatch_sensitivity = int(
                    data.get(
                        "post_draw_repair_color_mismatch_sensitivity",
                        getattr(self, "post_draw_repair_color_mismatch_sensitivity", 100),
                    )
                )
            except Exception:
                color_mismatch_sensitivity = 100
            self.post_draw_repair_color_mismatch_sensitivity = max(50, min(200, color_mismatch_sensitivity))
            self.prep_color_space = self._normalize_prep_color_space(
                data.get("prep_color_space", getattr(self, "prep_color_space", "oklab"))
            )
            # One-time migration to perceptual colour: old configs persisted the
            # former default "legacy_rgb" (gamma sRGB, muddy). Bump those to OKLab
            # ONCE; the marker lets a LATER deliberate legacy choice stick.
            if not bool(data.get("color_quality_migrated_v1", False)):
                if self.prep_color_space == "legacy_rgb":
                    self.prep_color_space = "oklab"
            self.color_quality_migrated_v1 = True
            self.prep_preserve_accents = bool(
                data.get("prep_preserve_accents", getattr(self, "prep_preserve_accents", True)))
            _pmm = str(data.get("palette_match_metric", getattr(self, "palette_match_metric", "ciede2000")) or "").strip().lower()
            self.palette_match_metric = _pmm if _pmm in {"euclidean", "ciede2000"} else "ciede2000"
            self.prep_quantization_mode = self._normalize_prep_quantization_mode(
                data.get("prep_quantization_mode", getattr(self, "prep_quantization_mode", "kmeans"))
            )
            self.prep_cleanup_mode = self._normalize_prep_cleanup_mode(
                data.get("prep_cleanup_mode", getattr(self, "prep_cleanup_mode", "off"))
            )
            self.prep_dither_enabled = bool(
                data.get("prep_dither_enabled", getattr(self, "prep_dither_enabled", False)))
            self.color_merge_threshold = max(0.0, float(
                data.get("color_merge_threshold", getattr(self, "color_merge_threshold", 0.05))))
            self.run_length_merge_enabled = bool(
                data.get("run_length_merge_enabled", getattr(self, "run_length_merge_enabled", False)))
            self.astar_bridge_enabled = bool(
                data.get("astar_bridge_enabled", getattr(self, "astar_bridge_enabled", False)))
            self.skip_matching_canvas = bool(
                data.get("skip_matching_canvas", getattr(self, "skip_matching_canvas", True)))
            self.cheap_bridges_enabled = bool(
                data.get("cheap_bridges_enabled", getattr(self, "cheap_bridges_enabled", True)))
            connects = data.get("target_connects_points", getattr(self, "target_connects_points", None))
            self.target_connects_points = None if connects is None else bool(connects)
            self.manual_match_space = self._normalize_manual_match_space(
                data.get("manual_match_space", getattr(self, "manual_match_space", "oklab")))
            self.euler_greedy_pairing_enabled = bool(
                data.get("euler_greedy_pairing_enabled", getattr(self, "euler_greedy_pairing_enabled", False)))
            self.fill_traversal_mode = self._normalize_fill_traversal_mode(
                data.get("fill_traversal_mode", getattr(self, "fill_traversal_mode", "auto")))
            self.area_order_2opt_enabled = bool(
                data.get("area_order_2opt_enabled", getattr(self, "area_order_2opt_enabled", True)))
            self.route_kernels_numba_enabled = bool(
                data.get("route_kernels_numba_enabled", getattr(self, "route_kernels_numba_enabled", True)))
            self.semantic_order_mode = self._normalize_semantic_order_mode(
                data.get("semantic_order_mode", getattr(self, "semantic_order_mode", "off")))
            try:
                self.semantic_threshold = max(0.05, min(0.95, float(
                    data.get("semantic_threshold", getattr(self, "semantic_threshold", 0.35)))))
            except (TypeError, ValueError):
                self.semantic_threshold = 0.35
            self.semantic_model_id = str(
                data.get("semantic_model_id", getattr(self, "semantic_model_id", "bg.u2netp"))
                or "bg.u2netp").strip() or "bg.u2netp"
            self.semantic_region_split_enabled = bool(
                data.get("semantic_region_split_enabled",
                         getattr(self, "semantic_region_split_enabled", False)))
            self.semantic_background_drop = self._normalize_semantic_background_drop(
                data.get("semantic_background_drop", getattr(self, "semantic_background_drop", "off")))
            self.aggressive_despeckle_enabled = bool(
                data.get("aggressive_despeckle_enabled",
                         getattr(self, "aggressive_despeckle_enabled", False)))
            self.prep_keep_details = bool(data.get("prep_keep_details", getattr(self, "prep_keep_details", True)))
            self.motion_profile_enabled = bool(
                data.get("motion_profile_enabled", getattr(self, "motion_profile_enabled", False)))
            try:
                self.motion_vmax_factor = max(1.0, min(4.0, float(
                    data.get("motion_vmax_factor", getattr(self, "motion_vmax_factor", 1.6)))))
                self.motion_corner_factor = max(0.5, min(2.0, float(
                    data.get("motion_corner_factor", getattr(self, "motion_corner_factor", 1.0)))))
                self.motion_accel_px = max(1, min(60, int(
                    data.get("motion_accel_px", getattr(self, "motion_accel_px", 6)))))
            except Exception:
                self.motion_vmax_factor, self.motion_corner_factor, self.motion_accel_px = 1.6, 1.0, 6
            self.fill_route_polish_enabled = bool(
                data.get("fill_route_polish_enabled", getattr(self, "fill_route_polish_enabled", False)))
            self.snake_turn_minimize_enabled = bool(
                data.get("snake_turn_minimize_enabled", getattr(self, "snake_turn_minimize_enabled", False)))
            self.area_order_or_opt_enabled = bool(
                data.get("area_order_or_opt_enabled", getattr(self, "area_order_or_opt_enabled", True)))
            self.area_entry_exit_routing_enabled = bool(
                data.get("area_entry_exit_routing_enabled", getattr(self, "area_entry_exit_routing_enabled", False)))
            self.telemetry_enabled = bool(
                data.get("telemetry_enabled", getattr(self, "telemetry_enabled", True)))
            self.smart_eta_enabled = bool(
                data.get("smart_eta_enabled", getattr(self, "smart_eta_enabled", True)))
            try:
                self.pen_repress_settle = max(0.0, min(0.5, float(
                    data.get("pen_repress_settle", getattr(self, "pen_repress_settle", 0.05)))))
            except Exception:
                self.pen_repress_settle = 0.05
            self.draw_with_layers_enabled = bool(
                data.get("draw_with_layers_enabled", self.draw_with_layers_enabled))
            loaded_layers = data.get("target_app_layer_coords", [])
            self.target_app_layer_coords = [tuple(coord) for coord in loaded_layers if
                                            isinstance(coord, (list, tuple)) and len(coord) == 2]
            self._notify_layers_changed()
            loaded_palette = data.get("manual_palette_coords", [])
            palette_list = []
            if isinstance(loaded_palette, (list, tuple)):
                for item in loaded_palette:
                    if isinstance(item, dict):
                        x = item.get("x")
                        y = item.get("y")
                        rgb = item.get("rgb")
                        hex_val = item.get("hex")
                        if x is None or y is None:
                            continue
                        try:
                            x_int = int(x); y_int = int(y)
                        except Exception:
                            continue
                        if isinstance(rgb, (list, tuple)) and len(rgb) >= 3:
                            try:
                                rgb_clean = [int(rgb[0]), int(rgb[1]), int(rgb[2])]
                            except Exception:
                                rgb_clean = None
                        else:
                            rgb_clean = None
                        entry = {"x": x_int, "y": y_int}
                        if rgb_clean is not None:
                            entry["rgb"] = rgb_clean
                        if isinstance(hex_val, str):
                            entry["hex"] = hex_val
                        palette_list.append(entry)
            self.manual_palette_coords = palette_list
            self._invalidate_manual_palette_cache()
            self._notify_manual_palette_changed()
            self._notify_outline_fill_settings_changed()
            self._load_actions_from_config(data)
            requested_cpm = data.get("color_picking_method", self.color_picking_method)
            if requested_cpm != "manual_palette":
                if self.manual_palette_coords and self.manual_palette_mix_enabled:
                    requested_cpm = "manual_palette"
            self.color_picking_method = requested_cpm
            self.hex_add_hash = bool(data.get("hex_add_hash", data.get("hex_field_add_hash", getattr(self, "hex_add_hash", False))))
            loaded_circle_params = data.get("circle_params_calib")
            self.circle_params_calib = tuple(loaded_circle_params) if isinstance(loaded_circle_params,
                                                                                 list) else loaded_circle_params
            loaded_slider_params = data.get("slider_params_calib")
            if isinstance(loaded_slider_params, list):
                if len(loaded_slider_params) == 3:
                    self.slider_params_calib = ("vertical", loaded_slider_params[0], loaded_slider_params[1], loaded_slider_params[2])
                else:
                    self.slider_params_calib = tuple(loaded_slider_params)
            else:
                self.slider_params_calib = loaded_slider_params
            loaded_sp = data.get("screen_palette_calib")
            self.screen_palette_calib = None
            if isinstance(loaded_sp, dict) and loaded_sp.get("lab"):
                try:
                    lab = np.asarray(loaded_sp["lab"], dtype=np.float32)
                    coords = np.asarray(loaded_sp["coords"], dtype=np.int32)
                    if lab.ndim == 2 and lab.shape[0] > 0 and lab.shape[0] == coords.shape[0]:
                        sl = loaded_sp.get("slider")
                        slider = tuple(sl) if isinstance(sl, (list, tuple)) and len(sl) == 4 else None
                        self.screen_palette_calib = {"lab": lab, "coords": coords, "slider": slider}
                except Exception:
                    self.screen_palette_calib = None
            from engine.olegpainter import wheel_picker
            loaded_wheel = data.get("wheel_square_calib")
            self.wheel_square_calib = loaded_wheel if wheel_picker.valid(loaded_wheel) else None
            self._wheel_last_square = None
            self.palette_rotation_direction_calib = data.get("palette_rotation_direction_calib",
                                                             self.palette_rotation_direction_calib)
            self._log("Конфигурация успешно загружена.")
            self._notify_dynamic_brush_settings_changed()
            self.current_color_index = 0
            self.drawn_mask = None
            self.cluster_map = None
            self.quantized_preview_image = None; self.small_components_preview_mask = None
            self.image_original_rgba = None
            self.drawing_enabled = False
            self.stop_flag = False
            return True
        except Exception as e:
            self._log(f"Ошибка загрузки конфигурации: {e}", True)
            traceback.print_exc()
            return False

    def get_ai_provider_info(self) -> tuple[str, str]:
        return self.ai_backend.provider_info()

    def set_image_from_pil(self, pil_image_obj, source_name="<Clipboard>"):
        if not isinstance(pil_image_obj, Image.Image):
            self._log(
                f"Попытка установить не-PIL объект ({type(pil_image_obj)}) как исходное изображение. Источник: {source_name}",
                is_error=True)
            self.source_pil_image = None
            self.IMAGE_PATH = ""
            return False

        try:
            self.source_pil_image = pil_image_obj.copy()
            if self.source_pil_image.mode != 'RGBA':
                self.source_pil_image = self.source_pil_image.convert('RGBA')
        except Exception as e:
            self._log(f"Ошибка при копировании или конвертации объекта PIL ({source_name}): {e}", is_error=True)
            self.source_pil_image = None
            self.IMAGE_PATH = ""
            return False

        self.IMAGE_PATH = source_name
        self._log(f"Изображение успешно установлено из объекта PIL: {source_name}")

        self.current_color_index = 0
        self.drawn_mask = None
        self.cluster_map = None
        self.quantized_preview_image = None; self.small_components_preview_mask = None
        self.image_original_rgba = None
        self.reset_manual_image_rect()
        return True



    def _serialize_screen_palette_calib(self):
        """JSON-safe form of screen_palette_calib for get_config (lists, rounded LAB)."""
        sp = getattr(self, "screen_palette_calib", None)
        if not isinstance(sp, dict) or sp.get("lab") is None:
            return None
        try:
            lab = np.asarray(sp["lab"], dtype=np.float32)
            coords = np.asarray(sp["coords"], dtype=np.int32)
            if lab.shape[0] == 0:
                return None
            out = {"lab": np.round(lab, 1).tolist(), "coords": coords.tolist()}
            if sp.get("slider"):
                out["slider"] = list(sp["slider"])
            return out
        except Exception:
            return None

    def upscale_current_image_ai(self):
        self._report_ai_deferred("AI upscale current image")
        if self.status_callback:
            self.status_callback(ai_deferred_message())
        return False

    def set_ai_background_model(self, model_id: str):
        self.bg_ai_model_id = model_id

    def set_ai_upscale_model(self, model_id: str):
        self.ai_upscale_model_id = model_id

    def set_ai_outpaint_model(self, model_id: str, mode: str | None = None):
        self.ai_outpaint_model_id = model_id
        if mode:
            self.ai_outpaint_mode = mode

    def _refresh_ai_metadata(self):
        self.bg_ai_model_path = self.bg_ai_model_alias
        self.ai_upscale_model_path = self.ai_upscale_model_alias
        self.ai_outpaint_model_alias = self.ai_outpaint_model_alias


    def reset_current_drawing(self):
        if self._quiet():
            return False
        self._log("Сброс текущего рисунка для нового начала...")

        _old_thread = None
        if self.drawing_thread and self.drawing_thread.is_alive():
            self._log("Сигнал остановки для активного потока рисования...")
            self.stop_flag = True
            self.drawing_enabled = False
            _old_thread = self.drawing_thread
        else:

            self.stop_flag = True
            self.drawing_enabled = False

        if _old_thread:
            self._log(f"Ожидание завершения потока {_old_thread.name if _old_thread.name else 'OlegPainterThread'}...")
            _old_thread.join(2.0)
            if _old_thread.is_alive():
                self._log(
                    f"[КРИТИЧНО] Поток рисования {_old_thread.name if _old_thread.name else 'OlegPainterThread'} не завершился! Прерываю сброс, чтобы не перезаписать состояние под работающим потоком.",
                    True)
                # Поток ещё жив: НЕ продолжаем — иначе prepare_image_and_palette ниже
                # перезапишет cluster_map/drawn_mask прямо под работающим потоком (гонка).
                # stop_flag оставляем True и ссылку на поток сохраняем, чтобы следующий
                # вызов снова попытался его дождаться.
                return False

        self.drawing_thread = None

        if not self.IMAGE_PATH or not self.draw_region:
            self._log("Невозможно сбросить рисунок: не задан путь к изображению или область рисования.", True)
            if self.status_callback:
                if not self.IMAGE_PATH:
                    self.status_callback(tr("status_error_no_image_for_processing"))
                else:
                    self.status_callback(tr("status_error_no_area_for_processing"))
            if self.preview_update_callback: self.preview_update_callback(None)
            if self.pixel_update_callback:
                _prev_cluster_map, self.cluster_map = self.cluster_map, np.array([[]])
                _prev_drawn_mask, self.drawn_mask = self.drawn_mask, np.array([[]], dtype=bool)
                self.pixel_update_callback()
                self.cluster_map, self.drawn_mask = _prev_cluster_map, _prev_drawn_mask

            self.stop_flag = False
            return False

        self._log("Переобработка изображения для нового рисунка...")

        success = self.prepare_image_and_palette()

        if not success:
            self.stop_flag = False

        if success and self.status_callback:
            self.status_callback(tr("status_new_drawing_ready"))

        return success

    def _cancel_active_captures(self, reason_key=""):
        self._end_capture_session()
        cancelled_something = False
        if self.is_waiting_for_hex_click:
            self._log("Cancelling HEX field click wait.")
            if self.mouse_listener_hex and self.mouse_listener_hex.is_alive():
                self.mouse_listener_hex.stop()
            self.is_waiting_for_hex_click = False
            if reason_key: self.status_callback(tr(reason_key))
            cancelled_something = True

        if self.is_capturing_app_layer_coords:
            self._log("Cancelling App Layer coordinate capture.")
            if self.mouse_listener_app_layer and self.mouse_listener_app_layer.is_alive():
                self.mouse_listener_app_layer.stop()
            self.is_capturing_app_layer_coords = False
            self._layer_capture_draft = []
            self._notify_layers_changed()
            if reason_key: self.status_callback(tr(reason_key))
            else: self.status_callback(tr("status_app_layer_capture_cancelled_action"))
            cancelled_something = True

        if getattr(self, 'is_capturing_manual_palette', False):
            self._log("Cancelling manual palette capture.")
            listener = getattr(self, 'mouse_listener_manual_palette', None)
            if listener and getattr(listener, 'is_alive', lambda: False)():
                try:
                    listener.stop()
                except Exception:
                    log.debug('ignored exception in listener.stop()', exc_info=True)
            self.mouse_listener_manual_palette = None
            self.is_capturing_manual_palette = False
            self._manual_palette_last_click_at = 0.0
            self._manual_palette_last_click_pos = None
            self._notify_manual_palette_changed()
            if reason_key: self.status_callback(tr(reason_key))
            else: self.status_callback(tr("status_manual_palette_capture_cancelled_action"))
            cancelled_something = True
        if getattr(self, 'is_capturing_outline_fill_coords', False):
            self._log("Cancelling outline & fill tool coordinate capture.")
            listener = getattr(self, 'mouse_listener_outline_fill', None)
            if listener and getattr(listener, 'is_alive', lambda: False)():
                try:
                    listener.stop()
                except Exception:
                    log.debug('ignored exception in listener.stop()', exc_info=True)
            self.mouse_listener_outline_fill = None
            self.is_capturing_outline_fill_coords = False
            self._outline_fill_capture_step = None
            if reason_key:
                try:
                    self.status_callback(tr(reason_key))
                except Exception:
                    log.debug('ignored exception in self.status_callback(tr(reason_key))', exc_info=True)
            else:
                try:
                    self.status_callback(tr("status_outline_fill_capture_cancelled"))
                except Exception:
                    log.debug("ignored exception in self.status_callback(tr('status_outline_fill_capture_cancelled'))", exc_info=True)
            cancelled_something = True
        if getattr(self, 'is_capturing_extra_actions', False):
            self._log("Cancelling extra actions capture.")
            slot_label = self._actions_slot_label(self.active_extra_actions_slot)
            self._stop_actions_listeners()
            self.is_capturing_extra_actions = False
            self.active_extra_actions_slot = None
            self._capture_events_buffer = []
            self._capture_last_event_time = None
            self._capture_ignore_until = 0.0
            self._notify_extra_actions_changed()
            if reason_key:
                self.status_callback(tr(reason_key))
            else:
                try:
                    self.status_callback(tr("status_extra_actions_capture_cancelled_slot", slot=slot_label))
                except Exception:
                    self.status_callback("Extra actions capture cancelled.")
            cancelled_something = True
        if getattr(self, 'is_capturing_manual_mix_background', False):
            self._log("Cancelling manual mix background capture.")
            listener = getattr(self, 'mouse_listener_manual_mix_background', None)
            if listener and getattr(listener, 'is_alive', lambda: False)():
                try:
                    listener.stop()
                except Exception:
                    log.debug('ignored exception in listener.stop()', exc_info=True)
            self.mouse_listener_manual_mix_background = None
            self.is_capturing_manual_mix_background = False
            if reason_key:
                try:
                    self.status_callback(tr(reason_key))
                except Exception:
                    log.debug('ignored exception in self.status_callback(tr(reason_key))', exc_info=True)
            else:
                try:
                    self.status_callback(tr("status_manual_mix_canvas_cancelled"))
                except Exception:
                    self.status_callback("Manual mix background capture cancelled.")
            cancelled_something = True
        if getattr(self, 'is_capturing_dynamic_brush_coord', False):
            self._log("Cancelling dynamic brush coordinate capture.")
            listener = getattr(self, 'mouse_listener_dynamic_brush', None)
            if listener and getattr(listener, 'is_alive', lambda: False)():
                try:
                    listener.stop()
                except Exception:
                    log.debug('ignored exception in listener.stop()', exc_info=True)
            self.mouse_listener_dynamic_brush = None
            self.is_capturing_dynamic_brush_coord = False
            self._dynamic_brush_point_capture_value = None
            if reason_key:
                try:
                    self.status_callback(tr(reason_key))
                except Exception:
                    log.debug('ignored exception in self.status_callback(tr(reason_key))', exc_info=True)
            else:
                try:
                    self.status_callback(tr("status_dynamic_brush_capture_cancelled"))
                except Exception:
                    log.debug("ignored exception in self.status_callback(tr('status_dynamic_brush_capture_cancelled'))", exc_info=True)
            cancelled_something = True
        return cancelled_something

    def set_k_clusters(self, k):
        try:
            k_int = int(k)
            if k_int < 1: self._log("K_Clusters must be at least 1.", True); return False
            if self.k_clusters != k_int: self.k_clusters = k_int; self._log(f"K_Clusters set to: {self.k_clusters}")
            return True
        except ValueError: self._log(f"Invalid K_Clusters value: {k}", True); return False

    def recommend_color_count(self, max_colors: int = 32) -> dict:
        """Recommend how many clearly different colours the loaded picture has
        (the user keeps the final choice). Quantize a small copy at a high k in
        OKLab, merge shades closer than the threshold, then count the colours
        that own real areas. Returns {count, max_colors, threshold, raw_k}.

        Antialiasing must not count: black lines on white gave 4 (black, white
        and two greys). The copy is shrunk without blending, transparent pixels
        are ignored, and a colour counts only by its pixels whose 4-neighbours
        mostly share it (a 1-px fringe between two colours never does)."""
        try:
            from .prep_pipeline import perceptual_kmeans_quantize, merge_similar_clusters
            pil = getattr(self, "image_original_rgba", None) or getattr(self, "source_pil_image", None)
            if pil is None:
                return {"count": 0, "reason": "no_image"}
            rgba = np.asarray(pil.convert("RGBA"))
            h, w = rgba.shape[:2]
            scale = max(h, w) / 400.0          # analysis only; nearest keeps real colours
            if scale > 1.0:
                rgba = cv2.resize(rgba, (max(1, int(w / scale)), max(1, int(h / scale))),
                                  interpolation=cv2.INTER_NEAREST)
            opaque = rgba[..., 3] >= 128
            if not opaque.any():
                return {"count": 0, "reason": "no_image"}
            rgb = rgba[..., :3]
            pixels = rgb[opaque]
            uniq = int(len(np.unique(pixels, axis=0)))
            raw_k = int(min(int(max_colors), max(1, uniq)))
            labels, centers, _pal = perceptual_kmeans_quantize(pixels.reshape(-1, 1, 3), k=raw_k, color_space="oklab")
            # 0.0 — легитимное значение «слияние выключено»: берётся не меньше 0.1
            threshold = max(0.1, float(getattr(self, "color_merge_threshold", 0.05)))
            merged, _centers, _ = merge_similar_clusters(np.asarray(labels).reshape(-1), centers, threshold, "oklab")
            label_map = np.full(opaque.shape, -1, np.int64)
            label_map[opaque] = np.asarray(merged).reshape(-1)
            padded = np.pad(label_map, 1, constant_values=-1)
            centre = padded[1:-1, 1:-1]
            same = sum((padded[1 + dy:padded.shape[0] - 1 + dy, 1 + dx:padded.shape[1] - 1 + dx] == centre).astype(np.int8)
                       for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1)))
            solid = opaque & (same >= 3)
            sizes = np.bincount(label_map[solid], minlength=int(label_map.max()) + 1) if solid.any() else np.zeros(1)
            total = max(1, int(opaque.sum()))
            kept = [int(c) for c in np.flatnonzero(sizes >= max(4.0, 0.003 * total))]
            kept = self._drop_transition_colours(label_map, kept, pixels, np.asarray(merged).reshape(-1))
            count = int(max(1, len(kept)))
            # Black-and-white: every real colour is grey (no chroma) and at most one
            # dark and one light remain — line art, text, a black silhouette.
            from .color_spaces import rgb_to_oklab_numpy
            lab = rgb_to_oklab_numpy(np.asarray(pixels, np.float32).reshape(-1, 3))
            merged_flat = np.asarray(merged).reshape(-1)
            centres = [lab[merged_flat == c].mean(axis=0) for c in kept] or [lab.mean(axis=0)]
            grey = all(float(np.hypot(c[1], c[2])) < 0.04 for c in centres)
            dark = sum(1 for c in centres if c[0] < 0.5)
            light = len(centres) - dark
            black_white = grey and dark <= 1 and light <= 1
            return {"count": count, "max_colors": int(max_colors), "threshold": threshold, "raw_k": raw_k,
                    "mode": "bw" if black_white else "color"}
        except Exception:
            log.debug("recommend_color_count failed", exc_info=True)
            return {"count": 0, "reason": "error"}

    @staticmethod
    def _drop_transition_colours(label_map, kept, pixels, merged) -> list:
        """A blurred edge between two colours makes a band of in-between shades
        that can be wider than 1 px. Such a colour lies between two others in
        OKLab and nearly all its pixels touch both of them: it is not counted."""
        from .color_spaces import rgb_to_oklab_numpy
        if len(kept) < 3:
            return kept
        lab = rgb_to_oklab_numpy(np.asarray(pixels, np.float32).reshape(-1, 3))
        centre = {c: lab[merged == c].mean(axis=0) for c in kept}
        area = {c: int(np.count_nonzero(label_map == c)) for c in kept}
        kernel = np.ones((5, 5), np.uint8)
        near = {c: cv2.dilate((label_map == c).astype(np.uint8), kernel) > 0 for c in kept}
        result = list(kept)
        for c in sorted(kept, key=lambda k: area[k]):
            own = label_map == c
            others = [k for k in kept if k != c]  # a band may border another band
            for i, a in enumerate(others):
                for b in others[i + 1:]:
                    ab = centre[b] - centre[a]
                    length = float(np.dot(ab, ab))
                    if length < 1e-9:
                        continue
                    t = float(np.dot(centre[c] - centre[a], ab) / length)
                    off = float(np.linalg.norm(centre[a] + t * ab - centre[c]))
                    touching = np.count_nonzero(own & near[a] & near[b]) / max(1, np.count_nonzero(own))
                    if 0.05 < t < 0.95 and off < 0.06 and touching >= 0.35:
                        result.remove(c)
                        break
                else:
                    continue
                break
        return result

    def set_draw_delay(self, delay):
        try:
            delay_float = float(delay)
            if delay_float < 0: self._log("Draw_delay cannot be negative.", True); return False
            if self.draw_delay != delay_float: self.draw_delay = delay_float; self._log(f"Draw_delay set to: {self.draw_delay}")
            return True
        except ValueError: self._log(f"Invalid Draw_delay value: {delay}", True); return False

    def set_brush_size(self, size):
        try:
            size_int = int(size)
            if size_int < 1: self._log("Brush size must be at least 1.", True); return False
            if self.brush_size != size_int: self.brush_size = size_int; self._log(f"Brush size set to: {self.brush_size}")
            return True
        except ValueError: self._log(f"Invalid Brush size value: {size}", True); return False


    def get_dynamic_brush_settings(self) -> dict:
        return self._dynamic_brush_settings_snapshot()

    def _notify_dynamic_brush_settings_changed(self):
        callback = getattr(self, "dynamic_brush_settings_changed_callback", None)
        if callable(callback):
            try:
                callback(self._dynamic_brush_settings_snapshot())
            except Exception:
                log.debug('ignored exception in callback(self._dynamic_brush_settings_snapshot())', exc_info=True)

    def set_dynamic_brush_control_mode(self, mode: str | None):
        normalized = str(mode or "text").strip().lower()
        if normalized not in ("text", "slider", "points"):
            normalized = "text"
        if normalized == getattr(self, "dynamic_brush_control_mode", "text"):
            return True
        self.dynamic_brush_control_mode = normalized
        self._last_dynamic_brush_value = None
        profile = getattr(self, "dynamic_brush_profile", None)
        if isinstance(profile, dict) and str(profile.get("control_mode") or "text").strip().lower() != normalized:
            # A different control means the calibration no longer applies — but the
            # CAPTURES (coord / slider track / size points / scratch zone) are kept:
            # toggling modes back and forth must not destroy the user's setup.
            profile = dict(profile)
            profile["valid"] = False
            profile["control_mode"] = normalized
            profile["control_params"] = None
            profile["cached_calibration"] = None
            self.dynamic_brush_profile = profile
            self.dynamic_brush_calibration = None
            self._update_dynamic_brush_profile_state()
        self._notify_dynamic_brush_settings_changed()
        return True

    def set_dynamic_brush_enabled(self, enabled: bool):
        enabled_flag = bool(enabled)
        changed = enabled_flag != bool(getattr(self, "dynamic_brush_enabled", False))
        self.dynamic_brush_enabled = enabled_flag
        self._update_dynamic_brush_profile_state()
        if not enabled_flag:
            self.dynamic_brush_session_active = False
            self._set_dynamic_brush_runtime_state("idle")
        if changed:
            self._notify_dynamic_brush_settings_changed()
        return True

    def set_dynamic_brush_drag_enabled(self, enabled: bool):
        flag = bool(enabled)
        if flag != bool(getattr(self, "dynamic_brush_drag_enabled", False)):
            self.dynamic_brush_drag_enabled = flag
            self._log(f"Dynamic brush drag mode: {'on' if flag else 'off'}.")
            self._notify_dynamic_brush_settings_changed()
        return True

    def update_dynamic_brush_settings(self, *, min_value=None, max_value=None, default_value=None, step=None):
        try:
            min_candidate = float(min_value) if min_value is not None else float(self.dynamic_brush_min_value)
            max_candidate = float(max_value) if max_value is not None else float(self.dynamic_brush_max_value)
            default_candidate = float(default_value) if default_value is not None else float(self.dynamic_brush_default_value)
        except Exception as exc:
            self._log(f"Invalid dynamic brush parameters: {exc}", True)
            return False

        value_floor = 0.0 if str(getattr(self, "dynamic_brush_control_mode", "text") or "text").strip().lower() in ("slider", "points") else 0.001
        min_candidate = max(value_floor, min_candidate)
        max_candidate = max(min_candidate, max_candidate)
        default_candidate = min(max_candidate, max(min_candidate, default_candidate))

        step_candidate = self.dynamic_brush_step_value
        if step is not None:
            try:
                requested_step = float(step)
            except Exception:
                requested_step = self.dynamic_brush_step_value
            step_candidate = max(0.0001, abs(requested_step))

        max_candidate = min(9999.0, max_candidate)
        min_candidate = min(max_candidate, max(value_floor, min_candidate))
        default_candidate = min(max_candidate, max(min_candidate, default_candidate))
        default_candidate = self._quantize_dynamic_brush_value(
            default_candidate,
            min_v=min_candidate,
            max_v=max_candidate,
            step=step_candidate,
        )

        changed = (
            not math.isclose(self.dynamic_brush_min_value, min_candidate, rel_tol=1e-06, abs_tol=1e-06)
            or not math.isclose(self.dynamic_brush_max_value, max_candidate, rel_tol=1e-06, abs_tol=1e-06)
            or not math.isclose(self.dynamic_brush_default_value, default_candidate, rel_tol=1e-06, abs_tol=1e-06)
            or not math.isclose(self.dynamic_brush_step_value, step_candidate, rel_tol=1e-06, abs_tol=1e-06)
        )

        self.dynamic_brush_min_value = min_candidate
        self.dynamic_brush_max_value = max_candidate
        self.dynamic_brush_default_value = default_candidate
        self.dynamic_brush_step_value = step_candidate
        profile = getattr(self, "dynamic_brush_profile", None)
        if isinstance(profile, dict):
            profile = dict(profile)
            range_payload = dict(profile.get("range") or {})
            range_payload.update(
                {
                    "min_value": min_candidate,
                    "max_value": max_candidate,
                    "default_value": default_candidate,
                    "step_value": step_candidate,
                }
            )
            profile["range"] = range_payload
            self.dynamic_brush_profile = profile

        if changed:
            self._last_dynamic_brush_value = None
            self._log(
                f"Dynamic brush settings updated: min={min_candidate:.3f}, "
                f"max={max_candidate:.3f}, default={default_candidate:.3f}, step={step_candidate}"
            )
            self._notify_dynamic_brush_settings_changed()
        return True

    def reset_dynamic_brush_profile(self):
        self.dynamic_brush_profile = None
        self.dynamic_brush_profile_state = "needs_learning"
        self.dynamic_brush_runtime_state = "idle"
        self.dynamic_brush_session_active = False
        self.dynamic_brush_coord = None
        self.dynamic_brush_slider_params = None
        self.dynamic_brush_points = []
        self.dynamic_brush_scratch_zone = None
        self.dynamic_brush_calibration = None
        self.dynamic_brush_calibration_updated_at = None
        self._last_dynamic_brush_value = None
        self._notify_dynamic_brush_settings_changed()
        return True







    def _build_dynamic_brush_calibration_payload(
        self,
        samples: list[dict[str, float | str]],
        *,
        shape: str = "unknown",
        shape_confidence: float = 0.0,
        validation: dict | None = None,
        runtime_correction: dict | None = None,
        safety_margin_cells: float | None = None,
        timestamp: float | None = None,
    ) -> dict | None:
        calibration = {
            "version": self._DYNAMIC_BRUSH_PROFILE_VERSION,
            "measurement_revision": self._DYNAMIC_BRUSH_MEASUREMENT_REVISION,
            "required_values": sorted({float(row["value"]) for row in samples}),
            "samples": list(samples or ()),
            "shape": str(shape or "unknown").strip().lower(),
            "shape_confidence": float(shape_confidence or 0.0),
            "validation": dict(validation) if isinstance(validation, dict) else None,
            "runtime_correction": (
                dict(runtime_correction)
                if isinstance(runtime_correction, dict)
                else self._dynamic_brush_identity_runtime_correction()
            ),
            "safety_margin_cells": (
                float(safety_margin_cells)
                if safety_margin_cells is not None
                else self._DYNAMIC_BRUSH_DEFAULT_SAFETY_MARGIN_CELLS
            ),
            "timestamp": float(timestamp) if timestamp is not None else time.time(),
        }
        return self._normalize_dynamic_brush_calibration_payload(calibration)

    def _store_dynamic_brush_calibration(self, calibration, *, update_profile_cache: bool = True):
        normalized = self._normalize_dynamic_brush_calibration_payload(calibration)
        if not isinstance(normalized, dict):
            return False
        self.dynamic_brush_calibration = dict(normalized)
        self.dynamic_brush_calibration_updated_at = normalized.get("timestamp")
        if update_profile_cache:
            profile = getattr(self, "dynamic_brush_profile", None)
            if isinstance(profile, dict):
                profile = dict(profile)
                profile["cached_calibration"] = dict(normalized)
                self.dynamic_brush_profile = profile
        self._last_dynamic_brush_value = None
        self._notify_dynamic_brush_settings_changed()
        return True







    def _grab_patch(self, center_x: int, center_y: int, half: int) -> Image.Image | None:
        left = int(center_x - half)
        top = int(center_y - half)
        right = int(center_x + half)
        bottom = int(center_y + half)
        if left >= right or top >= bottom:
            return None
        try:
            grabbed = capture_screen(bbox=(left, top, right, bottom))
            if grabbed is not None:
                return grabbed.convert("RGB")
        except Exception as exc:
            self._log(f"Dynamic brush calibration capture failed: {exc}", True)
        return None


    # (the v1 probe measurers lived here; v2 measures one stamp's effective radius
    # in DynamicBrushMixin._dynamic_brush_v2_measure_stamp)







    def _mark_dynamic_brush_coverage(self, coverage_mask: np.ndarray | None) -> int:
        if self.drawn_mask is None or coverage_mask is None:
            return 0
        try:
            mask_bool = np.asarray(coverage_mask, dtype=bool)
        except Exception:
            return 0
        if mask_bool.shape != self.drawn_mask.shape:
            return 0
        fresh = mask_bool & (~self.drawn_mask)
        changed = int(np.count_nonzero(fresh))
        if changed <= 0:
            return 0
        self.drawn_mask[fresh] = True
        if self.pixel_update_callback:
            self.pixel_update_callback()
        return changed



    def _region_dimensions(self, *, fallback_cols=1, fallback_rows=1, brush=1) -> tuple[int, int]:
        if isinstance(self.draw_region, tuple) and len(self.draw_region) == 4:
            try:
                _, _, w, h = self.draw_region
                return int(max(1, w)), int(max(1, h))
            except Exception:
                log.debug('ignored exception in _, _, w, h = self.draw_region', exc_info=True)
        return max(1, int(fallback_cols)) * brush, max(1, int(fallback_rows)) * brush

    @staticmethod
    def _cell_center_axis(origin: int, length: int, index: int, brush: int) -> int:
        length = max(1, int(length))
        axis_end = origin + length
        cell_start = origin + index * brush
        cell_end = min(cell_start + brush, axis_end)
        if cell_end <= cell_start:
            return max(origin, min(cell_start, axis_end - 1))
        center = cell_start + (cell_end - cell_start) // 2
        if center >= axis_end:
            center = axis_end - 1
        if center < origin:
            center = origin
        return center


    def _notify_outline_fill_settings_changed(self):
        cb = getattr(self, "outline_fill_settings_changed_callback", None)
        if callable(cb):
            snapshot = self._outline_fill_settings_snapshot()
            try:
                cb(snapshot)
            except Exception:
                log.debug('ignored exception in cb(snapshot)', exc_info=True)

    def get_outline_fill_settings(self) -> dict:
        return self._outline_fill_settings_snapshot()

    def set_outline_fill_tool_mode(self, mode: str):
        normalized = str(mode or "").strip().lower()
        if normalized not in ("keys", "coords"):
            normalized = "keys"
        if normalized == self.outline_fill_tool_mode:
            return True
        self.outline_fill_tool_mode = normalized
        self._notify_outline_fill_settings_changed()
        return True

    def set_outline_fill_brush_key(self, sequence: str):
        seq = str(sequence or "").strip()
        if not seq:
            return False
        self.outline_fill_brush_key = seq
        self._notify_outline_fill_settings_changed()
        if self.status_callback:
            try:
                self.status_callback(tr("status_outline_fill_brush_key_set", key=seq))
            except Exception:
                log.debug("ignored exception in self.status_callback(tr('status_outline_fill_brush_key_set', key=seq))", exc_info=True)
        return True

    def set_outline_fill_fill_key(self, sequence: str):
        seq = str(sequence or "").strip()
        if not seq:
            return False
        self.outline_fill_fill_key = seq
        self._notify_outline_fill_settings_changed()
        if self.status_callback:
            try:
                self.status_callback(tr("status_outline_fill_fill_key_set", key=seq))
            except Exception:
                log.debug("ignored exception in self.status_callback(tr('status_outline_fill_fill_key_set', key=seq))", exc_info=True)
        return True


    def set_outline_fill_coords(self, brush_coord=None, fill_coord=None):
        changed = False
        if isinstance(brush_coord, (list, tuple)) and len(brush_coord) >= 2:
            try:
                self.outline_fill_brush_coord = (int(brush_coord[0]), int(brush_coord[1]))
                changed = True
            except Exception:
                log.debug('ignored exception in self.outline_fill_brush_coord = (int(brush_coord[0]), int(brush_coord[1]))', exc_info=True)
        if isinstance(fill_coord, (list, tuple)) and len(fill_coord) >= 2:
            try:
                self.outline_fill_fill_coord = (int(fill_coord[0]), int(fill_coord[1]))
                changed = True
            except Exception:
                log.debug('ignored exception in self.outline_fill_fill_coord = (int(fill_coord[0]), int(fill_coord[1]))', exc_info=True)
        if changed:
            self._notify_outline_fill_settings_changed()
        return changed

    @capture_start
    def start_outline_fill_coord_capture(self):
        from .coordinate_capture import start_outline
        start_outline(self)











    def set_extra_actions_enabled(self, enabled, slot="pre"):
        slot_key = self._normalize_actions_slot(slot)
        bucket = self._actions_bucket(slot_key)
        flag = bool(enabled)
        capture_active = self.is_capturing_extra_actions and self.active_extra_actions_slot == slot_key
        if flag and not bucket.get("events") and not capture_active:
            self._log(tr("status_error_extra_actions_not_set_slot", slot=self._actions_slot_label(slot_key)), True)
            if self.status_callback:
                try:
                    self.status_callback(tr("status_error_extra_actions_not_set_slot", slot=self._actions_slot_label(slot_key)))
                except Exception:
                    log.debug("ignored exception in self.status_callback(tr('status_error_extra_actions_not_set_slot', slot=self....", exc_info=True)
            self._notify_extra_actions_changed()
            return False
        if bool(bucket.get("enabled")) != flag:
            bucket["enabled"] = flag
            self._notify_extra_actions_changed()
            state_text = "enabled" if flag else "disabled"
            self._log(f"Extra actions ({slot_key}) playback {state_text}.")
        return True

    def _log(self, message, is_error=False):
        prefix = "[ERROR] " if is_error else "[INFO] "
        full_message = prefix + str(message)
        if is_error:
            logging.error(str(message))
        else:
            logging.info(str(message))
        if self.status_callback:
            try:
                self.status_callback(full_message)
            except Exception as e:
                logging.warning(f"Status callback failed: {e}")

    @capture_start
    def start_hex_coord_capture(self):
        from .coordinate_capture import start_hex
        start_hex(self)



    def clear_dynamic_brush_points(self):
        self.dynamic_brush_points = []
        self._last_dynamic_brush_value = None
        if self.status_callback:
            try:
                self.status_callback(tr("status_dynamic_brush_points_cleared"))
            except Exception:
                log.debug("ignored exception in points-cleared status", exc_info=True)
        self._notify_dynamic_brush_settings_changed()
        return True









    def _run_dynamic_brush_calibration(self, *, calibration_region=None, persist: bool = True, status_messages: bool = True):
        """Five range probes (or every preset) build a value->radius curve.
        Complete, consistent observations are required before CLOSED-LOOP verification
        probe stamps a predicted mid-curve value and compares the measured radius
        against the target (failed rounds refine the curve and retry, max 2 rounds).
        Replaces the v1 8-value x 3-probe sweep + drawn-square validation."""
        if self._automation_cancelled():
            return None
        region_rect = self._dynamic_brush_reference_rect(calibration_region if calibration_region is not None else self.draw_region)
        if region_rect is None:
            if status_messages:
                msg = tr("status_dynamic_brush_calibration_area_required")
                self._log(msg, True)
                if self.status_callback:
                    self.status_callback(msg)
            return None
        control_mode = str(getattr(self, "dynamic_brush_control_mode", "text") or "text").strip().lower()
        base_values: list[float] = []
        if control_mode == "points":
            # Points mode: every preset IS a measurable reality — probe each one,
            # no interpolation schedule needed.
            for point in (self.dynamic_brush_points or [])[:8]:
                if not isinstance(point, dict):
                    continue
                try:
                    candidate = float(point["value"])
                except Exception:
                    continue
                if not any(math.isclose(candidate, existing, rel_tol=0.0, abs_tol=1e-9) for existing in base_values):
                    base_values.append(candidate)
            base_values.sort()
        elif control_mode == "text" and bool(getattr(self, "dynamic_brush_text_auto", True)):
            # Nobody knows their program's size range: type a Fibonacci ladder and
            # keep what the canvas actually shows (trimmed after the probes).
            self.update_dynamic_brush_settings(min_value=1.0, max_value=float(self._TEXT_AUTO_VALUES[-1]),
                                               default_value=1.0, step=1.0)
            base_values = [float(v) for v in self._TEXT_AUTO_VALUES]
        elif control_mode == "slider":
            # A track says nothing about pixels («Нарисуй меня!»: 1..265 px over a
            # 275 px track): grow from the small end and stop at the first stamp
            # that does not fit the test zone. Blind quarters of the track painted
            # blots bigger than the zone, over the drawing.
            base_values = [self._quantize_dynamic_brush_value(v) for v in self._SLIDER_AUTO_POSITIONS]
        else:
            min_v = float(self.dynamic_brush_min_value)
            max_v = float(self.dynamic_brush_max_value)
            if max_v <= min_v:
                max_v = min_v + max(1.0, float(self.dynamic_brush_step_value))
            for position in (0.0, 0.25, 0.5, 0.75, 1.0):
                candidate = min_v + (max_v - min_v) * position
                quantized = self._quantize_dynamic_brush_value(candidate, min_v=min_v, max_v=max_v)
                if not any(math.isclose(quantized, existing, rel_tol=0.0, abs_tol=1e-6) for existing in base_values):
                    base_values.append(quantized)
        if len(base_values) < 2:
            if status_messages:
                msg = tr("status_dynamic_brush_calibration_values_failed")
                self._log(msg, True)
                if self.status_callback:
                    self.status_callback(msg)
            return None
        patch_half = self._dynamic_brush_patch_half(region=region_rect)
        # base probes + adaptive gap subdivision + verification rounds. Use the
        # BLANKEST spots in the scratch (avoids stamps left by previous re-learns).
        text_auto = control_mode == "text" and bool(getattr(self, "dynamic_brush_text_auto", True))
        ladder = text_auto or control_mode == "slider"
        points = self._dynamic_brush_v2_clean_spots(region_rect, patch_half, len(base_values) + 9)
        # auto ladders look for a spot per probe; only verification needs these
        if len(points) < (3 if ladder else len(base_values) + 1):
            if status_messages:
                msg = tr("status_dynamic_brush_calibration_area_small")
                self._log(msg, True)
                if self.status_callback:
                    self.status_callback(msg)
            return None
        if status_messages:
            self._log(tr("status_dynamic_brush_calibration_start_log"))
            if self.status_callback:
                self.status_callback(tr("status_dynamic_brush_calibration_start_ui"))
        self._set_dynamic_brush_runtime_state("deep_learning")
        samples: list[dict[str, float | str]] = []
        verification = {"passed": False, "relative_error": 1.0}
        try:
            # One dark probe color so stamps are visible on a light canvas (best-effort;
            # without a usable color-picking method the current paint color is used).
            if self._dynamic_brush_can_pick_calibration_color():
                self._dynamic_brush_apply_calibration_color(color_hex=self._dynamic_brush_probe_color(region_rect))
            verification_count = min(2, len(points) - (0 if ladder else len(base_values)))
            point_iter = iter(points[:-verification_count])
            verification_points = iter(points[-verification_count:])
            probe_failures = 0

            def grow(values) -> tuple[list, str]:
                """Growing sizes, each on a blank spot big enough for it, until a
                stamp stops fitting the zone, stops growing or shrinks. Returns the
                samples and why it stopped."""
                found, previous_px, plateau_count = [], 0.0, 0
                for value in values:
                    if self._automation_cancelled():
                        return found, "cancelled"
                    need_half = int(max(12, min(patch_half, previous_px * 2.2 + 10)))
                    spots = self._dynamic_brush_v2_clean_spots(region_rect, need_half, 1)
                    if not spots:
                        return found, "no_spot"
                    px, py = spots[0]
                    probe = self._dynamic_brush_v2_probe(value, int(px), int(py), need_half, region_rect=region_rect)
                    if not isinstance(probe, dict):
                        # too big for the zone, or the program did not paint it
                        self._log(f"Dynamic brush probe: value {float(value):g} left no measurable stamp; "
                                  "learning stops at the sizes found.")
                        return found, "unmeasured"
                    sample = self._dynamic_brush_v2_make_sample(value, probe)
                    grown = float(probe["radius_px"])
                    if found and grown < previous_px - max(.5, previous_px * .15):
                        # Numeric validators may reject a digit (55 -> 5 for a
                        # field capped at 40), rather than clamp. Keep only the
                        # preceding increasing range and verify it independently.
                        return found, "shrank"
                    if found and grown <= previous_px * 1.05 + 0.3:
                        plateau_count += 1
                        if len(found) >= 2 and plateau_count >= 2:
                            return found, "plateau"  # repeated saturation after observed growth
                        continue  # small sizes can be clamped to the same minimum
                    found.append(sample)
                    previous_px = grown
                    plateau_count = 0
                    if status_messages and self.status_callback:
                        shown = f"{value:g}" if text_auto else f"{round(value * 100, 1):g}% ползунка"
                        self.status_callback(f"Проверяем размер кисти {shown}…")
                return found, "done"

            if ladder:
                samples, stop = grow(base_values)
                params = self.dynamic_brush_slider_params
                if (control_mode == "slider" and len(samples) < 2 and stop == "shrank"
                        and isinstance(params, (tuple, list)) and len(params) == 4):
                    # Measured stamps shrink along the track: its small end is the
                    # other one. Only measured evidence swaps the ends — a stamp at
                    # the far end of a normal track is a blot over the drawing.
                    orientation, fixed, coord_max, coord_min = params
                    self.dynamic_brush_slider_params = (orientation, fixed, coord_min, coord_max)
                    self._last_dynamic_brush_value = None
                    self._log("Dynamic brush: the slider grows the other way; track ends swapped automatically.")
                    samples, stop = grow(base_values)
            for idx, value in enumerate(() if ladder else base_values, start=1):
                if self._automation_cancelled():
                    break
                try:
                    px, py = next(point_iter)
                except StopIteration:
                    break
                probe = self._dynamic_brush_v2_probe(value, int(px), int(py), patch_half, region_rect=region_rect)
                if isinstance(probe, dict):
                    samples.append(self._dynamic_brush_v2_make_sample(value, probe))
                else:
                    probe_failures += 1
                    self._log(f"Dynamic brush probe: value {float(value):g} left no measurable stamp")
                    # retry once on the next (also blank) spot — the first may have
                    # been a leftover stamp / a spot the click missed.
                    try:
                        rx, ry = next(point_iter)
                    except StopIteration:
                        rx = ry = None
                    if rx is not None and not self._automation_cancelled():
                        retry = self._dynamic_brush_v2_probe(value, int(rx), int(ry), patch_half, region_rect=region_rect)
                        if isinstance(retry, dict):
                            samples.append(self._dynamic_brush_v2_make_sample(value, retry))
                            probe_failures -= 1
                if status_messages and self.status_callback:
                    self.status_callback(
                        tr("status_dynamic_brush_calibration_sample_done", idx=idx, total=len(base_values), value=value)
                    )
            if probe_failures:
                self._log(f"Dynamic brush calibration: {probe_failures}/{len(base_values)} probes left no detectable stamp.")
                measured = sorted({float(s["value"]) for s in samples})
                missing = [v for v in base_values if not any(math.isclose(v, m, abs_tol=1e-6) for m in measured)]
                if control_mode != "points" and len(measured) >= 3 and missing and min(missing) > measured[-1]:
                    # The largest sizes did not fit the test zone: they are the
                    # upper limit of what can be learned here, not a broken control.
                    base_values = [v for v in base_values if v <= measured[-1] + 1e-9]
                    probe_failures = 0
                    self._log(f"Dynamic brush: sizes above {measured[-1]:g} do not fit the test zone; learning up to it.")
            if text_auto and len(samples) >= 2:
                base_values = sorted(float(s["value"]) for s in samples)
                self.update_dynamic_brush_settings(min_value=base_values[0], max_value=base_values[-1],
                                                   default_value=base_values[0], step=1.0)
                self._log(f"Dynamic brush: size field range found automatically {base_values[0]:g}..{base_values[-1]:g}.")
            elif ladder and len(samples) >= 2:
                # The track keeps its ends (positions map onto it); the program is
                # left at the size nearest the drawing's own brush, not at a fifth
                # of the track (a 53 px blot in «Нарисуй меня!»).
                base_values = sorted(float(s["value"]) for s in samples)
                base_px = max(.5, float(self.brush_size) / 2.0)
                rest = min(samples, key=lambda s: abs(float(s["point_reach_cells"]) * float(s.get("brush_px", 1.0))
                                                      - base_px))
                self.update_dynamic_brush_settings(default_value=float(rest["value"]))
                self._log(f"Dynamic brush: slider sizes measured up to {base_values[-1]:g} of the track.")
            if control_mode == "slider" and self._dynamic_brush_flip_reversed_slider(samples):
                base_values = sorted(float(s["value"]) for s in samples)
            # Adaptive densification (sliders/text only): a non-linear control can
            # jump several-fold between two neighbouring probes — subdivide the
            # widest radius gap until adjacent samples are within ~1.45x: the pocket
            # planner uses ONLY measured sizes, so a dense ladder = big strokes fit
            # more places and nothing between probes is ever guessed.
            if control_mode != "points" and self._dynamic_brush_samples_consistent(samples, base_values):
                extra_left = 6
                while extra_left > 0 and not self._automation_cancelled() and len(samples) >= 2:
                    rows = sorted(samples, key=lambda r: float(r.get("value", 0.0) or 0.0))
                    worst_pair = None
                    worst_ratio = 1.45
                    for low, high in zip(rows, rows[1:]):
                        low_r = float(low.get("point_reach_cells", 0.0) or 0.0)
                        high_r = float(high.get("point_reach_cells", 0.0) or 0.0)
                        if low_r <= 0:
                            continue
                        ratio = high_r / low_r
                        if ratio > worst_ratio:
                            worst_ratio = ratio
                            worst_pair = (low, high)
                    if worst_pair is None:
                        break
                    middle_value = self._quantize_dynamic_brush_value(
                        (float(worst_pair[0]["value"]) + float(worst_pair[1]["value"])) / 2.0
                    )
                    if any(
                        math.isclose(middle_value, float(row.get("value", 0.0) or 0.0), rel_tol=0.0, abs_tol=1e-6)
                        for row in rows
                    ):
                        break
                    # A middle stamp is at most as big as the upper neighbour: look for a
                    # fresh blank spot sized for IT (the pre-planned spots are sized for
                    # the biggest stamp and run out in a small scratch zone).
                    upper_px = float(worst_pair[1].get("point_reach_cells", 0.0) or 0.0) * float(
                        worst_pair[1].get("brush_px", 1.0) or 1.0)
                    need_half = int(max(12, min(patch_half, upper_px * 1.3 + 6)))
                    spots = self._dynamic_brush_v2_clean_spots(region_rect, need_half, 1)
                    if not spots:
                        break
                    px, py = spots[0]
                    probe = self._dynamic_brush_v2_probe(middle_value, int(px), int(py), need_half, region_rect=region_rect)
                    if isinstance(probe, dict):
                        samples.append(self._dynamic_brush_v2_make_sample(middle_value, probe))
                    extra_left -= 1
            if not probe_failures and self._dynamic_brush_samples_consistent(samples, base_values) and not self._automation_cancelled():
                verification = self._dynamic_brush_v2_verify(samples, verification_points, patch_half, region_rect=region_rect)
        finally:
            try:
                if not self._automation_cancelled():
                    self._apply_dynamic_brush_value(self.dynamic_brush_default_value, force=True)
            except Exception:
                log.debug('ignored exception in self._apply_dynamic_brush_value(self.dynamic_brush_default_value, force=True)', exc_info=True)
        if self._automation_cancelled():
            self._set_dynamic_brush_runtime_state("idle")
            return None
        if len(samples) < 2:
            self._set_dynamic_brush_runtime_state("idle")
            if status_messages:
                # Specific, actionable: the stamps weren't detectable (the usual
                # cause is a scratch zone already covered in paint from a previous
                # run, or a brush colour that blends with the canvas).
                msg = tr("status_dynamic_brush_calibration_no_stamp")
                self._log(msg, True)
                if self.status_callback:
                    self.status_callback(msg)
            return None
        samples = self._dynamic_brush_drop_contradicting_extras(samples, base_values)
        if probe_failures or not self._dynamic_brush_samples_consistent(samples, base_values):
            self._set_dynamic_brush_runtime_state("validation_failed")
            if status_messages:
                msg = tr("status_dynamic_brush_calibration_inconsistent")
                self._log(msg, True)
                if self.status_callback:
                    self.status_callback(msg)
            return None
        shape, shape_confidence = self._dynamic_brush_shape_summary_from_samples(samples)
        # GUARD: if the control NEVER changed the brush (every probe measured the
        # same radius), the calibration is useless — the dynamic brush would then
        # have no big sizes and silently draw everything with the smallest one.
        # Refuse loudly so the user fixes the control instead of getting a bad fill.
        degenerate, degenerate_msg = self._dynamic_brush_calibration_degenerate(samples)
        if degenerate:
            self._set_dynamic_brush_runtime_state("validation_failed")
            if status_messages:
                self._log(degenerate_msg, True)
                if self.status_callback:
                    self.status_callback(degenerate_msg)
            return None
        validation = {
            "target_rect": None,
            "outside_pixels": 0,
            "inside_coverage": max(0.0, min(1.0, 1.0 - float(verification.get("relative_error", 1.0) or 0.0))),
            "passed": bool(verification.get("passed")),
        }
        calibration_payload = self._build_dynamic_brush_calibration_payload(
            samples,
            shape=shape,
            shape_confidence=shape_confidence,
            validation=validation,
            runtime_correction=self._dynamic_brush_identity_runtime_correction(),
            safety_margin_cells=self._DYNAMIC_BRUSH_DEFAULT_SAFETY_MARGIN_CELLS,
        )
        if not isinstance(calibration_payload, dict):
            self._set_dynamic_brush_runtime_state("idle")
            return None
        # Edge-pass gap warning: the static base pass steps on the brush_size grid,
        # so a smallest brush narrower than that grid leaves gaps along edges.
        try:
            smallest_d = 2.0 * min(float(r.get("point_reach_cells", 0.0) or 0.0) for r in samples) * max(1, int(self.brush_size))
            if smallest_d + 0.5 < float(self.brush_size) and status_messages:
                warn = tr("status_dynamic_brush_smallest_below_grid", px=round(smallest_d, 1), cell=int(self.brush_size))
                self._log(warn, True)
                if self.status_callback:
                    self.status_callback(warn)
        except Exception:
            log.debug("ignored exception in smallest-vs-grid warning", exc_info=True)
        if persist:
            self._store_dynamic_brush_calibration(calibration_payload, update_profile_cache=True)
        else:
            self.dynamic_brush_calibration = dict(calibration_payload)
            self.dynamic_brush_calibration_updated_at = calibration_payload["timestamp"]
            self._notify_dynamic_brush_settings_changed()
        if status_messages:
            validation_passed = bool(validation.get("passed"))
            status_key = (
                "status_dynamic_brush_calibration_success"
                if validation_passed
                else "status_dynamic_brush_calibration_validation_failed"
            )
            status_msg = tr(
                status_key,
                samples=self._dynamic_brush_sample_count(calibration_payload),
                shape=str(calibration_payload.get("shape", "unknown") or "unknown"),
                coverage=float(validation.get("inside_coverage", 0.0) or 0.0),
                outside=int(validation.get("outside_pixels", 0) or 0),
            )
            self._log(status_msg, not validation_passed)
            if self.status_callback:
                self.status_callback(status_msg)
        self._set_dynamic_brush_runtime_state("validation_passed" if bool(validation.get("passed")) else "validation_failed")
        return calibration_payload

    def learn_dynamic_brush_profile(self):
        if self.drawing_enabled or self._automation_cancelled():
            return False
        reference_rect = self._dynamic_brush_reference_rect()
        if reference_rect is None:
            msg = tr("status_dynamic_brush_calibration_area_required")
            self._log(msg, True)
            if self.status_callback:
                self.status_callback(msg)
            return False
        if not self._dynamic_brush_control_ready():
            mode = str(getattr(self, "dynamic_brush_control_mode", "text") or "text").strip().lower()
            msg = tr("status_error_dynamic_brush_slider_missing") if mode == "slider" else tr("status_error_dynamic_brush_coord_missing")
            self._log(msg, True)
            if self.status_callback:
                self.status_callback(msg)
            return False
        scratch_zone = (self._dynamic_brush_reference_rect(self.dynamic_brush_scratch_zone)
                        if self.dynamic_brush_scratch_zone is not None else None)
        if scratch_zone is None:
            msg = tr("status_error_dynamic_brush_scratch_missing")
            self._log(msg, True)
            if self.status_callback:
                self.status_callback(msg)
            return False
        if self.is_calibrating_dynamic_brush:
            return False
        self.is_calibrating_dynamic_brush = True
        previous_control = {name: getattr(self, name) for name in (
            "dynamic_brush_min_value", "dynamic_brush_max_value", "dynamic_brush_default_value",
            "dynamic_brush_step_value", "dynamic_brush_slider_params")}
        learned = False
        control_mode = str(getattr(self, "dynamic_brush_control_mode", "text") or "text").strip().lower()
        if control_mode == "slider":
            auto_range = self._dynamic_brush_slider_auto_range()
            self.update_dynamic_brush_settings(
                min_value=auto_range["min_value"],
                max_value=auto_range["max_value"],
                default_value=auto_range["default_value"],
                step=auto_range["step_value"],
            )
        elif control_mode == "points":
            point_values = sorted(
                float(p["value"]) for p in (self.dynamic_brush_points or []) if isinstance(p, dict)
            )
            if len(point_values) < 2:
                msg = tr("status_dynamic_brush_points_missing")
                self._log(msg, True)
                if self.status_callback:
                    self.status_callback(msg)
                self.is_calibrating_dynamic_brush = False
                return False
            self.update_dynamic_brush_settings(
                min_value=point_values[0],
                max_value=point_values[-1],
                default_value=point_values[0],
            )
        previous_profile = dict(self.dynamic_brush_profile) if isinstance(self.dynamic_brush_profile, dict) else None
        previous_calibration = dict(self.dynamic_brush_calibration) if isinstance(self.dynamic_brush_calibration, dict) else None
        try:
            calibration = self._run_dynamic_brush_calibration(calibration_region=scratch_zone, persist=False, status_messages=True)
            if (not isinstance(calibration, dict) or self._automation_cancelled()
                    or not calibration.get("validation", {}).get("passed")):
                if previous_profile is not None:
                    self.dynamic_brush_profile = previous_profile
                self.dynamic_brush_calibration = previous_calibration
                self._update_dynamic_brush_profile_state()
                self._notify_dynamic_brush_settings_changed()
                return False
            if control_mode == "slider":
                control_params = self._dynamic_brush_normalize_slider_params(self.dynamic_brush_slider_params, reference_rect=reference_rect)
                range_payload = self._dynamic_brush_slider_auto_range()
            elif control_mode == "points":
                control_params = self._dynamic_brush_normalize_points(self.dynamic_brush_points, reference_rect=reference_rect)
                range_payload = {
                    "min_value": float(self.dynamic_brush_min_value),
                    "max_value": float(self.dynamic_brush_max_value),
                    "default_value": float(self.dynamic_brush_default_value),
                    "step_value": float(self.dynamic_brush_step_value),
                }
            else:
                control_params = self._dynamic_brush_normalize_point(self.dynamic_brush_coord, reference_rect=reference_rect)
                range_payload = {
                    "min_value": float(self.dynamic_brush_min_value),
                    "max_value": float(self.dynamic_brush_max_value),
                    "default_value": float(self.dynamic_brush_default_value),
                    "step_value": float(self.dynamic_brush_step_value),
                }
            scratch_norm = self._dynamic_brush_normalize_rect(scratch_zone, reference_rect=reference_rect)
            if control_params is None or scratch_norm is None:
                return False
            validation = calibration.get("validation") if isinstance(calibration.get("validation"), dict) else {}
            validation_passed = bool(validation.get("passed"))
            profile = {
                "version": self._DYNAMIC_BRUSH_PROFILE_VERSION,
                "valid": validation_passed,
                "control_mode": control_mode,
                "control_params": control_params,
                "scratch_zone": scratch_norm,
                "range": dict(range_payload),
                "cached_calibration": dict(calibration),
                "learned_at": time.time(),
            }
            if control_mode == "slider":
                profile["value_scale"] = "normalized"
            self.dynamic_brush_profile = profile
            self.dynamic_brush_enabled = True
            self.dynamic_brush_calibration = dict(calibration)
            self.dynamic_brush_calibration_updated_at = calibration.get("timestamp")
            self.dynamic_brush_session_active = False
            self._update_dynamic_brush_profile_state()
            self._set_dynamic_brush_runtime_state("ready" if validation_passed else "validation_failed")
            self._sync_dynamic_brush_profile_runtime()
            learned = validation_passed
            return validation_passed
        finally:
            if not learned:
                for name, value in previous_control.items():
                    setattr(self, name, value)
                self.dynamic_brush_profile = previous_profile
                self.dynamic_brush_calibration = previous_calibration
                self._update_dynamic_brush_profile_state()
            # Cached positions are relative to the old range/direction after a
            # failed run; the next attempt must apply its first value afresh.
            self._last_dynamic_brush_value = None
            self.is_calibrating_dynamic_brush = False
            self._notify_dynamic_brush_settings_changed()

    def prepare_dynamic_brush_for_draw(self):
        self.dynamic_brush_session_active = False
        if not self._dynamic_brush_requested_for_current_draw():
            self._set_dynamic_brush_runtime_state("idle")
            return False
        self._sync_dynamic_brush_profile_runtime()
        if self._update_dynamic_brush_profile_state() != "ready":
            self.dynamic_brush_calibration = None
            self._set_dynamic_brush_runtime_state("needs_learning")
            return False
        if not self._dynamic_brush_control_ready():
            self.dynamic_brush_calibration = None
            self._set_dynamic_brush_runtime_state("invalid")
            return False
        scratch_zone = self._dynamic_brush_reference_rect(self.dynamic_brush_scratch_zone)
        if scratch_zone is None:
            self.dynamic_brush_calibration = None
            self._set_dynamic_brush_runtime_state("invalid")
            return False
        cached = self._normalize_dynamic_brush_calibration_payload(self._dynamic_brush_cached_calibration())
        if not isinstance(cached, dict):
            self.dynamic_brush_calibration = None
            self._set_dynamic_brush_runtime_state("invalid")
            return False
        # v2: no blanket runtime-correction sweep before every run. Instead each
        # APPLIED size is verified by one test stamp at draw time (see
        # _dynamic_brush_v2_apply_and_measure) — reset that session state here.
        self._dyn2_base_value = None
        self._dyn2_scratch_probe_index = 0
        self._dyn2_detail_notice = False
        # Calibration and the user move the control; its real position is unknown,
        # so the first size of the session must be applied, not deduplicated.
        self._last_dynamic_brush_value = None
        self.dynamic_brush_calibration = dict(cached)
        self.dynamic_brush_calibration_updated_at = cached.get("timestamp")
        self.dynamic_brush_session_active = True
        self._notify_dynamic_brush_settings_changed()
        self._set_dynamic_brush_runtime_state("ready")
        return True

    def preprocess_image(self, img=None):
        if img is None:
            if isinstance(self.source_pil_image, Image.Image):
                return self.source_pil_image
            return self.image_original_rgba
        return img

    def select_area(self):
        if self.drawing_enabled:
            self._log(tr("status_error_drawing_active"), True)
            self.status_callback(tr("status_error_drawing_active"))
            return
        self._cancel_active_captures(reason_key="status_area_selection_cancelled_hex_wait")

        self._log("Selecting area.")
        self.status_callback("Start area selection (Enter/C)")

        try:
            bbox = self._get_app_monitor_bbox()  # (L,T,R,B)
            offset_x = offset_y = 0
            if bbox and len(bbox) == 4:
                l, t, r, b = bbox
                offset_x, offset_y = int(l), int(t)
                # Снимаем скриншот только этого монитора
                screenshot = capture_screen(bbox=(l, t, r, b))
            else:
                # запасной вариант — как было
                screenshot = capture_screen(all_screens=True)

            img_np = np.array(screenshot)
            img_cv = cv2.cvtColor(img_np, cv2.COLOR_RGB2BGR)

            window_title = "Select area (Enter - confirm, C - cancel)"
            cv2.namedWindow(window_title, cv2.WINDOW_NORMAL)
            cv2.setWindowProperty(window_title, cv2.WND_PROP_TOPMOST, 1)
            x, y, w, h = cv2.selectROI(window_title, img_cv, showCrosshair=True, fromCenter=False)
            cv2.destroyWindow(window_title)
        except Exception as e:
            self._log(f"Area selection error: {e}", True)
            self.status_callback(tr("status_selection_error", e=e))
            return

        if w > 0 and h > 0:
            # важное: приводим координаты к абсолютным
            abs_x = int(x + offset_x)
            abs_y = int(y + offset_y)
            self.apply_viewport_state(
                draw_region_desktop_px=(abs_x, abs_y, int(w), int(h)),
                reset_manual=True,
            )
            self._log(f"Area selected: {self.draw_region}")
            self.status_callback(tr("status_area_selected", x=abs_x, y=abs_y, w=w, h=h))

            if self.IMAGE_PATH:
                self.status_callback(tr("status_area_selected_process_now"))
                self.prepare_image_and_palette()
            else:
                self.status_callback(tr("status_area_selected_select_image"))

            if self.stencil_enabled:
                self._update_active_stencil()
        else:
            self._log("Area selection cancelled.")
            self.status_callback(tr("status_area_selection_cancelled"))
            self.apply_viewport_state(
                draw_region_desktop_px=None,
                manual_rect_area_px=None,
                reset_manual=False,
            )
            if self.stencil_enabled and self.hide_stencil_callback:
                self.hide_stencil_callback(blocking=True)


    def layer_capture_points(self):
        from .coordinate_capture import layer_points
        return layer_points(self)

    def _notify_layers_changed(self):
        cb = getattr(self, 'layers_changed_callback', None)
        if callable(cb):
            try:
                cb(self.layer_capture_points())
            except Exception:
                log.debug('ignored exception in cb(self.target_app_layer_coords)', exc_info=True)

    def _notify_manual_palette_changed(self):
        cb = getattr(self, 'manual_palette_changed_callback', None)
        if callable(cb):
            try:
                cb(self.manual_palette_coords)
            except Exception:
                log.debug('ignored exception in cb(self.manual_palette_coords)', exc_info=True)
    def _notify_extra_actions_changed(self):
        cb = getattr(self, 'extra_actions_changed_callback', None)
        if callable(cb):
            try:
                cb(self._extra_actions_summary())
            except Exception:
                log.debug('ignored exception in cb(self._extra_actions_summary())', exc_info=True)

    def _sample_screen_rgb(self, x: int, y: int):
        try:
            bbox = (int(x), int(y), int(x) + 1, int(y) + 1)
            grabbed = capture_screen(bbox=bbox)
            if grabbed is None:
                return None
            pix = grabbed.convert('RGB').getpixel((0, 0))
            return (int(pix[0]), int(pix[1]), int(pix[2]))
        except Exception as exc:
            self._log(f"Screen sample error: {exc}", True)
            return None

    def _calculate_luminance(self, rgb):
        r, g, b = rgb
        return 0.299 * r + 0.587 * g + 0.114 * b

    def _normalize_prep_color_space(self, value) -> str:
        normalized = str(value or "").strip().lower()
        allowed = {"legacy_rgb", "cielab", "oklab"}
        if normalized not in allowed:
            return "legacy_rgb"
        return normalized

    def _normalize_prep_quantization_mode(self, value) -> str:
        normalized = str(value or "").strip().lower()
        allowed = {"kmeans", "minibatch", "imagequant"}
        if normalized not in allowed:
            return "kmeans"
        return normalized

    def _palette_match_metric_value(self) -> str:
        v = str(getattr(self, "palette_match_metric", "ciede2000") or "").strip().lower()
        return v if v in {"euclidean", "ciede2000"} else "ciede2000"

    def _normalize_manual_match_space(self, value) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in {"lab", "oklab"}:
            return "oklab"
        return normalized

    def _normalize_fill_traversal_mode(self, value) -> str:
        normalized = str(value or "").strip().lower()
        if normalized not in {"auto", "gilbert", "fermat"}:
            return "auto"
        return normalized

    def _normalize_prep_cleanup_mode(self, value) -> str:
        normalized = str(value or "").strip().lower()
        allowed = {"off", "vectorized", "vectorized_plus_stray_merge"}
        if normalized not in allowed:
            return "off"
        return normalized

    def _palette_lightness(self, rgb) -> float:
        if rgb is None:
            return 0.0
        color_space = self._normalize_prep_color_space(getattr(self, "prep_color_space", "legacy_rgb"))
        if color_space in {"cielab", "oklab"}:
            try:
                return float(perceptual_lightness_from_rgb(rgb, color_space=color_space))
            except Exception:
                log.debug('ignored exception in return float(perceptual_lightness_from_rgb(rgb, color_space=color_space))', exc_info=True)
        return float(self._calculate_luminance(rgb))

    def _palette_saturation(self, rgb) -> float:
        """HSV saturation in [0,1]: 0 for neutrals (white / gray / black), ~1 for
        the most vivid colors. Secondary palette-order key so that among colors of
        SIMILAR lightness the more saturated (vivid) ones are painted first."""
        if rgb is None:
            return 0.0
        try:
            r, g, b = (float(c) for c in rgb[:3])
        except Exception:
            return 0.0
        mx = max(r, g, b)
        mn = min(r, g, b)
        return 0.0 if mx <= 0 else (mx - mn) / mx

    def _build_palette_from_cluster_map(
        self,
        rgb_map_small: np.ndarray,
        background_cluster_id: int,
        *,
        use_perceptual_lightness: bool = False,
    ) -> list[tuple[str, int, int, tuple[int, int, int]]]:
        if self.cluster_map is None or rgb_map_small is None or rgb_map_small.size == 0:
            return []

        temp_palette = []
        for cid in np.unique(self.cluster_map):
            cid_int = int(cid)
            if cid_int == background_cluster_id or cid_int < 0:
                continue
            cluster_mask = self.cluster_map == cid_int
            count_val = int(np.count_nonzero(cluster_mask))
            if count_val <= 0:
                continue
            cluster_pixels = rgb_map_small[cluster_mask]
            if cluster_pixels.size == 0:
                continue
            mean_rgb = tuple(
                int(max(0, min(255, round(float(v)))))
                for v in cluster_pixels.reshape(-1, 3).mean(axis=0)
            )
            lightness = self._palette_lightness(mean_rgb) if use_perceptual_lightness else self._calculate_luminance(mean_rgb)
            temp_palette.append(
                {
                    "hex": "#{:02X}{:02X}{:02X}".format(*mean_rgb),
                    "id": cid_int,
                    "count": count_val,
                    "rgb": mean_rgb,
                    "luminance": lightness,
                }
            )
        sorted_palette = self._sort_palette_entries(temp_palette)
        return [(p["hex"], p["id"], p["count"], p["rgb"]) for p in sorted_palette]
    def _invalidate_manual_palette_cache(self):
        self._manual_palette_cache = None
        self._manual_palette_match_cache = {}
        self._manual_palette_target_rgb = {}
        self._manual_palette_mix_solution_cache = {}
        self._manual_palette_cluster_plan = {}
        self._manual_palette_force_entry = None
        self._manual_palette_force_alpha = None
        self._manual_palette_force_disable_mix = False
        self._manual_mix_warned_alpha = False

    @property
    def manual_palette_mix_enabled(self) -> bool:
        return getattr(self, "_manual_palette_mix_enabled", False)

    @manual_palette_mix_enabled.setter
    def manual_palette_mix_enabled(self, value: bool) -> None:
        new_val = bool(value)
        prev = getattr(self, "_manual_palette_mix_enabled", False)
        self._manual_palette_mix_enabled = new_val
        if new_val != prev:
            self._manual_palette_mix_solution_cache = {}
        self._manual_mix_warned_alpha = False

    @property
    def manual_palette_mix_alpha(self) -> float:
        return getattr(self, "_manual_palette_mix_alpha", 1.0)

    @manual_palette_mix_alpha.setter
    def manual_palette_mix_alpha(self, value: float) -> None:
        try:
            numeric = float(value)
        except Exception:
            numeric = 0.5
        clamped = max(0.0, min(1.0, numeric))
        prev = getattr(self, "_manual_palette_mix_alpha", None)
        self._manual_palette_mix_alpha = clamped
        if prev is None or abs(clamped - prev) > 1e-6:
            self._manual_palette_mix_solution_cache = {}
        self._manual_mix_warned_alpha = False

    def _srgb_to_lab_array(self, rgb_array):
        if rgb_array is None:
            return np.zeros((0, 3), dtype=np.float32)
        arr = np.asarray(rgb_array, dtype=np.float32)
        if arr.ndim == 1:
            arr = arr.reshape(1, 3)
        if arr.size == 0:
            return np.zeros((0, 3), dtype=np.float32)

        arr = np.clip(arr, 0.0, 255.0) / 255.0
        threshold = 0.04045
        linear = np.where(arr <= threshold, arr / 12.92, ((arr + 0.055) / 1.055) ** 2.4)

        r = linear[..., 0]
        g = linear[..., 1]
        b = linear[..., 2]

        X = r * 0.4124564 + g * 0.3575761 + b * 0.1804375
        Y = r * 0.2126729 + g * 0.7151522 + b * 0.0721750
        Z = r * 0.0193339 + g * 0.1191920 + b * 0.9503041

        X /= 0.95047
        Y /= 1.00000
        Z /= 1.08883

        delta = 6.0 / 29.0
        delta3 = delta ** 3
        factor = 1.0 / (3 * delta * delta)
        offset = 4.0 / 29.0

        def f(t):
            return np.where(t > delta3, np.cbrt(t), factor * t + offset)

        fx = f(X)
        fy = f(Y)
        fz = f(Z)

        L = 116.0 * fy - 16.0
        a = 500.0 * (fx - fy)
        b_lab = 200.0 * (fy - fz)

        lab = np.stack((L, a, b_lab), axis=-1)
        return lab.astype(np.float32)

    def _srgb_to_lab_single(self, rgb_tuple):
        lab = self._srgb_to_lab_array(rgb_tuple)
        if lab.shape[0] == 0:
            return np.zeros(3, dtype=np.float32)
        return lab[0]

    def _srgb_to_oklab_single(self, rgb_tuple):
        # x100 puts OKLab (L in 0..1) on the CIELAB magnitude scale so any distance
        # logged next to legacy dE values stays comparable at a glance.
        okl = rgb_to_oklab_numpy(np.asarray([rgb_tuple], dtype=np.float64)) * 100.0
        return okl[0].astype(np.float32)

    def _manual_match_key(self) -> str:
        """Palette-entry field + converter used for manual-palette nearest matching."""
        return "okl" if self._normalize_manual_match_space(
            getattr(self, "manual_match_space", "oklab")) == "oklab" else "lab"

    def _srgb_to_match_single(self, rgb_tuple):
        if self._manual_match_key() == "okl":
            return self._srgb_to_oklab_single(rgb_tuple)
        return self._srgb_to_lab_single(rgb_tuple)

    def _build_manual_mix_target_map(self, manual_indices, counts_array, pixels_for_clustering):
        target_map: dict[int, tuple[int, int, int]] = {}
        try:
            if manual_indices is None or counts_array is None or pixels_for_clustering is None:
                return target_map
            manual_idx_arr = np.asarray(manual_indices, dtype=np.int64)
            if manual_idx_arr.size == 0:
                return target_map
            counts = np.asarray(counts_array, dtype=np.int64)
            if counts.size == 0:
                return target_map
            max_len = counts.shape[0]
            sums = np.zeros((max_len, 3), dtype=np.float64)
            pixels_arr = np.asarray(pixels_for_clustering, dtype=np.float64)
            if pixels_arr.ndim != 2 or pixels_arr.shape[1] != 3:
                return target_map
            np.add.at(sums, manual_idx_arr, pixels_arr)
            for idx, count_val in enumerate(counts):
                if count_val > 0 and idx < sums.shape[0]:
                    avg = sums[idx] / float(count_val)
                    target_map[idx] = tuple(
                        int(round(max(0.0, min(255.0, value)))) for value in avg
                    )
        except Exception as exc:
            self._log(f"Manual mix target computation error: {exc}", True)
        return target_map

    def set_manual_mix_canvas_rgb(self, rgb_tuple: tuple[int, int, int] | list[int]) -> bool:
        try:
            r, g, b = (int(max(0, min(255, round(float(v))))) for v in rgb_tuple)
            new_rgb = (r, g, b)
        except Exception:
            return False
        prev_rgb = getattr(self, "manual_mix_canvas_rgb", (255, 255, 255))
        if prev_rgb == new_rgb:
            return True
        self.manual_mix_canvas_rgb = new_rgb
        self._manual_palette_mix_solution_cache = {}
        try:
            if self.status_callback:
                hex_value = "#{:02X}{:02X}{:02X}".format(*new_rgb)
                try:
                    self.status_callback(tr("status_manual_mix_canvas_set", hex=hex_value))
                except Exception:
                    self.status_callback(f"Manual mix canvas colour set to {hex_value}.")
        except Exception:
            log.debug("ignored exception in if self.status_callback: hex_value = '#{:02X}{:02X}{:02X}'.format(*new_rgb) t...", exc_info=True)
        try:
            cb = getattr(self, "manual_mix_canvas_changed_callback", None)
            if callable(cb):
                cb(new_rgb)
        except Exception:
            log.debug("ignored exception in cb = getattr(self, 'manual_mix_canvas_changed_callback', None)", exc_info=True)
        return True

    def _finish_manual_mix_background_capture(self, success: bool = False) -> None:
        self._end_capture_session()
        listener = getattr(self, 'mouse_listener_manual_mix_background', None)
        if listener and getattr(listener, 'is_alive', lambda: False)():
            try:
                listener.stop()
            except Exception:
                log.debug('ignored exception in listener.stop()', exc_info=True)
        self.mouse_listener_manual_mix_background = None
        self.is_capturing_manual_mix_background = False
        if not success:
            try:
                if self.status_callback:
                    self.status_callback(tr("status_manual_mix_canvas_cancelled"))
            except Exception:
                log.debug("ignored exception in if self.status_callback: self.status_callback(tr('status_manual_mix_canvas_ca...", exc_info=True)

    def _average_screen_rgb_block(self, x: int, y: int, half_size: int = 7) -> tuple[int, int, int] | None:
        left = int(max(0, x - half_size))
        top = int(max(0, y - half_size))
        right = int(x + half_size + 1)
        bottom = int(y + half_size + 1)
        try:
            grabbed = capture_screen(bbox=(left, top, right, bottom))
        except Exception as exc:
            self._log(f"Manual mix canvas sample grab error: {exc}", True)
            return None
        if grabbed is None:
            return None
        try:
            np_pixels = np.asarray(grabbed.convert("RGB"), dtype=np.float32)
            if np_pixels.size == 0:
                return None
            mean = np_pixels.mean(axis=(0, 1))
            return tuple(int(max(0, min(255, round(float(v))))) for v in mean)
        except Exception as exc:
            self._log(f"Manual mix canvas sample average error: {exc}", True)
            return None

    def _capture_draw_region_rgb(self):
        region = getattr(self, "draw_region", None)
        if not region or len(region) < 4:
            return None
        try:
            x0, y0, w, h = (int(region[0]), int(region[1]), int(region[2]), int(region[3]))
        except Exception:
            return None
        if w <= 0 or h <= 0:
            return None
        try:
            grabbed = capture_screen(bbox=(x0, y0, x0 + w, y0 + h))
        except Exception as exc:
            self._log(f"Post-draw repair capture failed: {exc}", True)
            return None
        if grabbed is None:
            return None
        try:
            return np.asarray(grabbed.convert("RGB"), dtype=np.uint8)
        except Exception as exc:
            self._log(f"Post-draw repair capture convert failed: {exc}", True)
            return None



    @staticmethod
    def _normalize_post_draw_repair_mode(value) -> str:
        mode = str(value or "").strip().lower()
        return "aggressive" if mode == "aggressive" else "conservative"






    def _compute_cell_sample_map_from_rgb(self, image_rgb):
        if self.cluster_map is None:
            return None
        try:
            arr = np.asarray(image_rgb, dtype=np.float32)
        except Exception:
            return None
        if arr.ndim != 3 or arr.shape[2] < 3:
            return None
        arr = arr[:, :, :3]
        h_px, w_px = arr.shape[:2]
        if h_px <= 0 or w_px <= 0:
            return None
        h_map, w_map = self.cluster_map.shape[:2]
        brush = max(1, int(getattr(self, "brush_size", 1) or 1))
        sample_size = self._post_draw_repair_sample_box_size()
        row_starts, row_ends, row_sizes = self._post_draw_repair_axis_ranges(h_px, h_map, brush, sample_size)
        col_starts, col_ends, col_sizes = self._post_draw_repair_axis_ranges(w_px, w_map, brush, sample_size)
        if row_starts.size == 0 or col_starts.size == 0:
            return None
        integral = np.pad(arr.cumsum(axis=0).cumsum(axis=1), ((1, 0), (1, 0), (0, 0)), mode="constant")
        sums = (
            integral[row_ends[:, None], col_ends[None, :]]
            - integral[row_starts[:, None], col_ends[None, :]]
            - integral[row_ends[:, None], col_starts[None, :]]
            + integral[row_starts[:, None], col_starts[None, :]]
        )
        areas = row_sizes[:, None].astype(np.float32) * col_sizes[None, :].astype(np.float32)
        areas = np.maximum(1.0, areas)
        return sums / areas[:, :, None]

    def _expected_drawn_cell_mask(self):
        if self.cluster_map is None:
            return None
        try:
            bg_id = int(getattr(self, "_background_cluster_id", -1))
        except Exception:
            bg_id = -1
        target_mask = self.cluster_map != bg_id
        small_mask = getattr(self, "small_components_preview_mask", None)
        if isinstance(small_mask, np.ndarray) and small_mask.shape == target_mask.shape:
            try:
                target_mask = target_mask & (~small_mask.astype(bool, copy=False))
            except Exception:
                target_mask = target_mask & (~small_mask.astype(bool))
        return target_mask

    def _analyze_post_draw_repair_samples(self, baseline_samples, final_samples, expected_samples, target_mask):
        if baseline_samples is None or final_samples is None or expected_samples is None or target_mask is None:
            return None
        try:
            baseline = np.asarray(baseline_samples, dtype=np.float32)
            final = np.asarray(final_samples, dtype=np.float32)
            expected = np.asarray(expected_samples, dtype=np.float32)
            mask = np.asarray(target_mask, dtype=bool)
        except Exception:
            return None
        if baseline.shape != final.shape or baseline.shape != expected.shape:
            return None
        if baseline.ndim != 3 or baseline.shape[2] < 3 or baseline.shape[:2] != mask.shape:
            return None
        baseline = baseline[:, :, :3]
        final = final[:, :, :3]
        expected = expected[:, :, :3]
        thresholds = self._post_draw_repair_thresholds()
        expected_delta = np.abs(expected - baseline)
        expected_euclid = np.sqrt(np.sum(expected_delta * expected_delta, axis=2, dtype=np.float32))
        expected_max = np.max(expected_delta, axis=2)
        verifiable = (
            mask
            & (expected_euclid >= thresholds["min_expected_euclid_delta"])
            & (expected_max >= thresholds["min_expected_max_channel_delta"])
        )
        final_delta = np.abs(final - baseline)
        final_euclid = np.sqrt(np.sum(final_delta * final_delta, axis=2, dtype=np.float32))
        final_max = np.max(final_delta, axis=2)
        final_to_expected_delta = np.abs(final - expected)
        final_to_expected_euclid = np.sqrt(np.sum(final_to_expected_delta * final_to_expected_delta, axis=2, dtype=np.float32))
        final_to_expected_max = np.max(final_to_expected_delta, axis=2)
        # Cells skipped because the canvas already matched: any later departure
        # from the target is damage (a neighbour's spill), not an unverifiable cell.
        matched = getattr(self, "_canvas_matched_mask", None)
        spoiled = np.zeros(mask.shape, dtype=bool)
        if isinstance(matched, np.ndarray) and matched.shape == mask.shape:
            matched = mask & matched & (~verifiable)
            spoiled = (
                matched
                & (final_to_expected_euclid >= thresholds["color_mismatch_euclid_delta"])
                & (final_to_expected_max >= thresholds["color_mismatch_max_channel_delta"])
            )
        else:
            matched = spoiled
        unverifiable = mask & (~verifiable) & (~matched)
        relative_bg_like = (
            verifiable
            & (final_euclid <= (expected_euclid * thresholds["bg_relative_euclid_ratio"]))
            & (final_to_expected_euclid >= (final_euclid * thresholds["bg_expected_distance_ratio"]))
            & (
                final_max
                <= np.maximum(
                    thresholds["bg_relative_max_channel_min"],
                    expected_max * thresholds["bg_relative_max_channel_ratio"],
                )
            )
        )
        hole_mask = (
            verifiable
            & (
                (
                    (final_euclid <= thresholds["same_bg_euclid_delta"])
                    & (final_max <= thresholds["same_bg_max_channel_delta"])
                )
                | relative_bg_like
            )
        )
        color_mismatch_mask = np.zeros(mask.shape, dtype=bool)
        if self._post_draw_repair_detects_color_mismatch():
            color_mismatch_mask = (
                verifiable
                & (
                    final_to_expected_euclid
                    >= np.maximum(
                        thresholds["color_mismatch_euclid_delta"],
                        expected_euclid * thresholds["color_mismatch_expected_ratio"],
                    )
                )
                & (
                    final_to_expected_max
                    >= np.maximum(
                        thresholds["color_mismatch_max_channel_delta"],
                        expected_max * thresholds["color_mismatch_expected_max_ratio"],
                    )
                )
            )
            hole_mask = hole_mask | color_mismatch_mask
        hole_mask = hole_mask | spoiled
        return {
            "target_mask": mask,
            "verifiable_mask": verifiable | matched,
            "unverifiable_mask": unverifiable,
            "hole_mask": hole_mask,
            "color_mismatch_mask": color_mismatch_mask,
            "target_cells": int(np.count_nonzero(mask)),
            "verifiable_cells": int(np.count_nonzero(verifiable | matched)),
            "unverifiable_cells": int(np.count_nonzero(unverifiable)),
            "detected_cells": int(np.count_nonzero(hole_mask)),
            "color_mismatch_cells": int(np.count_nonzero(color_mismatch_mask)),
        }

    def _compute_partial_post_draw_repair_holes(self, baseline_rgb, final_rgb, expected_rgb, target_mask):
        if self.cluster_map is None or target_mask is None:
            return None
        try:
            baseline = np.asarray(baseline_rgb, dtype=np.float32)
            final = np.asarray(final_rgb, dtype=np.float32)
            expected = np.asarray(expected_rgb, dtype=np.float32)
            mask = np.asarray(target_mask, dtype=bool)
        except Exception:
            return None
        if baseline.shape != final.shape or baseline.shape != expected.shape:
            return None
        if baseline.ndim != 3 or baseline.shape[2] < 3:
            return None
        if mask.shape != self.cluster_map.shape:
            return None

        baseline = baseline[:, :, :3]
        final = final[:, :, :3]
        expected = expected[:, :, :3]
        h_px, w_px = baseline.shape[:2]
        h_map, w_map = mask.shape[:2]
        if h_px <= 0 or w_px <= 0 or h_map <= 0 or w_map <= 0:
            return None

        brush = max(1, int(getattr(self, "brush_size", 1) or 1))
        thresholds = self._post_draw_repair_thresholds()
        row_starts = np.arange(h_map, dtype=np.int32) * brush
        row_ends = np.minimum(row_starts + brush, h_px).astype(np.int32, copy=False)
        col_starts = np.arange(w_map, dtype=np.int32) * brush
        col_ends = np.minimum(col_starts + brush, w_px).astype(np.int32, copy=False)

        expected_delta = np.abs(expected - baseline)
        expected_euclid = np.sqrt(np.sum(expected_delta * expected_delta, axis=2, dtype=np.float32))
        expected_max = np.max(expected_delta, axis=2)

        final_delta = np.abs(final - baseline)
        final_euclid = np.sqrt(np.sum(final_delta * final_delta, axis=2, dtype=np.float32))
        final_to_expected_delta = np.abs(final - expected)
        final_to_expected_euclid = np.sqrt(
            np.sum(final_to_expected_delta * final_to_expected_delta, axis=2, dtype=np.float32)
        )
        final_to_expected_max = np.max(final_to_expected_delta, axis=2)

        pixel_verifiable = (
            (expected_euclid >= thresholds["partial_min_expected_euclid_delta"])
            & (expected_max >= thresholds["partial_min_expected_max_channel_delta"])
        )
        pixel_missing = (
            pixel_verifiable
            & (
                final_to_expected_euclid
                >= np.maximum(8.0, expected_euclid * thresholds["partial_missing_expected_ratio"])
            )
            & (
                final_euclid
                <= np.maximum(
                    thresholds["same_bg_euclid_delta"] * 1.5,
                    expected_euclid * thresholds["partial_final_bg_ratio"],
                )
            )
            & (final_euclid <= final_to_expected_euclid + 4.0)
        )
        pixel_detected = pixel_missing
        if self._post_draw_repair_detects_color_mismatch():
            pixel_color_mismatch = (
                pixel_verifiable
                & (
                    final_to_expected_euclid
                    >= np.maximum(
                        thresholds["partial_color_mismatch_euclid_delta"],
                        expected_euclid * thresholds["partial_color_mismatch_expected_ratio"],
                    )
                )
                & (
                    final_to_expected_max
                    >= np.maximum(
                        thresholds["partial_color_mismatch_max_channel_delta"],
                        expected_max * thresholds["partial_color_mismatch_expected_max_ratio"],
                    )
                )
            )
            pixel_detected = pixel_detected | pixel_color_mismatch

        verifiable_integral = np.pad(
            pixel_verifiable.astype(np.uint32).cumsum(axis=0).cumsum(axis=1),
            ((1, 0), (1, 0)),
            mode="constant",
        )
        detected_integral = np.pad(
            pixel_detected.astype(np.uint32).cumsum(axis=0).cumsum(axis=1),
            ((1, 0), (1, 0)),
            mode="constant",
        )

        verifiable_counts = (
            verifiable_integral[row_ends[:, None], col_ends[None, :]]
            - verifiable_integral[row_starts[:, None], col_ends[None, :]]
            - verifiable_integral[row_ends[:, None], col_starts[None, :]]
            + verifiable_integral[row_starts[:, None], col_starts[None, :]]
        ).astype(np.int32, copy=False)
        detected_counts = (
            detected_integral[row_ends[:, None], col_ends[None, :]]
            - detected_integral[row_starts[:, None], col_ends[None, :]]
            - detected_integral[row_ends[:, None], col_starts[None, :]]
            + detected_integral[row_starts[:, None], col_starts[None, :]]
        ).astype(np.int32, copy=False)

        with np.errstate(divide="ignore", invalid="ignore"):
            detected_ratio = np.divide(
                detected_counts.astype(np.float32),
                np.maximum(1, verifiable_counts).astype(np.float32),
            )

        min_missing_pixels = 1 if brush <= 2 else max(2, brush // 2)
        ratio_threshold = 0.20 if brush <= 2 else (0.14 if brush <= 4 else 0.08)
        ratio_threshold = max(0.04, min(0.35, ratio_threshold * thresholds["partial_ratio_threshold_scale"]))
        if self._post_draw_repair_detects_color_mismatch():
            ratio_threshold = min(ratio_threshold, thresholds["partial_color_ratio_threshold"])
        line_threshold = max(1, brush - 1)

        partial_holes = (
            mask
            & (verifiable_counts > 0)
            & (detected_counts >= min_missing_pixels)
            & ((detected_ratio >= ratio_threshold) | (detected_counts >= line_threshold))
        )
        return partial_holes.astype(bool, copy=False)


    def _build_post_draw_repair_regions(self, hole_mask: np.ndarray):
        if self.draw_region is None or self.cluster_map is None:
            return [], None
        try:
            mask_u8 = np.asarray(hole_mask, dtype=np.uint8) * 255
        except Exception:
            return [], None
        if mask_u8.size == 0 or not np.any(mask_u8):
            return [], None
        try:
            from PIL import Image, ImageDraw  # type: ignore

            dx, dy, region_w_px, region_h_px = map(int, self.draw_region)
            region_w_px = max(1, region_w_px)
            region_h_px = max(1, region_h_px)
            overlay_img = Image.new("RGBA", (region_w_px, region_h_px), (0, 0, 0, 0))
            overlay_draw = ImageDraw.Draw(overlay_img, "RGBA")
        except Exception:
            return [], None
        try:
            num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask_u8, connectivity=4)
        except Exception:
            return [], overlay_img
        brush = max(1, int(getattr(self, "brush_size", 1) or 1))
        regions = []
        for label_idx in range(1, num_labels):
            coords = np.argwhere(labels == label_idx)
            if coords.size == 0:
                continue
            area_cells = int(stats[label_idx, cv2.CC_STAT_AREA])
            bbox_left = int(stats[label_idx, cv2.CC_STAT_LEFT])
            bbox_top = int(stats[label_idx, cv2.CC_STAT_TOP])
            bbox_width = int(stats[label_idx, cv2.CC_STAT_WIDTH])
            bbox_height = int(stats[label_idx, cv2.CC_STAT_HEIGHT])
            px_left = bbox_left * brush
            px_top = bbox_top * brush
            px_right = min(px_left + bbox_width * brush, region_w_px)
            px_bottom = min(px_top + bbox_height * brush, region_h_px)
            cluster_ids = []
            try:
                cluster_ids = np.unique(self.cluster_map[labels == label_idx]).tolist()
            except Exception:
                cluster_ids = []
            for sr, sc in coords:
                cell_left = int(sc) * brush
                cell_top = int(sr) * brush
                cell_right = min(cell_left + brush, region_w_px)
                cell_bottom = min(cell_top + brush, region_h_px)
                overlay_draw.rectangle(
                    [
                        cell_left,
                        cell_top,
                        max(cell_right - 1, cell_left),
                        max(cell_bottom - 1, cell_top),
                    ],
                    fill=(255, 64, 64, 110),
                )
            overlay_draw.rectangle(
                [
                    px_left,
                    px_top,
                    max(px_right - 1, px_left),
                    max(px_bottom - 1, px_top),
                ],
                outline=(255, 64, 64, 220),
                width=max(1, brush // 2),
            )
            regions.append(
                {
                    "area": area_cells,
                    "bbox_cells": (bbox_left, bbox_top, bbox_width, bbox_height),
                    "rect_px": (dx + px_left, dy + px_top, dx + px_right, dy + px_bottom),
                    "cluster_ids": [int(val) for val in cluster_ids if int(val) >= 0],
                    "label": int(label_idx),
                }
            )
        return regions, overlay_img

    def _notify_post_draw_repair(self, payload):
        callback = getattr(self, "post_draw_repair_callback", None)
        if not callable(callback) or not isinstance(payload, dict):
            return
        try:
            callback(payload)
        except Exception:
            log.debug('ignored exception in callback(payload)', exc_info=True)

    @capture_start
    def start_manual_mix_background_capture(self):
        if getattr(self, "drawing_enabled", False):
            err_msg = tr("status_error_cannot_define_manual_palette_drawing")
            self._log(err_msg, True)
            if self.status_callback:
                try:
                    self.status_callback(err_msg)
                except Exception:
                    log.debug('ignored exception in self.status_callback(err_msg)', exc_info=True)
            return
        if mouse is None:
            self._log("Manual mix background capture unavailable: pynput.mouse missing.", True)
            if self.status_callback:
                try:
                    self.status_callback(tr("status_manual_mix_canvas_unavailable"))
                except Exception:
                    self.status_callback("Manual mix background capture unavailable.")
            return
        if getattr(self, "is_capturing_manual_mix_background", False):
            self._log("Manual mix background capture already active.", True)
            return
        self._begin_capture_session("цвет фона холста")
        session = self._capture_session
        self.is_capturing_manual_mix_background = True

        try:
            import time as _time
            self.ignore_clicks_until = _time.time() + 0.25
        except Exception:
            self.ignore_clicks_until = 0.0

        self._log("Starting manual mix background capture.")
        if self.status_callback:
            try:
                self.status_callback(tr("status_manual_mix_canvas_start"))
            except Exception:
                self.status_callback("Click on the canvas background to sample its colour.")

        def on_click_manual_mix_background(x, y, button, pressed):
            if not pressed or button != mouse.Button.left or not self.is_capturing_manual_mix_background:
                return
            try:
                import time as _time
                if self.ignore_clicks_until and _time.time() < self.ignore_clicks_until:
                    return
            except Exception:
                log.debug('ignored exception in import time as _time', exc_info=True)
            rgb = self._average_screen_rgb_block(int(x), int(y), half_size=7)
            def commit():
                if rgb is not None:
                    self.set_manual_mix_canvas_rgb(rgb)
                    self._finish_manual_mix_background_capture(success=True)
                else:
                    self.status_callback(tr("status_manual_mix_canvas_failed"))
                    self._finish_manual_mix_background_capture(success=False)
            session.publish(commit)

        try:
            self.mouse_listener_manual_mix_background = self._capture_listener(mouse.Listener, on_click=on_click_manual_mix_background)
            self.mouse_listener_manual_mix_background.start()
        except Exception as exc:
            self._log(f"Manual mix background capture mouse error: {exc}", True)
            if self.status_callback:
                try:
                    self.status_callback(tr("status_manual_mix_canvas_unavailable"))
                except Exception:
                    self.status_callback(f"Manual mix background capture error: {exc}")
            self.is_capturing_manual_mix_background = False
            self.mouse_listener_manual_mix_background = None
    def _srgb_to_linear_rgb(self, rgb_value) -> np.ndarray:
        arr = np.asarray(rgb_value, dtype=np.float32)
        if arr.ndim == 0:
            arr = np.repeat(arr, 3)
        arr = arr.reshape(-1, 3)
        srgb = np.clip(arr / 255.0, 0.0, 1.0)
        threshold = 0.04045
        linear = np.where(
            srgb <= threshold,
            srgb / 12.92,
            ((srgb + 0.055) / 1.055) ** 2.4,
        )
        if linear.shape[0] == 1:
            return linear[0]
        return linear

    def _linear_to_srgb_rgb(self, linear_value) -> tuple[int, int, int]:
        arr = np.asarray(linear_value, dtype=np.float32)
        if arr.ndim == 0:
            arr = np.repeat(arr, 3)
        srgb = np.clip(arr, 0.0, 1.0)
        threshold = 0.0031308
        srgb = np.where(
            srgb <= threshold,
            srgb * 12.92,
            1.055 * (srgb ** (1.0 / 2.4)) - 0.055,
        )
        srgb = np.clip(np.round(srgb * 255.0), 0, 255).astype(int)
        if srgb.ndim > 1:
            srgb = srgb[0]
        return tuple(int(v) for v in srgb.tolist())

    def _blend_linear_rgb(self, dst_linear: np.ndarray, src_linear: np.ndarray, alpha: float) -> np.ndarray:
        alpha_clamped = float(max(0.0, min(1.0, alpha)))
        return np.clip(src_linear * alpha_clamped + dst_linear * (1.0 - alpha_clamped), 0.0, 1.0)

    def _project_simplex(self, weights: np.ndarray, z: float = 1.0) -> np.ndarray:
        """Project weights onto the simplex {w >= 0, sum(w) = z}."""
        w = np.asarray(weights, dtype=np.float32).reshape(-1)
        if w.size == 0:
            return w
        if np.all(w >= -1e-8):
            total = float(np.sum(w))
            if total <= z + 1e-8:
                return np.clip(w, 0.0, None)
        u = np.sort(w)[::-1]
        cssv = np.cumsum(u)
        rho = np.nonzero(u * np.arange(1, u.size + 1) > (cssv - z))[0]
        if rho.size == 0:
            theta = cssv[-1] / float(u.size)
        else:
            rho_idx = int(rho[-1])
            theta = (cssv[rho_idx] - z) / float(rho_idx + 1)
        projected = np.maximum(w - theta, 0.0)
        return projected

    # ---- custom freeform "draw-zone" mask (painted in the Alt+F2 stencil) ----
    def set_region_mask(self, mask) -> bool:
        """Restrict drawing to a painted freeform shape.

        ``mask`` is a 2D array (any resolution, draw-region orientation) where a
        truthy cell = "draw here", or ``None`` to clear the restriction. Cells
        OUTSIDE the shape are treated as background and never drawn. The mask is
        stored raw and resampled to the cluster grid on demand, so it survives
        resolution / area changes. Returns True if the stored mask changed."""
        prev = self._region_mask_src
        if mask is None:
            self._region_mask_src = None
        else:
            try:
                arr = np.asarray(mask)
                if arr.ndim == 3:
                    arr = arr[..., -1]          # RGBA/LA -> use the alpha channel
                if arr.ndim != 2 or arr.size == 0:
                    return False
                self._region_mask_src = (arr > 0)
            except Exception:
                log.debug("set_region_mask: bad input", exc_info=True)
                return False
        # Did it actually change?
        if prev is None and self._region_mask_src is None:
            changed = False
        elif prev is not None and self._region_mask_src is not None:
            try:
                changed = (prev.shape != self._region_mask_src.shape) or bool(
                    np.any(prev != self._region_mask_src)
                )
            except Exception:
                changed = True
        else:
            changed = True
        # Re-derive drawn_mask so a live preview/toggle takes effect without a full
        # re-prepare — but never mid-drawing (it would wipe the progress mask).
        if changed and self.cluster_map is not None and not self.drawing_enabled:
            try:
                self._ensure_drawn_mask_shape(force=True)
                if self.pixel_update_callback:
                    self.pixel_update_callback()
            except Exception:
                log.debug("set_region_mask: re-derive failed", exc_info=True)
        return changed

    def get_region_mask(self):
        return self._region_mask_src

    def _region_excluded_grid(self):
        """Boolean array (cluster_map shape) — True where a cell falls OUTSIDE the
        custom region mask. None when no mask is set / no cluster grid yet."""
        src = self._region_mask_src
        if src is None or self.cluster_map is None:
            return None
        try:
            h, w = self.cluster_map.shape
            m = np.asarray(src)
            if m.shape != (h, w):
                m = cv2.resize(
                    m.astype(np.uint8), (w, h), interpolation=cv2.INTER_NEAREST
                ).astype(bool)
            else:
                m = m.astype(bool)
            return ~m
        except Exception:
            log.debug("_region_excluded_grid failed", exc_info=True)
            return None

    def _with_region_exclusion(self, base_mask):
        """OR the out-of-region cells into a freshly-built background drawn-mask so
        the drawing loop skips everything outside the painted shape."""
        excl = self._region_excluded_grid()
        if excl is not None and getattr(excl, "shape", None) == getattr(base_mask, "shape", None):
            try:
                return base_mask | excl
            except Exception:
                log.debug("_with_region_exclusion OR failed", exc_info=True)
        return base_mask

    def _ensure_drawn_mask_shape(self, force: bool = False):
        if self.cluster_map is None:
            return
        needs_reset = bool(force)
        if self.drawn_mask is None:
            needs_reset = True
        else:
            try:
                if self.drawn_mask.shape != self.cluster_map.shape:
                    needs_reset = True
            except Exception:
                needs_reset = True
        if needs_reset:
            try:
                bg_id = getattr(self, "_background_cluster_id", -1)
                self.drawn_mask = self._with_region_exclusion(self.cluster_map == bg_id)
            except Exception:
                try:
                    self.drawn_mask = np.zeros_like(self.cluster_map, dtype=bool)
                except Exception:
                    self.drawn_mask = None

    def _reset_draw_progress(self, notify: bool = True) -> None:
        """Reset per-drawing progress so a fresh run can start immediately."""
        self.stop_flag = False
        self.drawing_enabled = False
        self.drawing_start_time = None
        self.pause_start_time = None
        self.paused_time_accumulated = 0.0
        self.current_color_index = 0
        # The nearest-region anchor must not leak from the PREVIOUS picture into
        # a fresh run (it would pull the first region toward a stale position).
        self._last_pen_cell = None
        self._route_exit_cell = None
        self._ensure_drawn_mask_shape(force=True)
        try:
            bg_id = getattr(self, "_background_cluster_id", -1)
            if self.cluster_map is not None:
                self.drawn_mask = self._with_region_exclusion(self.cluster_map == bg_id)
        except Exception:
            try:
                self.drawn_mask = np.zeros_like(self.cluster_map, dtype=bool)
            except Exception:
                self.drawn_mask = None
        if notify and self.pixel_update_callback:
            try:
                self.pixel_update_callback()
            except Exception:
                log.debug('ignored exception in self.pixel_update_callback()', exc_info=True)
        if self.status_callback:
            try:
                self.status_callback(tr("status_ready"))
            except Exception:
                try:
                    self.status_callback("Ready.")
                except Exception:
                    log.debug("ignored exception in self.status_callback('Ready.')", exc_info=True)
    def _sanitize_manual_palette_entry(self, entry, idx):
        if not isinstance(entry, dict):
            return None
        try:
            x_val = int(entry.get("x", entry.get("px", 0)))
            y_val = int(entry.get("y", entry.get("py", 0)))
        except Exception:
            return None

        rgb_val = entry.get("rgb")
        hex_val = entry.get("hex")
        rgb_tuple = None

        if isinstance(rgb_val, (list, tuple)) and len(rgb_val) >= 3:
            try:
                rgb_tuple = tuple(int(max(0, min(255, round(float(rgb_val[i]))))) for i in range(3))
            except Exception:
                rgb_tuple = None

        if rgb_tuple is None and isinstance(hex_val, str):
            hex_clean = hex_val.strip().lstrip("#")
            if len(hex_clean) == 6 and all(ch in "0123456789ABCDEFabcdef" for ch in hex_clean):
                try:
                    rgb_tuple = tuple(int(hex_clean[i:i + 2], 16) for i in (0, 2, 4))
                except Exception:
                    rgb_tuple = None

        if rgb_tuple is None:
            return None

        hex_str = f"#{rgb_tuple[0]:02X}{rgb_tuple[1]:02X}{rgb_tuple[2]:02X}"
        lab_val = self._srgb_to_lab_single(rgb_tuple)
        okl_val = self._srgb_to_oklab_single(rgb_tuple)
        return {
            "index": idx,
            "x": x_val,
            "y": y_val,
            "rgb": rgb_tuple,
            "hex": hex_str,
            "lab": lab_val,
            "okl": okl_val,
        }

    def _get_manual_palette_cache(self):
        cache = getattr(self, "_manual_palette_cache", None)
        if cache is not None:
            return cache

        raw_palette = getattr(self, "manual_palette_coords", None) or []
        sanitized_entries = []
        by_index = {}
        lab_list = []
        rgb_list = []
        idx_list = []
        for idx, entry in enumerate(raw_palette):
            sanitized = self._sanitize_manual_palette_entry(entry, idx)
            if sanitized is None:
                continue
            sanitized_entries.append(sanitized)
            by_index[sanitized["index"]] = sanitized
            lab_list.append(sanitized["lab"])
            rgb_list.append(sanitized["rgb"])
            idx_list.append(sanitized["index"])

        if lab_list:
            lab_array = np.stack(lab_list).astype(np.float32)
        else:
            lab_array = np.zeros((0, 3), dtype=np.float32)

        if rgb_list:
            rgb_array = np.array(rgb_list, dtype=np.float32)
        else:
            rgb_array = np.zeros((0, 3), dtype=np.float32)

        if idx_list:
            indices_array = np.array(idx_list, dtype=np.int32)
        else:
            indices_array = np.zeros((0,), dtype=np.int32)

        cache = {
            "entries": sanitized_entries,
            "by_index": by_index,
            "lab_array": lab_array,
            "rgb_array": rgb_array,
            "indices": indices_array,
            "raw_length": len(raw_palette) if isinstance(raw_palette, list) else len(idx_list),
        }
        self._manual_palette_cache = cache
        return cache

    def _sort_palette_entries(self, entries):
        """Order the COLORS of the palette (NOT regions — those are ordered by
        `area_sequence` at draw time, per-color). Scheme = "lightness + saturation":
          primary   : perceptual lightness, grouped into bands (light_to_dark or
                      dark_to_light per `tone_sequence`);
          secondary : within a lightness band, more saturated (vivid) colors first,
                      then muted / neutral (gray, white, black);
          tertiary  : exact lightness (smooth inside a band), then id for stability.
        Area/region size no longer affects the COLOR order (that was the old, very
        confusing rank-sum blend)."""
        if not entries:
            return entries

        tone_sequence = getattr(self, "tone_sequence", "light_to_dark") or "light_to_dark"
        light_first = (tone_sequence != "dark_to_light")
        # «Мелкие детали последними» (ORDER-DETAILS-001): flat colours first, thin
        # ones (outlines, eyes, text) last, so a brush wider than a cell does not
        # paint over them. Lightness stays the order among equally detailed colours.
        detail = self._palette_detail_scores(entries) if tone_sequence == "details_last" else {}

        # Semantic plane as the PRIMARY key (phase 5 / WS8-2): backdrop colors
        # first, then objects, then details; INSIDE a plane the tone order below
        # is untouched. Any failure or a degenerate single-plane picture falls
        # back to the legacy order (planes=None -> constant key).
        planes = None
        semantic_mode = self._normalize_semantic_order_mode(getattr(self, "semantic_order_mode", "off"))
        if semantic_mode != "off":
            try:
                planes = self._semantic_cluster_planes()
            except Exception:
                log.debug("semantic planes failed; keeping the tone order", exc_info=True)
                planes = None

        def _safe_float(value, default=0.0):
            try:
                return float(value)
            except Exception:
                return default

        lums = [_safe_float(entry.get("luminance"), 0.0) for entry in entries]
        # Normalize by the metric's OWN black->white range (not the palette's
        # min..max) so a "band" is an ABSOLUTE slice of lightness. Otherwise a
        # palette whose colors are all similar-lightness would get its tiny
        # differences blown up into separate bands and saturation would never
        # group them. Works for any lightness metric (Rec.601 0..255, OKLab 0..1).
        try:
            l_black = float(self._palette_lightness((0, 0, 0)))
            l_white = float(self._palette_lightness((255, 255, 255)))
        except Exception:
            l_black = min(lums) if lums else 0.0
            l_white = max(lums) if lums else 1.0
        span = max(1e-9, l_white - l_black)
        band_width = 0.12  # colors within ~12% of the black->white range share a band

        meta = []
        for idx, entry in enumerate(entries):
            norm = (lums[idx] - l_black) / span            # 0 = darkest .. 1 = lightest
            band = int(round(norm / band_width))
            sat = self._palette_saturation(entry.get("rgb"))
            band_key = -band if light_first else band       # lighter (or darker) band first
            lum_key = -lums[idx] if light_first else lums[idx]
            plane_key = (self._semantic_plane_rank(planes.get(entry.get("id"), 1), semantic_mode)
                         if planes else 0)
            detail_key = round(detail.get(entry.get("id"), 0.0), 2)
            meta.append((plane_key, detail_key, band_key, -sat, lum_key, entry.get("id", 0), entry))

        meta.sort(key=lambda m: m[:6])
        return [m[-1] for m in meta]

    def _palette_detail_scores(self, entries):
        """Share of each colour's cells that touch another colour: ~0 for big flat
        areas, ~1 for lines and specks. {} when the cluster map is not ready."""
        cluster_map = getattr(self, "cluster_map", None)
        if not isinstance(cluster_map, np.ndarray) or cluster_map.ndim != 2 or cluster_map.size == 0:
            return {}
        from scipy import ndimage
        scores = {}
        for entry in entries:
            cid = entry.get("id")
            try:
                mask = cluster_map == int(cid)
            except (TypeError, ValueError):
                continue
            cells = int(mask.sum())
            if cells == 0:
                continue
            inner = ndimage.binary_erosion(mask, structure=np.ones((3, 3), bool), border_value=1)
            scores[cid] = float((mask & ~inner).sum()) / cells
        return scores

    def _apply_palette_sorting(self):
        if getattr(self, 'drawing_enabled', False):
            return
        if not self.color_palette:
            return

        enriched = []
        for hex_color, cluster_id, count, rgb in self.color_palette:
            luminance = None
            if rgb is not None:
                try:
                    luminance = self._palette_lightness(rgb)
                except Exception:
                    luminance = None
            enriched.append({
                "hex": hex_color,
                "id": cluster_id,
                "count": count,
                "rgb": rgb,
                "luminance": luminance,
            })

        sorted_entries = self._sort_palette_entries(enriched)
        self.color_palette = [
            (entry["hex"], entry["id"], entry["count"], entry["rgb"])
            for entry in sorted_entries
        ]
        self.current_color_index = 0

    def set_tone_sequence(self, sequence):
        allowed = {"light_to_dark", "dark_to_light", "details_last"}
        if sequence not in allowed:
            return False
        if getattr(self, "tone_sequence", None) == sequence:
            return False
        if getattr(self, 'drawing_enabled', False):
            return False
        self.tone_sequence = sequence
        self._apply_palette_sorting()
        return True

    def set_area_sequence(self, sequence):
        allowed = {"large_to_small", "small_to_large", "nearest"}
        if sequence not in allowed:
            return False
        if getattr(self, "area_sequence", None) == sequence:
            return False
        if getattr(self, 'drawing_enabled', False):
            return False
        self.area_sequence = sequence
        self._apply_palette_sorting()
        return True

    def _run_perceptual_prep_pipeline(
        self,
        img_small_rgba_map: Image.Image,
        np_img_small_rgb_map: np.ndarray,
        background_cluster_id: int,
    ) -> bool:
        color_space = self._normalize_prep_color_space(getattr(self, "prep_color_space", "legacy_rgb"))
        if color_space not in {"cielab", "oklab"}:
            return False

        quantization_mode = self._normalize_prep_quantization_mode(
            getattr(self, "prep_quantization_mode", "kmeans")
        )
        cleanup_mode = self._normalize_prep_cleanup_mode(getattr(self, "prep_cleanup_mode", "off"))
        work_rgba = np.array(img_small_rgba_map)
        work_rgb_map = np.asarray(np_img_small_rgb_map)
        work_background_mask = np.asarray(self.background_mask).astype(bool, copy=False)
        work_h, work_w = work_rgb_map.shape[:2]
        total_cells = int(work_h * work_w)
        dither_on = bool(getattr(self, "prep_dither_enabled", False))
        reduce_perceptual_grid = False
        predict_cap = int(max(10000, getattr(self, "kmeans_predict_max_pixels", 900000)))
        if total_cells > predict_cap and dither_on:
            # Dithering depends on per-cell high-frequency detail; reducing the grid then
            # NEAREST-upscaling would block-replicate cells and destroy the dither pattern
            # (the exact banding it exists to hide). Keep full resolution — Floyd-Steinberg
            # and k-means predict are linear, so this stays affordable.
            self._log("Perceptual large-area reduction skipped: dithering needs full resolution.")
        if total_cells > predict_cap and not dither_on:
            try:
                scale = math.sqrt(total_cells / float(predict_cap))
                reduced_w = max(1, int(round(work_w / scale)))
                reduced_h = max(1, int(round(work_h / scale)))
                reduced_rgba = cv2.resize(
                    work_rgba,
                    (reduced_w, reduced_h),
                    interpolation=cv2.INTER_AREA,
                )
                reduced_rgb = cv2.resize(
                    work_rgb_map,
                    (reduced_w, reduced_h),
                    interpolation=cv2.INTER_AREA,
                )
                reduced_bg = cv2.resize(
                    work_background_mask.astype(np.uint8),
                    (reduced_w, reduced_h),
                    interpolation=cv2.INTER_NEAREST,
                ).astype(bool)
                reduce_perceptual_grid = True
                work_rgba = reduced_rgba
                work_rgb_map = reduced_rgb
                work_background_mask = reduced_bg
                self._log(
                    f"Perceptual large area optimization: clustering grid {work_w}x{work_h} -> {reduced_w}x{reduced_h}"
                )
            except Exception as reduce_exc:
                self._log(f"Perceptual large area optimization failed: {reduce_exc}", True)

        # Use the already reduced brush-grid image for perceptual preview/builds.
        # Large areas are optionally clustered on a temporary reduced grid and then
        # expanded back to the active brush map to avoid very expensive rebuilds.
        pipeline = build_perceptual_cluster_map(
            work_rgba,
            brush_size=1,
            k_clusters=max(1, int(getattr(self, "k_clusters", 1) or 1)),
            color_space=color_space,
            quantization_mode=quantization_mode,
            fit_sample_cap=int(max(5000, getattr(self, "kmeans_fit_max_samples", 220000))),
            alpha_threshold=int(getattr(self, "background_alpha_threshold", getattr(self, "alpha_threshold", 10))),
            background_mask=work_background_mask,
            background_cluster_id=background_cluster_id,
            dither=dither_on,
            # color_merge_threshold is an absolute distance in the active cluster space.
            # OKLab L is ~0..1 but CIELAB L is 0..100, so the same number is ~100x weaker in
            # CIELAB (a near no-op). Scale it so 0.05 means a comparable perceptual gap in both.
            merge_threshold=float(getattr(self, "color_merge_threshold", 0.0) or 0.0) * (100.0 if color_space == "cielab" else 1.0),
            preserve_accents=bool(getattr(self, "prep_preserve_accents", True)),
        )
        cluster_map = np.asarray(pipeline.get("cluster_map"), dtype=int)
        if cluster_map.ndim != 2:
            raise ValueError("Perceptual prep pipeline returned invalid cluster_map")

        cleanup_threshold = max(0, int(getattr(self, "fast_min_region_area", 0)))
        if dither_on and (cleanup_threshold > 0 or cleanup_mode != "off"):
            # Dithering is a deliberate field of 1-cell speckles; min-region merge and
            # cleanup would treat each speckle as a 'small region' and erase exactly the
            # texture the user asked for. Disable both while dithering is on.
            self._log("Perceptual cleanup / min-region merge skipped to preserve dithering.")
            cleanup_threshold = 0
            cleanup_mode = "off"
        merges_done = 0
        self.cluster_map = np.asarray(cluster_map, dtype=int)
        if cleanup_threshold > 0:
            pre_cleanup_palette = self._build_palette_from_cluster_map(
                work_rgb_map,
                background_cluster_id,
                use_perceptual_lightness=True,
            )
            center_map = {
                int(cluster_id): tuple(rgb)
                for _hex_color, cluster_id, _count, rgb in pre_cleanup_palette
                if rgb is not None
            }
            if center_map:
                try:
                    merges_done = self._merge_small_components_into_neighbors(center_map, background_cluster_id)
                    cluster_map = np.asarray(self.cluster_map, dtype=int)
                except Exception as merge_exc:
                    merges_done = 0
                    self._log(f"Perceptual merge-small failed: {merge_exc}", True)

        cleanup_meta = {"changed_cells": 0, "mode": cleanup_mode}
        cleanup_cell_cap = int(max(10000, getattr(self, "prep_cleanup_max_cells", 250000)))
        # The cleanup combo used to silently require the SEPARATE «min region
        # area» field to be > 1 — with it at 0/1 the selected mode did nothing
        # at all (live report: «переключаю Очистку — ничего не меняется»). The
        # combo is now self-sufficient: an explicit cleanup mode falls back to
        # its own default speck size when that field is unset.
        cleanup_min_area = cleanup_threshold if cleanup_threshold > 1 else 4
        if cleanup_mode != "off" and int(cluster_map.size) <= cleanup_cell_cap:
            cleanup_space = "oklab" if color_space == "oklab" else "lab"
            # Area-criterion guard for the stray-merge: a speck merges only into a
            # neighbour within this perceptual gap, so distinct-colour regions can
            # never cascade into one colour even at a large min_area. Scaled per
            # space (OKLab L ~0..1 vs CIELAB L 0..100 — ~100x).
            cleanup_merge_cap = 0.10 if cleanup_space == "oklab" else 12.0
            cluster_map, cleanup_meta = apply_cleanup_mode(
                cluster_map,
                work_rgb_map,
                mode=cleanup_mode,
                min_area=cleanup_min_area,
                background=background_cluster_id,
                space=cleanup_space,
                max_merge_distance=cleanup_merge_cap,
            )
        elif cleanup_mode != "off" and int(cluster_map.size) > cleanup_cell_cap:
            cleanup_meta = {"changed_cells": 0, "mode": f"{cleanup_mode}_skipped_large_map"}
            self._log(
                f"Perceptual cleanup skipped: cluster map too large ({int(cluster_map.size)} cells > {cleanup_cell_cap})."
            )

        if reduce_perceptual_grid:
            full_h, full_w = np_img_small_rgb_map.shape[:2]
            cluster_map = cv2.resize(
                np.asarray(cluster_map, dtype=np.int32),
                (full_w, full_h),
                interpolation=cv2.INTER_NEAREST,
            ).astype(int, copy=False)
            cluster_map[np.asarray(self.background_mask).astype(bool, copy=False)] = background_cluster_id

        self.cluster_map = np.asarray(cluster_map, dtype=int)
        self.background_mask = self.cluster_map == background_cluster_id
        self.color_palette = self._build_palette_from_cluster_map(
            np_img_small_rgb_map,
            background_cluster_id,
            use_perceptual_lightness=True,
        )

        filtered_rgb = pipeline.get("filtered_rgb")
        active_mask = pipeline.get("active_mask")
        active_pixels = int(np.count_nonzero(active_mask)) if isinstance(active_mask, np.ndarray) else 0
        palette_size = len(self.color_palette)
        self._log(
            "Perceptual prep pipeline: "
            f"space={color_space}, quantization={quantization_mode}, cleanup={cleanup_mode}, "
            f"active_pixels={active_pixels}, palette={palette_size}"
        )
        if isinstance(filtered_rgb, np.ndarray):
            self._log(f"Perceptual preprocess image size: {filtered_rgb.shape[1]}x{filtered_rgb.shape[0]}")
        if cleanup_mode != "off":
            # Always report the outcome — «changed 0 cells» is itself the answer
            # to "why don't I see a difference" (the image simply has no specks).
            self._log(
                f"Perceptual cleanup ({cleanup_meta.get('mode', cleanup_mode)}): "
                f"changed {int(cleanup_meta.get('changed_cells', 0) or 0)} cell(s), "
                f"min_area={cleanup_min_area}."
            )
        if merges_done:
            self._log(
                f"Perceptual merge-small merged {merges_done} region(s) into neighbors "
                f"(min area {cleanup_threshold})."
            )
        return True

    @staticmethod
    def _normalize_crop(crop) -> tuple:
        try:
            l, t, r, b = (float(v) for v in crop)
        except Exception:
            return (0.0, 0.0, 1.0, 1.0)
        l = min(max(0.0, l), 1.0); r = min(max(0.0, r), 1.0)
        t = min(max(0.0, t), 1.0); b = min(max(0.0, b), 1.0)
        if r < l:
            l, r = r, l
        if b < t:
            t, b = b, t
        if r - l < 1e-3:
            r = min(1.0, l + 1e-3)
        if b - t < 1e-3:
            b = min(1.0, t + 1e-3)
        return (l, t, r, b)

    def get_crop_norm(self) -> tuple:
        return self._normalize_crop(getattr(self, "crop_norm", (0.0, 0.0, 1.0, 1.0)))

    def set_crop_norm(self, crop, *, reprocess: bool = True) -> bool:
        """Set the source crop as normalized (left, top, right, bottom) in 0..1.
        (0,0,1,1) means no crop. Returns True if it changed."""
        new = self._normalize_crop(crop)
        old = self.get_crop_norm()
        self.crop_norm = new
        changed = (new != old)
        if changed and reprocess and self.IMAGE_PATH:
            self.prepare_image_and_palette()
        return changed

    def reset_crop(self, *, reprocess: bool = True) -> None:
        self.set_crop_norm((0.0, 0.0, 1.0, 1.0), reprocess=reprocess)

    def set_source_flip(self, *, horizontal=None, vertical=None, reprocess: bool = True) -> None:
        if horizontal is not None:
            self.flip_horizontal = bool(horizontal)
        if vertical is not None:
            self.flip_vertical = bool(vertical)
        if reprocess and self.IMAGE_PATH:
            self.prepare_image_and_palette()

    def _apply_source_transforms(self) -> None:
        """Crop/flip ``self.image_original_rgba`` in place before it is scaled to the
        draw area. With crop (0,0,1,1) and no flips this is a no-op."""
        img = self.image_original_rgba
        if img is None:
            return
        l, t, r, b = self.get_crop_norm()
        if (l, t, r, b) != (0.0, 0.0, 1.0, 1.0):
            try:
                W, H = img.size
                x0 = max(0, min(W - 1, int(round(l * W))))
                y0 = max(0, min(H - 1, int(round(t * H))))
                x1 = max(x0 + 1, min(W, int(round(r * W))))
                y1 = max(y0 + 1, min(H, int(round(b * H))))
                img = img.crop((x0, y0, x1, y1))
            except Exception as exc:
                self._log(f"Source crop failed ({exc}); using full image.", True)
                img = self.image_original_rgba
        try:
            if getattr(self, "flip_horizontal", False):
                img = img.transpose(Image.FLIP_LEFT_RIGHT)
            if getattr(self, "flip_vertical", False):
                img = img.transpose(Image.FLIP_TOP_BOTTOM)
        except Exception as exc:
            self._log(f"Source flip failed ({exc}).", True)
        self.image_original_rgba = img

    def prepare_image_and_palette(self):
        if self._quiet(): return False

        if self.drawing_enabled:
            self._log(tr("status_drawing_cannot_change_settings"), True)
            self.status_callback(tr("status_drawing_cannot_change_settings"))
            return False

        self._cancel_active_captures(reason_key="status_image_processing_cancelled_hex_wait")

        if self.draw_region is None:
            self._log("Область для рисования не выбрана. Обработка невозможна.", True)
            self.status_callback(tr("status_error_no_area_for_processing"))
            return False

        self.status_callback(tr("status_processing_image"))
        self._log("Подготовка изображения и палитры...")
        start_time = time.time()
        self.has_transparency = False

        img_to_process_pil = None

        if self.source_pil_image and isinstance(self.source_pil_image, Image.Image):
            img_to_process_pil = self.source_pil_image
            self._log(f"Обработка изображения из памяти (источник: {self.IMAGE_PATH}).")
        elif self.IMAGE_PATH and os.path.exists(self.IMAGE_PATH) and self.IMAGE_PATH != tr("clipboard_source_name"):
            self._log(f"Загрузка изображения из файла: {self.IMAGE_PATH}")
            try:
                img_to_process_pil = Image.open(self.IMAGE_PATH)
            except Exception as e:
                self._log(f"Ошибка загрузки изображения из файла '{self.IMAGE_PATH}': {e}", True)
                self.status_callback(tr("status_file_not_found_error"))
                self.image_original_rgba = None;
                self.cluster_map = None;
                self.drawn_mask = None;
                self.color_palette = [];
                self.quantized_preview_image = None; self.small_components_preview_mask = None;
                self.source_pil_image = None;
                if self.pixel_update_callback: self.pixel_update_callback()
                if self.preview_update_callback: self.preview_update_callback(None)
                if self.stencil_enabled: self._update_active_stencil()
                return False
        else:
            log_msg = "Источник изображения не найден."
            if self.IMAGE_PATH:
                log_msg += f" (Проверено: self.source_pil_image is None или не изображение; self.IMAGE_PATH='{self.IMAGE_PATH}' не существует или невалиден)"
            self._log(log_msg, True)
            self.status_callback(tr("status_error_no_image_for_processing"))
            self.image_original_rgba = None;
            self.cluster_map = None;
            self.drawn_mask = None;
            self.color_palette = [];
            self.quantized_preview_image = None; self.small_components_preview_mask = None;
            self.source_pil_image = None;
            if self.pixel_update_callback: self.pixel_update_callback()
            if self.preview_update_callback: self.preview_update_callback(None)
            if self.stencil_enabled: self._update_active_stencil()
            return False

        try:
            if not isinstance(img_to_process_pil, Image.Image):
                raise ValueError(f"img_to_process_pil не является объектом PIL.Image, а {type(img_to_process_pil)}")
            if img_to_process_pil.mode != "RGBA":
                self.image_original_rgba = img_to_process_pil.convert("RGBA")
            else:
                self.image_original_rgba = img_to_process_pil.copy()
        except Exception as e:
            self._log(f"Критическая ошибка при подготовке img_to_process_pil для RGBA: {e}", True)
            traceback.print_exc()
            self.status_callback(tr("status_processing_error", e=str(e)))
            self.image_original_rgba = None;
            self.cluster_map = None;
            self.drawn_mask = None;
            self.color_palette = [];
            self.quantized_preview_image = None; self.small_components_preview_mask = None;
            self.source_pil_image = None;
            if self.pixel_update_callback: self.pixel_update_callback()
            if self.preview_update_callback: self.preview_update_callback(None)
            return False

        success = False
        try:
            if not self.image_original_rgba or not isinstance(self.image_original_rgba, Image.Image):
                self._log(
                    "Критическая ошибка: self.image_original_rgba не установлено корректно перед основной обработкой.",
                    True)
                raise ValueError("self.image_original_rgba is None or not a PIL Image before main processing")

            # Crop / flip the effective source before it is scaled to the draw area.
            # Default crop_norm (0,0,1,1) with no flips = no-op (drawing unchanged).
            self._apply_source_transforms()

            x, y, w, h = self.draw_region
            w = max(1, int(w))
            h = max(1, int(h))
            resample_mode = Image.LANCZOS if not hasattr(Image, "Resampling") else Image.Resampling.LANCZOS
            if self.get_stretch_to_area():
                img_resized_rgba_full = self.image_original_rgba.resize((w, h), resample_mode)
            else:
                manual_rect = self.ensure_manual_image_rect()
                img_resized_rgba_full = self._render_manual_fit(w, h, manual_rect, resample_mode)

            w_brush_map = max(1, (w + self.brush_size - 1) // self.brush_size)
            h_brush_map = max(1, (h + self.brush_size - 1) // self.brush_size)
            self._log(f"Cluster map size: {w_brush_map}x{h_brush_map} (Brush: {self.brush_size}px)")

            img_small_rgba_map = img_resized_rgba_full.resize((w_brush_map, h_brush_map),
                                                              Image.LANCZOS if not hasattr(Image,
                                                                                           "Resampling") else Image.Resampling.LANCZOS)
            np_img_small_rgba_map = np.array(img_small_rgba_map)
            np_img_small_rgb_map = np_img_small_rgba_map[:, :, :3]
            alpha_channel_map = np_img_small_rgba_map[:, :, 3]

            self.background_mask = np.zeros((h_brush_map, w_brush_map), dtype=bool)
            background_cluster_id = -1
            self._background_cluster_id = background_cluster_id

            if np.any(alpha_channel_map < (255 - self.alpha_threshold)):
                self.has_transparency = True

            # Transparency ALWAYS defines background: near/fully-transparent
            # pixels are never part of the drawing, regardless of the
            # background-removal feature. Without this, B&W mode flattens a
            # transparent PNG (RGB of transparent pixels is usually black) onto
            # black and paints the whole frame as a solid square. Same
            # alpha convention as the perceptual color pipeline (keep alpha >
            # threshold => background is alpha <= threshold).
            try:
                self.background_mask |= (alpha_channel_map <= int(self.alpha_threshold))
            except Exception:
                log.debug('ignored exception applying alpha background mask', exc_info=True)

            if self.use_ai_background and self.ai_backend.is_enabled():
                self._log("Using AI model for background mask.")
                prob_mask = self.compute_background_mask_ai(img_resized_rgba_full)
                if prob_mask is not None:
                    mask_bool = prob_mask <= self.bg_ai_threshold
                    mask_small = cv2.resize(mask_bool.astype(np.uint8), (w_brush_map, h_brush_map),
                                            interpolation=cv2.INTER_NEAREST)
                    self.background_mask |= mask_small.astype(bool)
                else:
                    self._log("AI background removal failed.", True)
            elif self.use_ai_background:
                self.use_ai_background = False

            if self.remove_background:
                self._log(f"Classic background removal ON. Has significant alpha: {self.has_transparency}")
                self._apply_basic_bg_removal(np_img_small_rgb_map, alpha_channel_map)
            if not self.remove_background and not self.use_ai_background:
                self._log("Background removal OFF.")

            self.color_palette = []
            self._manual_palette_target_rgb = {}
            self._manual_palette_mix_solution_cache = {}
            self.cluster_map = np.full((h_brush_map, w_brush_map), background_cluster_id, dtype=int)
            self._cluster_uses_manual_palette = False

            highlight_mask = None
            if self.mode == "bw":
                self._log(f"B&W mode selected. Draw: {self.bw_draw_mode}")
                if np_img_small_rgb_map.size == 0:
                    self._log("Cannot process B&W mode, small RGB map is empty.", True)
                    raise ValueError("Small RGB map is empty, cannot convert to grayscale for B&W mode.")

                img_gray_map = cv2.cvtColor(np_img_small_rgb_map, cv2.COLOR_RGB2GRAY)
                _, thresh_map = cv2.threshold(img_gray_map, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
                self.cluster_map = (thresh_map == 0).astype(np.int8)
                self.cluster_map[self.background_mask] = background_cluster_id

                counts = {}
                counts[1] = np.count_nonzero(self.cluster_map == 1)
                counts[0] = np.count_nonzero(self.cluster_map == 0)
                temp_palette_bw = []
                if self.bw_draw_mode in ["both", "black_only"] and counts[1] > 0:
                    temp_palette_bw.append(
                        {"hex": "#000000", "id": 1, "count": counts[1], "rgb": (0, 0, 0), "luminance": 0})
                if self.bw_draw_mode in ["both", "white_only"] and counts[0] > 0:
                    temp_palette_bw.append(
                        {"hex": "#FFFFFF", "id": 0, "count": counts[0], "rgb": (255, 255, 255), "luminance": 255})

                sorted_bw = self._sort_palette_entries(temp_palette_bw)
                self.color_palette = [(p["hex"], p["id"], p["count"], p["rgb"]) for p in sorted_bw]
                sort_desc = f"tone={self.tone_sequence}, areas={self.area_sequence}"
                self._log(
                    f"B&W palette created ({sort_desc}, Mode: {self.bw_draw_mode}): {len(self.color_palette)} colors")

            else:
                self._log("Color mode selected.")
                pixels_for_clustering = np_img_small_rgb_map[~self.background_mask]
                palette_applied = False
                manual_cache = None
                manual_requested = (self.color_picking_method == "manual_palette")
                use_manual_palette = False
                if manual_requested:
                    try:
                        manual_cache = self._get_manual_palette_cache()
                    except Exception as exc:
                        manual_cache = None
                        self._log(f"Manual palette normalization failed: {exc}", True)
                    if manual_cache and manual_cache.get("entries"):
                        use_manual_palette = True
                        self._log(f"Manual palette quantization active ({len(manual_cache['entries'])} captured color(s)).")
                    else:
                        self._log("Manual palette quantization inactive: palette empty or invalid.")

                if len(pixels_for_clustering) == 0:
                    self._log("No non-background pixels for clustering.")
                else:
                    try:
                        if use_manual_palette and manual_cache:
                            if self.manual_palette_mix_enabled:
                                try:
                                    palette_applied = self._apply_manual_palette_universal(
                                        pixels_for_clustering,
                                        manual_cache,
                                        h_brush_map,
                                        w_brush_map,
                                    )
                                except Exception as manual_mix_exc:
                                    palette_applied = False
                                    self._log(f"Manual universal palette error: {manual_mix_exc}", True)
                    except Exception as manual_exc:
                        self._log(f"Manual palette quantization error: {manual_exc}", True)
                        traceback.print_exc()
                        palette_applied = False
                        self._cluster_uses_manual_palette = False

                    if not palette_applied:
                        if manual_requested and getattr(self, "drawing_enabled", False):
                            err_msg = tr("status_error_cannot_define_manual_palette_drawing")
                            self._log(err_msg + " (manual palette quantization failed).", True)
                            self.status_callback(err_msg)
                            self.stop_flag = True
                            self.drawing_enabled = False
                            return False
                        if manual_requested:
                            self._cluster_uses_manual_palette = False
                        perceptual_enabled = (
                            self._normalize_prep_color_space(getattr(self, "prep_color_space", "legacy_rgb"))
                            in {"cielab", "oklab"}
                        )
                        if perceptual_enabled:
                            try:
                                palette_applied = self._run_perceptual_prep_pipeline(
                                    img_small_rgba_map,
                                    np_img_small_rgb_map,
                                    background_cluster_id,
                                )
                            except Exception as perceptual_exc:
                                palette_applied = False
                                self._log(f"Perceptual prep pipeline failed: {perceptual_exc}", True)
                                traceback.print_exc()

                        if not palette_applied:
                            base_non_bg_pixels = pixels_for_clustering
                            kmeans_pixels = base_non_bg_pixels
                            kmeans_bg_mask = self.background_mask
                            reduce_kmeans_grid = False
                            reduce_shape = (h_brush_map, w_brush_map)
                            total_cells = int(w_brush_map * h_brush_map)
                            predict_cap = int(max(10000, getattr(self, "kmeans_predict_max_pixels", 900000)))
                            if total_cells > predict_cap:
                                try:
                                    scale = math.sqrt(total_cells / float(predict_cap))
                                    reduced_w = max(1, int(round(w_brush_map / scale)))
                                    reduced_h = max(1, int(round(h_brush_map / scale)))
                                    reduced_rgb = cv2.resize(
                                        np_img_small_rgb_map,
                                        (reduced_w, reduced_h),
                                        interpolation=cv2.INTER_AREA,
                                    )
                                    reduced_bg = cv2.resize(
                                        self.background_mask.astype(np.uint8),
                                        (reduced_w, reduced_h),
                                        interpolation=cv2.INTER_NEAREST,
                                    ).astype(bool)
                                    reduced_pixels = reduced_rgb[~reduced_bg]
                                    if len(reduced_pixels) > 0:
                                        reduce_kmeans_grid = True
                                        reduce_shape = (reduced_h, reduced_w)
                                        kmeans_pixels = reduced_pixels
                                        kmeans_bg_mask = reduced_bg
                                        self._log(
                                            f"Large area optimization: clustering grid {w_brush_map}x{h_brush_map} -> {reduced_w}x{reduced_h}"
                                        )
                                except Exception as reduce_exc:
                                    self._log(f"Large area optimization failed: {reduce_exc}", True)

                            self._log(
                                f"Pixels for clustering: {len(kmeans_pixels)} (source non-bg: {len(base_non_bg_pixels)}, grid: {w_brush_map * h_brush_map})"
                            )
                            unique_pixels = np.unique(kmeans_pixels, axis=0)
                            k_actual = min(self.k_clusters, len(unique_pixels))
                            if k_actual < 1:
                                self._log("No unique non-background pixels for clustering (k_actual < 1).")
                            else:
                                self._log(f"Running KMeans with k={k_actual} on non-background pixels...")
                                kmeans = KMeans(n_clusters=k_actual, random_state=42, init='k-means++', n_init='auto',
                                                algorithm='lloyd')
                                clustering_pixels = kmeans_pixels.astype(np.float32, copy=False)
                                fit_pixels = clustering_pixels
                                sample_cap = int(max(5000, getattr(self, "kmeans_fit_max_samples", 220000)))
                                if len(clustering_pixels) > sample_cap:
                                    self._log(
                                        f"Sampling KMeans fit pixels: {sample_cap}/{len(clustering_pixels)}"
                                    )
                                    rng = np.random.default_rng(42)
                                    sample_idx = rng.choice(len(clustering_pixels), size=sample_cap, replace=False)
                                    fit_pixels = clustering_pixels[sample_idx]
                                kmeans.fit(fit_pixels)
                                non_bg_labels = kmeans.predict(clustering_pixels)

                                # Fill cluster ids in one pass (much faster than nested Python loops).
                                if reduce_kmeans_grid:
                                    work_cluster_map = np.full(reduce_shape, background_cluster_id, dtype=int)
                                else:
                                    work_cluster_map = self.cluster_map
                                flat_cluster = work_cluster_map.reshape(-1)
                                flat_bg_mask = kmeans_bg_mask.reshape(-1)
                                flat_cluster[~flat_bg_mask] = non_bg_labels.astype(flat_cluster.dtype, copy=False)
                                if reduce_kmeans_grid:
                                    self.cluster_map = cv2.resize(
                                        work_cluster_map.astype(np.int32),
                                        (w_brush_map, h_brush_map),
                                        interpolation=cv2.INTER_NEAREST,
                                    ).astype(int, copy=False)
                                    self.cluster_map[self.background_mask] = background_cluster_id

                                centers = kmeans.cluster_centers_.astype(int)
                                center_map = {cid: tuple(centers[cid]) for cid in range(k_actual)}

                                merges_done = 0
                                threshold = max(0, int(getattr(self, "fast_min_region_area", 0)))
                                if threshold > 0:
                                    try:
                                        merges_done = self._merge_small_components_into_neighbors(center_map, background_cluster_id)
                                    except Exception as merge_exc:
                                        merges_done = 0
                                        self._log(f"[MergeSmall] merge failed: {merge_exc}", True)

                                temp_palette_color = []
                                for cid, rgb_t in center_map.items():
                                    count_val = int(np.count_nonzero(self.cluster_map == cid))
                                    if count_val <= 0:
                                        continue
                                    hex_c = "#{:02X}{:02X}{:02X}".format(rgb_t[0], rgb_t[1], rgb_t[2])
                                    # legacy k-means путь: lightness в той же перцептивной метрике,
                                    # что использует _sort_palette_entries (иначе band-span ломается)
                                    lum = self._palette_lightness(rgb_t)
                                    temp_palette_color.append(
                                        {"hex": hex_c, "id": cid, "count": count_val, "rgb": rgb_t, "luminance": lum})

                                sorted_palette_color = self._sort_palette_entries(temp_palette_color)
                                self.color_palette = [(p["hex"], p["id"], p["count"], p["rgb"]) for p in sorted_palette_color]
                                sort_desc = f"tone={self.tone_sequence}, areas={self.area_sequence}"
                                if merges_done:
                                    self._log(f"[MergeSmall] merged {merges_done} region(s) into neighbors (min area {self.fast_min_region_area}).")
                                self._log(f"Color palette created ({sort_desc}): {len(self.color_palette)} colors")

                if manual_requested and manual_cache and manual_cache.get("entries") and not self._cluster_uses_manual_palette:
                    self._project_clusters_to_manual_palette(manual_cache)

            # Aggressive despeckle (opt-in): relabel every remaining sub-threshold
            # component to the nearest large region so no costly tiny dots remain.
            # Post-quantization; runs once for both the perceptual and legacy paths.
            try:
                self._merge_identical_palette_colors(background_cluster_id)
            except Exception as merge_exc:
                self._log(f"Identical-color merge failed: {merge_exc}", True)
            try:
                if bool(getattr(self, "aggressive_despeckle_enabled", False)):
                    self._apply_aggressive_despeckle(background_cluster_id)
            except Exception as despeckle_exc:
                self._log(f"Aggressive despeckle failed: {despeckle_exc}", True)

            # Semantic plane removal (opt-in): drop the backdrop or the object
            # plane from drawing. Post-quantization, so it runs here once the
            # cluster_map + palette are final for both the perceptual and the
            # legacy k-means paths.
            try:
                if self._normalize_semantic_background_drop(getattr(self, "semantic_background_drop", "off")) != "off":
                    self._apply_semantic_background_removal(background_cluster_id)
            except Exception as sem_bg_exc:
                self._log(f"Semantic background removal failed: {sem_bg_exc}", True)

            threshold = max(0, int(getattr(self, "fast_min_region_area", 0)))
            if threshold > 0:
                highlight_mask = self._compute_small_component_mask(threshold, background_cluster_id)
            else:
                highlight_mask = None

            preview_np_rgba = np.zeros((h, w, 4), dtype=np.uint8)
            active_palette_map_rgb = {item[1]: item[3] for item in self.color_palette}

            # Fast-path for brush=1: avoid millions of Python loop iterations.
            if self.brush_size == 1 and h_brush_map == h and w_brush_map == w:
                non_bg_mask = self.cluster_map != background_cluster_id
                if self.mode == "bw":
                    black_mask = non_bg_mask & (self.cluster_map == 1)
                    white_mask = non_bg_mask & (self.cluster_map == 0)
                    if self.bw_draw_mode in ("both", "black_only"):
                        preview_np_rgba[black_mask, :3] = (0, 0, 0)
                        preview_np_rgba[black_mask, 3] = 255
                    if self.bw_draw_mode in ("both", "white_only"):
                        preview_np_rgba[white_mask, :3] = (255, 255, 255)
                        preview_np_rgba[white_mask, 3] = 255
                else:
                    for cluster_id, rgb in active_palette_map_rgb.items():
                        cluster_mask = non_bg_mask & (self.cluster_map == cluster_id)
                        if np.any(cluster_mask):
                            preview_np_rgba[cluster_mask, :3] = rgb
                            preview_np_rgba[cluster_mask, 3] = 255
                    unresolved = non_bg_mask & (preview_np_rgba[:, :, 3] == 0)
                    if np.any(unresolved):
                        preview_np_rgba[unresolved, :3] = (255, 0, 255)
                        preview_np_rgba[unresolved, 3] = 255

            else:
                for r_map_idx in range(h_brush_map):
                    for c_map_idx in range(w_brush_map):
                        cluster_val = self.cluster_map[r_map_idx, c_map_idx]
                        r_start_full, c_start_full = r_map_idx * self.brush_size, c_map_idx * self.brush_size
                        r_end_full, c_end_full = min(r_start_full + self.brush_size, h), min(c_start_full + self.brush_size,
                                                                                             w)
                        if cluster_val == background_cluster_id:
                            preview_np_rgba[r_start_full:r_end_full, c_start_full:c_end_full, 3] = 0
                        else:
                            target_color_rgb = None
                            make_pixel_opaque = False
                            if self.mode == "bw":
                                is_black = (cluster_val == 1)
                                is_white = (cluster_val == 0)
                                if self.bw_draw_mode == "black_only" and is_black:
                                    target_color_rgb = (0, 0, 0); make_pixel_opaque = True
                                elif self.bw_draw_mode == "white_only" and is_white:
                                    target_color_rgb = (255, 255, 255); make_pixel_opaque = True
                                elif self.bw_draw_mode == "both":
                                    if is_black:
                                        target_color_rgb = (0, 0, 0); make_pixel_opaque = True
                                    elif is_white:
                                        target_color_rgb = (255, 255, 255); make_pixel_opaque = True
                                else:
                                    if is_black:
                                        target_color_rgb = (0, 0, 0); make_pixel_opaque = True
                                    elif is_white:
                                        target_color_rgb = (255, 255, 255); make_pixel_opaque = True
                            elif self.mode == "color":
                                target_color_rgb = active_palette_map_rgb.get(cluster_val)
                                if target_color_rgb:
                                    make_pixel_opaque = True
                                else:
                                    target_color_rgb = (255, 0, 255); make_pixel_opaque = True

                            if make_pixel_opaque and target_color_rgb is not None:
                                # Small regions are drawn like any other: the preview used to
                                # show them as holes, so kept details looked like white dots.
                                preview_np_rgba[r_start_full:r_end_full, c_start_full:c_end_full, :3] = target_color_rgb
                                preview_np_rgba[r_start_full:r_end_full, c_start_full:c_end_full, 3] = 255

            self.small_components_preview_mask = highlight_mask if highlight_mask is not None else None
            self.quantized_preview_image = Image.fromarray(preview_np_rgba)

            if self.cluster_map is not None:
                self.drawn_mask = self._with_region_exclusion(self.cluster_map == background_cluster_id)
            else:
                self.drawn_mask = np.zeros((h_brush_map, w_brush_map), dtype=bool)

            self.current_color_index = 0
            success = True

        except Exception as e:
            self._log(f"Processing error in main image processing block: {e}", True)
            traceback.print_exc()
            self.status_callback(tr("status_processing_error", e=str(e)))
            self.image_original_rgba = None;
            self.cluster_map = None;
            self.drawn_mask = None;
            self.color_palette = [];
            self.quantized_preview_image = None; self.small_components_preview_mask = None;
            success = False

        finally:
            end_time = time.time()
            if success:
                self._log(f"Preparation completed in {end_time - start_time:.2f} sec.")
                self.status_callback(tr("status_image_processed"))
            else:
                self._log("Preparation failed.")
                if self.preview_update_callback: self.preview_update_callback(None)
                if self.pixel_update_callback: self.pixel_update_callback()

            if self.pixel_update_callback: self.pixel_update_callback()
            if self.preview_update_callback:
                self.preview_update_callback(self.quantized_preview_image if success else None)

        return success



    def _set_alpha_slider_value(self, alpha_value: float) -> bool:
        params = getattr(self, "alpha_slider_params", None)
        if not params:
            self._log(tr("status_error_alpha_slider_not_calibrated"), True)
            return False
        try:
            alpha = max(0.0, min(1.0, float(alpha_value)))
        except Exception:
            alpha = 0.0
        orientation, fixed_coord, coord_one, coord_zero = params
        if orientation == "vertical":
            click_x = int(round(fixed_coord))
            click_y = int(round(coord_zero * (1.0 - alpha) + coord_one * alpha))
        else:
            click_y = int(round(fixed_coord))
            click_x = int(round(coord_zero * (1.0 - alpha) + coord_one * alpha))
        try:
            self._click_abs(click_x, click_y)
            if not self._sleep_with_abort(0.05):
                return False
            self._input_click(button="left")
            if not self._sleep_with_abort(0.05):
                return False
        except Exception as exc:
            self._log(f"Alpha slider set error: {exc}", True)
            return False
        return True

    def _activate_manual_palette_entry(self, entry: dict | None) -> bool:
        if not entry:
            return False
        try:
            x = int(entry.get("x", entry.get("px", 0)))
            y = int(entry.get("y", entry.get("py", 0)))
        except Exception:
            return False
        try:
            if self._automation_cancelled():
                return False
            self._click_abs(x, y)
            if not self._sleep_with_abort(0.05):
                return False
            if self._automation_cancelled():
                return False
            self._input_click(button="left")
            if not self._sleep_with_abort(0.05):
                return False
        except Exception as exc:
            self._log(f"Manual palette pick error: {exc}", True)
            self.stop_flag = True
            self.drawing_enabled = False
            self.status_callback(tr("status_error_color_selection"))
            return False
        return True



    def _refresh_screen_metrics(self):
        """Refresh the native desktop mapping once before a drawing run."""
        self._input.backend.reset()

    def _move_abs(self, x, y):
        """Move in physical desktop pixels through the common input boundary."""
        self._input.move(int(x), int(y))
        if self._pen_pos != (int(x), int(y)):
            self._pen_prev = self._pen_pos
        self._pen_pos = (int(x), int(y))

    _NUDGE_STEP_WAIT = 0.015
    # «Нарисуй меня!» draws a segment only when the cursor moves on and ignores a
    # repeated point, so every stroke lost its last segment (live 2026-10-01: a
    # press, one 60 px move and a release left a dot even after 300 ms; a further
    # 4 px move kept the line). The step goes back over the painted segment.
    _RELEASE_STEP_PX = 4
    _RELEASE_STEP_WAIT = 0.03

    def _release_step_point(self):
        """A point a few pixels back along the last segment, or None for a dot."""
        pos, prev = self._pen_pos, self._pen_prev
        if pos is None or prev is None or prev == pos:
            return None
        dx, dy = prev[0] - pos[0], prev[1] - pos[1]
        length = math.hypot(dx, dy)
        step = min(length, float(self._RELEASE_STEP_PX))
        return (int(round(pos[0] + dx / length * step)), int(round(pos[1] + dy / length * step)))

    def _nudge_pointer(self) -> None:
        """Let a frame-sampling target register the cursor where it stands."""
        pos = self._pen_pos
        if pos is None:
            return
        x, y = pos
        for dx in (1, 0, -1, 0):
            self._input.move(x + dx, y)
            time.sleep(self._NUDGE_STEP_WAIT)

    def _capture_point(self, fallback=None):
        """Return the current cursor position in PHYSICAL pixels via GetCursorPos
        (= the same space `_click_abs` / GetSystemMetrics use). Coordinate-capture
        handlers must use THIS instead of the listener's (x, y): a hook library may
        report DPI-scaled/logical coordinates while the click targets physical
        pixels -> the saved point would click off-target at non-100% scaling."""
        try:
            cx, cy = interception.mouse_position()  # win32api.GetCursorPos -> physical
            return (int(cx), int(cy))
        except Exception:
            log.debug('ignored exception in _capture_point GetCursorPos', exc_info=True)
            return fallback

    def _click_abs(self, x, y):
        """Position for a control click using the same mapping as drawing."""
        self._input.move(int(x), int(y))
        # The press nudge and the release repeat act where the cursor is: a stale
        # canvas position would move every colour click back onto the canvas.
        self._pen_pos = (int(x), int(y))
        self._pen_prev = None       # a control is clicked or dragged, not stroked

    def _input_click(self, button="left"):
        try:
            self._pen_down(button)
        finally:
            if not self._mouse_up_with_settle(button):
                raise RuntimeError("Cannot release the mouse after a click")

    def _ui_input_wait(self, seconds):
        return not self._sleep_with_abort(max(0., float(seconds)))

    def _ui_click_at(self, x, y, *, clicks: int = 2, hold: float = 0.05, settle: float = 0.08) -> None:
        """A target click owns the button through release, including cancellation."""
        if self._automation_cancelled():
            return
        self._click_abs(int(x), int(y))
        if self._ui_input_wait(settle):
            return
        for _ in range(max(1, int(clicks))):
            if self._automation_cancelled():
                return
            try:
                self._press_pointer("left")
                if self._ui_input_wait(max(.01, hold)):
                    return
            finally:
                if not self._mouse_up_with_settle():
                    raise RuntimeError("Cannot release the mouse after a control click")
            if self._ui_input_wait(settle):
                return

    def _ui_drag(self, x1, y1, x2, y2, *, hold: float = 0.06, settle: float = 0.08, wiggle=None) -> None:
        """Move a control in physical desktop coordinates; always release on stop."""
        if self._automation_cancelled():
            return
        x1, y1, x2, y2 = int(x1), int(y1), int(x2), int(y2)
        self._click_abs(x1, y1)
        if self._ui_input_wait(settle):
            return
        try:
            self._press_pointer("left")
            if self._ui_input_wait(max(.01, hold)):
                return
            distance = math.hypot(x2 - x1, y2 - y1)
            steps = max(1, min(60, int(distance / 12) + 1))
            duration = min(.6, .15 + distance * .0008)
            for i in range(1, steps + 1):
                if self._automation_cancelled():
                    return
                t = i / steps
                self._click_abs(int(round(x1 + (x2 - x1) * t)), int(round(y1 + (y2 - y1) * t)))
                if self._ui_input_wait(duration / steps):
                    return
            if wiggle:
                wx, wy = int(wiggle[0]), int(wiggle[1])
                for dx, dy in ((wx, wy), (-wx, -wy), (0, 0)):
                    self._click_abs(x2 + dx, y2 + dy)
                    if self._ui_input_wait(.03):
                        return
            self._ui_input_wait(max(.01, hold))
        finally:
            if not self._mouse_up_with_settle():
                raise RuntimeError("Cannot release the mouse after dragging a control")
        self._ui_input_wait(settle)

    def _press_pointer(self, button: str = "left") -> None:
        """Every press, on the canvas or on a control, the way the drawing pen presses.
        A target sampling the cursor once per frame takes a press right after a jump
        at the previous cursor position (live «Нарисуй меня!»: the brush slider moved
        every other time, test dots landed where the cursor had waited and a slider
        drag drew a line there)."""
        if button == "left" and getattr(self, "pen_press_nudge", False):
            self._nudge_pointer()
        self._input.button_down(button)

    def _pen_down(self, button: str = "left"):
        """Press through the common transport; wait cooperatively after press."""
        self._pen_prev = None
        self._press_pointer(button)
        self._split_state = "fresh" if button == "left" and getattr(self, "pen_split_strokes", False) else None
        self._sleep_with_abort(float(getattr(self, "pen_button_delay", .03)), step=.01)

    def _split_gap(self) -> float:
        """Pause after a release in straight-stroke mode; 0 = three frames of the monitor
        under the drawing (a browser page takes a stroke on its next renders:
        live Gartic Phone at 166 fps lost strokes at 7 ms, none at 12 ms)."""
        gap = float(getattr(self, "pen_stroke_gap", 0.0) or 0.0)
        if gap > 0:
            return gap
        region = tuple(getattr(self, "draw_region", None) or ())
        cached = getattr(self, "_split_gap_auto", None)
        if cached is None or cached[0] != region:
            hz = 60
            try:
                import win32api
                point = (int(region[0] + region[2] / 2), int(region[1] + region[3] / 2)) if len(region) == 4 else (0, 0)
                device = win32api.GetMonitorInfo(win32api.MonitorFromPoint(point, 2))["Device"]
                hz = int(win32api.EnumDisplaySettings(device, -1).DisplayFrequency) or 60
            except Exception:
                log.debug("monitor refresh rate unknown; assuming 60 Hz", exc_info=True)
            # three frames: the page may render slower than the monitor refreshes
            # (240 Hz monitor, 166 fps page); 12 ms is the smallest gap proven live
            cached = (region, max(0.012, 3.0 / max(24, min(hz, 500)) + 0.002))
            self._split_gap_auto = cached
        return cached[1]

    def _split_segment(self, x1, y1, x2, y2) -> bool:
        """Straight-stroke mode: end the held stroke at a turn and press again.

        Returns False for a skipped one-cell step between two runs (both of its
        cells are covered by the runs); the pen then waits lifted at its end."""
        cell = max(1, int(getattr(self, "brush_size", 1) or 1))
        step = max(abs(int(x2) - int(x1)), abs(int(y2) - int(y1)))
        gap = self._split_gap()
        state = self._split_state
        if state == "run" and step <= cell:
            self._input.button_up("left")
            self._sleep_with_abort(gap, check_target=False)
            self._move_abs(x2, y2)
            self._split_state = "lifted"
            return False
        if state in ("run", "step"):
            self._input.button_up("left")
            self._sleep_with_abort(gap, check_target=False)
            self._input.button_down("left")
        elif state == "lifted":
            self._input.button_down("left")
        self._split_state = "run" if step > cell else "step"
        return True

    def draw_line(self, x1, y1, x2, y2, *, pace=None):
        # PAUSE is owned by the CALLER loops: they lift the pen, wait, and re-press.
        # draw_line must NOT wait on pause here -- doing so froze the loop with the
        # pen still held DOWN (the "LMB stuck during pause" bug). It only bails on a
        # hard stop/cancel; a pause pressed mid-stroke just lets this stroke finish,
        # then the caller lifts the pen on its next check.
        #
        # `pace` (entry_factor, exit_factor) — optional plotter-style speed profile
        # for the PACED fill path below: the sub-step COORDINATES never change
        # (pixels stay bit-identical), only the dwell between sub-steps follows a
        # trapezoid (slow out of corners, cruise on straights). None = constant
        # dwell, the legacy behaviour.
        if self.stop_flag or self._automation_cancelled():
            return
        if self._split_state is not None and getattr(self, "pen_split_strokes", False):
            if not self._split_segment(x1, y1, x2, y2):
                return
        fill_delay = float(getattr(self, "area_fill_delay", 0.0) or 0.0)
        max_step = int(getattr(self, "pen_max_step", 0) or 0)
        if fill_delay > 0.0:
            # Paint long strokes GRADUALLY: walk the pen along the stroke in
            # per-pixel sub-steps with `fill_delay` between them, instead of one
            # instant A->B drag. This regulates the FILL speed of big areas only
            # (long strokes / merged straight runs); short moves (single cells,
            # corners, small areas) are unaffected. 0 = instant (old behavior).
            dx = int(x2) - int(x1)
            dy = int(y2) - int(y1)
            dist = max(abs(dx), abs(dy))
            if dist > 1:
                steps = min(int(dist), 600)  # cap so huge runs stay bounded
                dwells = None
                if pace is not None and bool(getattr(self, "motion_profile_enabled", False)):
                    try:
                        dwells = trapezoid_dwells(
                            steps, fill_delay, pace[0], pace[1],
                            vmax_factor=float(getattr(self, "motion_vmax_factor", 1.6)),
                            accel_px=int(getattr(self, "motion_accel_px", 6)),
                        )
                    except Exception:
                        dwells = None
                for s in range(1, steps):
                    # Pause (drawing_enabled=False) must NOT break a stroke mid-way — that
                    # left the rest of the stroke undrawn and teleported to the endpoint
                    # (visible gap, esp. with spray). Only a HARD stop/cancel breaks; a
                    # pause lets the stroke finish, then the caller lifts the pen.
                    if self.stop_flag or self._automation_cancelled():
                        break
                    ix = int(round(x1 + dx * s / steps))
                    iy = int(round(y1 + dy * s / steps))
                    try:
                        self._move_abs(ix, iy)
                    except Exception:
                        log.debug('ignored exception in draw_line fill sub-step', exc_info=True)
                    self._sleep_with_abort(fill_delay if dwells is None else float(dwells[s - 1]))
        elif max_step > 0:
            # CONTINUOUS stroke: walk A->B in <=max_step px hops instead of one instant
            # jump, so a target that stamps at the sampled cursor position (Roblox spray)
            # gets overlapping marks along the whole stroke (no gap on long jumps).
            # `draw_delay` (if any) paces each hop so the target samples it.
            dx = int(x2) - int(x1)
            dy = int(y2) - int(y1)
            dist = max(abs(dx), abs(dy))
            if dist > max_step:
                steps = min((dist + max_step - 1) // max_step, 5000)  # ceil, bounded
                for s in range(1, steps):
                    # Pause (drawing_enabled=False) must NOT break a stroke mid-way — that
                    # left the rest of the stroke undrawn and teleported to the endpoint
                    # (visible gap, esp. with spray). Only a HARD stop/cancel breaks; a
                    # pause lets the stroke finish, then the caller lifts the pen.
                    if self.stop_flag or self._automation_cancelled():
                        break
                    ix = int(round(x1 + dx * s / steps))
                    iy = int(round(y1 + dy * s / steps))
                    try:
                        self._move_abs(ix, iy)
                    except Exception:
                        log.debug('ignored exception in draw_line continuous sub-step', exc_info=True)
                    if self.draw_delay > 0:
                        self._sleep_with_abort(self.draw_delay)
        # On a HARD stop/cancel mid-stroke, don't teleport the held pen to the endpoint
        # (that drags a stray line / leaves a partial stroke). Pause (drawing_enabled=False)
        # still completes the stroke above, so the endpoint is reached normally.
        if not (self.stop_flag or self._automation_cancelled()):
            try:
                self._move_abs(x2, y2)
            except Exception:
                log.debug('ignored exception in self._move_abs(x2, y2)', exc_info=True)
            if self.draw_delay > 0 and self.drawing_enabled:
                self._sleep_with_abort(self.draw_delay)

    def _dfs_traversal_order(self, mask_for_color, start_row, start_col):
        """Pure (no drawing) replica of dfs_4dir_draw's stack walk: returns the
        cells in the EXACT order the pen would visit them (pop order). Lets the
        optional run-length / A*-bridge renderer work on the whole sequence.
        Like the inline walk, it treats already-drawn cells as walls (the same
        `not self.drawn_mask` gate) so it does not re-traverse the outline / the
        background / earlier same-color components when handed the full color mask."""
        h, w = mask_for_color.shape
        if not (0 <= start_row < h and 0 <= start_col < w) or mask_for_color[start_row, start_col] != 255:
            return []
        drawn = self.drawn_mask
        if bool(getattr(self, "route_kernels_numba_enabled", True)):
            try:
                fast = pop_order_fast(mask_for_color, drawn, start_row, start_col,
                                      self.dfs_4dir_neighbor_offsets)
                if fast is not None:
                    return fast
            except Exception:
                log.debug("numba pop-order kernel failed; python fallback", exc_info=True)
        visited = np.zeros((h, w), dtype=bool)
        offsets = self.dfs_4dir_neighbor_offsets
        order: list[tuple[int, int]] = []
        stack = [(int(start_row), int(start_col))]
        visited[start_row, start_col] = True
        while stack:
            r, c = stack.pop()
            order.append((r, c))
            for dr, dc in offsets:
                nr, nc = r + dr, c + dc
                if 0 <= nr < h and 0 <= nc < w and mask_for_color[nr, nc] == 255 \
                        and not visited[nr, nc] and not (drawn is not None and drawn[nr, nc]):
                    visited[nr, nc] = True
                    stack.append((nr, nc))
        return order

    def _dfs_greedy_snake_order(self, mask_for_color, start_row, start_col):
        """Contiguous 'snake' visit order — the no-dots / no-overdraw alternative to the
        stack-DFS pop order, for when A* bridging is OFF.

        An organic, DFS-like greedy walk that PREFERS to keep going straight (so it lays
        long strokes like the old fill), and only LIFTS the pen when it hits a dead-end,
        jumping to the START of the nearest unfinished stroke. Two properties the user
        asked for:
          * NO 'dot-spam': every cell is reached either by continuing a stroke or by a
            lift that lands on a run-start (>=2 cells), never as an isolated teleport dot;
          * NO overdraw: every cell is visited EXACTLY once — the pen never travels back
            over already-painted cells (unlike A* bridging / Euler retrace).
        The price is a handful of pen-lifts (far fewer than the stack-DFS's backtrack
        jumps), which is topologically unavoidable on concave regions without overdraw.

        Honors drawn_mask (painted cells are walls) like the other traversals. Snakes
        along the region's LONG axis (wide -> horizontal lines, tall -> vertical) to look
        like the previous fill and minimise turns/dead-ends. Returns pen-visit order."""
        h, w = mask_for_color.shape
        if not (0 <= start_row < h and 0 <= start_col < w) or mask_for_color[start_row, start_col] != 255:
            return []
        drawn = self.drawn_mask
        if drawn is not None and drawn.shape != mask_for_color.shape:
            drawn = None  # caller passed a non-base grid (e.g. the dynamic-brush coarse tier grid)

        def passable(r, c):
            return mask_for_color[r, c] == 255 and not (drawn is not None and drawn[r, c])

        if not passable(start_row, start_col):
            return []
        sr, sc = int(start_row), int(start_col)

        if bool(getattr(self, "route_kernels_numba_enabled", True)):
            try:
                fast = snake_order_fast(
                    mask_for_color, drawn, sr, sc,
                    strip_axis=bool(getattr(self, "snake_turn_minimize_enabled", False)))
                if fast is not None:
                    return fast
            except Exception:
                log.debug("numba snake kernel failed; python fallback", exc_info=True)

        # Flood the connected, not-yet-drawn component once: gives its size (loop bound)
        # and bounding box (to pick the snake axis).
        comp_visited = np.zeros((h, w), dtype=bool)
        stack = [(sr, sc)]
        comp_visited[sr, sc] = True
        total = 0
        min_r, max_r, min_c, max_c = sr, sr, sc, sc
        while stack:
            r, c = stack.pop()
            total += 1
            if r < min_r: min_r = r
            if r > max_r: max_r = r
            if c < min_c: min_c = c
            if c > max_c: max_c = c
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = r + dr, c + dc
                if 0 <= nr < h and 0 <= nc < w and not comp_visited[nr, nc] and passable(nr, nc):
                    comp_visited[nr, nc] = True
                    stack.append((nr, nc))
        if total <= 0:
            return []
        horizontal_first = (max_c - min_c) >= (max_r - min_r)
        if bool(getattr(self, "snake_turn_minimize_enabled", False)):
            # TMSTC* idea: pick the snake axis by STRIP COUNT, not bbox aspect —
            # fewer maximal strips along an axis = longer strokes and fewer
            # turns/dead-ends on striped/concave shapes the bbox rule misjudges.
            try:
                h_strips = int((comp_visited[:, 1:] & ~comp_visited[:, :-1]).sum()
                               + comp_visited[:, 0].sum())
                v_strips = int((comp_visited[1:, :] & ~comp_visited[:-1, :]).sum()
                               + comp_visited[0, :].sum())
                horizontal_first = h_strips <= v_strips
            except Exception:
                log.debug("strip-count axis pick failed; using bbox aspect", exc_info=True)
        if horizontal_first:
            base = [(0, 1), (0, -1), (1, 0), (-1, 0)]   # wide region: horizontal first
        else:
            base = [(1, 0), (-1, 0), (0, 1), (0, -1)]   # tall region: vertical first

        def dir_priority(last):
            if last is None:
                return base
            rev = (-last[0], -last[1])
            return [last] + [d for d in base if d != last and d != rev] + [rev]

        def nearest_run_start(cur):
            """Nearest unvisited passable cell OF THIS COMPONENT, PREFERRING one that
            still has an unvisited neighbour (a real run-start). Only a fully-surrounded
            leftover (the unavoidable single dot) is taken when nothing better remains.
            The lift is pen-up, so the BFS may cross anything — but candidates MUST be
            gated on comp_visited: `total` counts only this component's cells, so leaking
            into another same-color region exhausts the count early and leaves THIS
            region with unpainted holes (live bug: 'не дорисовывает до конца')."""
            from collections import deque
            seen = np.zeros((h, w), dtype=bool)
            dq = deque([cur])
            seen[cur[0], cur[1]] = True
            dirs8 = ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (-1, 1), (1, -1), (1, 1))
            fallback = None
            while dq:
                r, c = dq.popleft()
                for dr, dc in dirs8:
                    nr, nc = r + dr, c + dc
                    if not (0 <= nr < h and 0 <= nc < w) or seen[nr, nc]:
                        continue
                    seen[nr, nc] = True
                    if comp_visited[nr, nc] and passable(nr, nc) and not snake_visited[nr, nc]:
                        for ar, ac in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                            pr, pc = nr + ar, nc + ac
                            if 0 <= pr < h and 0 <= pc < w and passable(pr, pc) and not snake_visited[pr, pc]:
                                return (nr, nc)             # a run-start: best choice
                        if fallback is None:
                            fallback = (nr, nc)             # remember nearest lone cell
                    else:
                        dq.append((nr, nc))
            return fallback

        snake_visited = np.zeros((h, w), dtype=bool)
        order: list[tuple[int, int]] = [(sr, sc)]
        snake_visited[sr, sc] = True
        cur = (sr, sc)
        last = None
        count = 1
        while count < total:
            moved = False
            for d in dir_priority(last):
                nr, nc = cur[0] + d[0], cur[1] + d[1]
                if 0 <= nr < h and 0 <= nc < w and passable(nr, nc) and not snake_visited[nr, nc]:
                    snake_visited[nr, nc] = True
                    cur = (nr, nc); order.append(cur); last = d; count += 1; moved = True
                    break
            if moved:
                continue
            nxt = nearest_run_start(cur)
            if nxt is None:
                break
            snake_visited[nxt[0], nxt[1]] = True
            cur = nxt; order.append(cur); last = None; count += 1
        return order

    def _gilbert_cell_order(self, mask_for_color, start_row, start_col):
        """Generalized-Hilbert visit order of the connected, not-yet-drawn
        component containing the start cell. Same passability rules as the
        snake (painted cells are walls). Returns [] to let the caller fall
        back to the default traversal."""
        h, w = mask_for_color.shape
        if not (0 <= start_row < h and 0 <= start_col < w) or mask_for_color[start_row, start_col] != 255:
            return []
        drawn = self.drawn_mask
        if drawn is not None and drawn.shape != mask_for_color.shape:
            drawn = None  # non-base grid (e.g. the dynamic-brush coarse tier grid)

        def passable(r, c):
            return mask_for_color[r, c] == 255 and not (drawn is not None and drawn[r, c])

        if not passable(start_row, start_col):
            return []
        comp_visited = np.zeros((h, w), dtype=bool)
        stack = [(int(start_row), int(start_col))]
        comp_visited[start_row, start_col] = True
        cells: list[tuple[int, int]] = []
        while stack:
            r, c = stack.pop()
            cells.append((r, c))
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = r + dr, c + dc
                if 0 <= nr < h and 0 <= nc < w and not comp_visited[nr, nc] and passable(nr, nc):
                    comp_visited[nr, nc] = True
                    stack.append((nr, nc))
        try:
            return gilbert_order_for_cells(cells)
        except Exception:
            log.debug("gilbert order failed; falling back to default traversal", exc_info=True)
            return []

    def _fermat_cell_order(self, mask_for_color, start_row, start_col):
        """Contour-parallel ('fermat') visit order of the connected, not-yet-drawn
        component, with a built-in SELECTOR: the result is compared against the
        greedy snake by lift count and the better order wins (tie -> fermat). The
        mode therefore can never draw worse than the shipped snake — the safety
        the user's verdicts demand. Returns [] to fall back entirely."""
        h, w = mask_for_color.shape
        if not (0 <= start_row < h and 0 <= start_col < w) or mask_for_color[start_row, start_col] != 255:
            return []
        drawn = self.drawn_mask
        if drawn is not None and drawn.shape != mask_for_color.shape:
            drawn = None  # non-base grid (e.g. the dynamic-brush coarse tier grid)

        def passable(r, c):
            return mask_for_color[r, c] == 255 and not (drawn is not None and drawn[r, c])

        if not passable(start_row, start_col):
            return []
        comp = np.zeros((h, w), dtype=bool)
        stack = [(int(start_row), int(start_col))]
        comp[start_row, start_col] = True
        while stack:
            r, c = stack.pop()
            for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
                nr, nc = r + dr, c + dc
                if 0 <= nr < h and 0 <= nc < w and not comp[nr, nc] and passable(nr, nc):
                    comp[nr, nc] = True
                    stack.append((nr, nc))
        try:
            spiral = fermat_cell_order(comp, (start_row, start_col))
        except Exception:
            log.debug("fermat order failed; falling back", exc_info=True)
            return []
        if not spiral:
            return []
        try:
            snake = self._dfs_greedy_snake_order(mask_for_color, start_row, start_col)
            if snake and route_metrics(snake)["lifts"] < route_metrics(spiral)["lifts"]:
                return snake
        except Exception:
            log.debug("fermat selector failed; keeping the spiral", exc_info=True)
        return spiral

    # Farthest in-colour detour (cells, Manhattan) tried instead of a pen lift.
    _CHEAP_BRIDGE_MAX_CELLS = 12

    def _pen_lift_cost(self) -> float:
        """Seconds one lift costs with the current pauses (release, move, press)."""
        return (2.0 * float(getattr(self, "pen_settle_delay", 0.0) or 0.0)
                + 2.0 * float(getattr(self, "pen_button_delay", 0.0) or 0.0)
                + float(getattr(self, "mouse_release_settle", 0.0) or 0.0))

    def _cheap_bridge(self, start, goal, passable, lift_cost: float, turn_cost: float):
        """An in-colour path from start to goal when drawing it costs less than a
        lift: 4-connected only (never clips a foreign corner, whatever the stamp),
        counted in straight strokes. None = lift instead."""
        distance = abs(int(start[0]) - int(goal[0])) + abs(int(start[1]) - int(goal[1]))
        if distance > self._CHEAP_BRIDGE_MAX_CELLS or turn_cost * 2 > lift_cost:
            return None
        bridge = astar_bridge(start, goal, passable, max_expansions=400, diagonal=False)
        if not bridge:
            return None
        strokes = sum(collinear_vertices_mask([start, *bridge, goal])) - 1
        return bridge if strokes * turn_cost < lift_cost else None

    def _draw_cell_sequence(self, cells, mask_for_color, x0, y0):
        """Render an ordered cell path with optional run-length merge (move the
        pen only at direction-change vertices -> identical pixels, far fewer mouse
        moves) and/or A* bridging (route backtrack jumps through ON-COLOR cells
        instead of cutting a straight line across other colors). Marks every cell
        drawn. Mirrors dfs_4dir_draw's pen-down / pause-resume handling."""
        if not cells or self.drawn_mask is None:
            return
        brush = self.brush_size
        h_mask, w_mask = mask_for_color.shape
        region_w, region_h = self._region_dimensions(fallback_cols=w_mask, fallback_rows=h_mask, brush=brush)
        run_merge = bool(getattr(self, "run_length_merge_enabled", False))
        astar_on = bool(getattr(self, "astar_bridge_enabled", False))
        cheap_bridges = not astar_on and bool(getattr(self, "cheap_bridges_enabled", True))
        passable = (mask_for_color == 255) if (astar_on or cheap_bridges) else None
        lift_cost = self._pen_lift_cost()
        turn_cost = max(1e-4, float(getattr(self, "draw_delay", 0.0) or 0.0))
        vertex_flags = collinear_vertices_mask(cells) if run_merge else None
        motion_on = bool(getattr(self, "motion_profile_enabled", False))
        motion_corner = float(getattr(self, "motion_corner_factor", 1.0))
        motion_vmax = float(getattr(self, "motion_vmax_factor", 1.6))
        pace_prev_dir = None  # direction of the previous PACED stroke (None after lifts)

        def center(rr, cc):
            return (self._cell_center_axis(x0, region_w, cc, brush),
                    self._cell_center_axis(y0, region_h, rr, brush))

        sr, sc = cells[0]
        start_x, start_y = center(sr, sc)
        mouse_is_down = False
        try:
            self._move_abs(start_x, start_y); time.sleep(self.pen_settle_delay)
            self._pen_down(); time.sleep(self.pen_settle_delay); mouse_is_down = True
        except Exception:
            self.stop_flag = True
            return
        if not self.drawn_mask[sr, sc]:
            self.drawn_mask[sr, sc] = True
            self._notify_pixel_progress()
        last_x, last_y = start_x, start_y
        prev_cell = (sr, sc)
        resumed_just_now = False

        def _mark(rr, cc):
            if not self.drawn_mask[rr, cc]:
                self.drawn_mask[rr, cc] = True
                self._notify_pixel_progress()

        def _mark_range(lo, hi):
            # Mark cells[lo..hi] inclusive — only AFTER a real stroke has actually
            # covered them. Deferred (collinear-interior) cells are therefore never
            # marked-but-unpainted, so a stop mid-run leaves no phantom 'done' gap.
            for k in range(lo, hi + 1):
                _mark(*cells[k])

        last_drawn_idx = 0  # cells[0] painted+marked above
        try:
            for idx in range(1, len(cells)):
                if self.stop_flag or self._automation_cancelled():
                    break
                r, c = cells[idx]
                if not self.drawing_enabled:
                    if mouse_is_down:
                        self._mouse_up_with_settle()
                        mouse_is_down = False   # always sync to pen-up so resume re-presses
                    while not self.drawing_enabled and not self.stop_flag:
                        time.sleep(0.01)
                    if self.drawing_enabled and not self.stop_flag:
                        resumed_just_now = True
                if self.stop_flag or self._automation_cancelled():
                    break
                nx, ny = center(r, c)
                if resumed_just_now or not mouse_is_down:
                    try:
                        self._move_abs(last_x, last_y); time.sleep(self.pen_repress_settle)
                        self._pen_down(); time.sleep(self.pen_settle_delay); mouse_is_down = True
                    except Exception:
                        self.stop_flag = True
                        break
                    resumed_just_now = False
                is_jump = not cells_adjacent(prev_cell, (r, c))
                drew_target = False
                if is_jump:
                    pace_prev_dir = None  # the pen lifts/bridges: next stroke enters at corner speed
                    # A non-adjacent jump must NEVER be drawn as a straight line across
                    # foreign colors/background. With A* on, route through ON-COLOR cells;
                    # if A* can't (disconnected region / over budget -> None) or A* is off,
                    # TELEPORT (pen up -> move -> pen down) instead of cutting a stray stroke.
                    bridge = None
                    if astar_on and passable is not None:
                        bridge = astar_bridge(prev_cell, (r, c), passable)
                    elif cheap_bridges and passable is not None:
                        bridge = self._cheap_bridge(prev_cell, (r, c), passable, lift_cost, turn_cost)
                    self._tm_count("bridges" if bridge else "teleports")
                    if bridge:
                        # Straight stretches of the bridge are one stroke each: a
                        # move per cell cost a turn pause per cell and made bridges
                        # slower than lifts (live Paint: Spongebob 21 -> 33 s).
                        chain = [prev_cell, *bridge, (r, c)]
                        keep = collinear_vertices_mask(chain)
                        for (br, bc), is_vertex in zip(chain[1:-1], keep[1:-1]):
                            if self.stop_flag or self._automation_cancelled():
                                break
                            if is_vertex:
                                bx, by = center(br, bc)
                                self.draw_line(last_x, last_y, bx, by)
                                last_x, last_y = bx, by
                        for br, bc in bridge:
                            _mark(br, bc)   # bridge cells get physically painted -> mark them
                        if not (self.stop_flag or self._automation_cancelled()):
                            self.draw_line(last_x, last_y, nx, ny)
                            last_x, last_y = nx, ny
                            drew_target = True
                    else:
                        if mouse_is_down:
                            self._mouse_up_with_settle(); mouse_is_down = False
                        try:
                            self._move_abs(nx, ny); time.sleep(self.pen_settle_delay)
                            self._pen_down(); time.sleep(self.pen_settle_delay); mouse_is_down = True
                            last_x, last_y = nx, ny
                            drew_target = True
                        except Exception:
                            self.stop_flag = True
                            break
                elif vertex_flags is None or vertex_flags[idx]:
                    if motion_on:
                        cur_dir = (ny - last_y, nx - last_x)
                        entry = junction_speed_factor(pace_prev_dir, cur_dir, motion_corner, motion_vmax)
                        self.draw_line(last_x, last_y, nx, ny, pace=(entry, motion_corner))
                        pace_prev_dir = cur_dir
                    else:
                        self.draw_line(last_x, last_y, nx, ny)
                    last_x, last_y = nx, ny
                    drew_target = True
                # else: collinear interior cell of a straight run -> pen deferred; its
                #       pixel (and its drawn_mask mark) lands when the next vertex stroke
                #       covers it (handled by _mark_range below).
                prev_cell = (r, c)
                if drew_target:
                    _mark_range(last_drawn_idx + 1, idx)  # covers any deferred run + this cell
                    last_drawn_idx = idx
            if not self.stop_flag and not self._automation_cancelled() and mouse_is_down \
                    and last_drawn_idx < len(cells) - 1:
                fx, fy = center(*cells[-1])  # flush a trailing deferred run
                if (fx, fy) != (last_x, last_y):
                    self.draw_line(last_x, last_y, fx, fy)
                _mark_range(last_drawn_idx + 1, len(cells) - 1)
                last_drawn_idx = len(cells) - 1
        finally:
            # The pen's REAL exit point (last cell a stroke actually reached, even
            # on stop/pause) — the entry/exit anchor consumer checks the shape so
            # foreign grids (dynamic-brush coarse tier) are never mixed in.
            self._route_exit_cell = (tuple(cells[last_drawn_idx]), mask_for_color.shape)
            self._notify_pixel_progress(force=True)  # flush the throttled preview
            if mouse_is_down:
                self._mouse_up_with_settle()

    def dfs_4dir_draw(self, mask_for_color, start_row, start_col, x0, y0):
        if self.drawn_mask is None: return
        brush = self.brush_size; h_mask, w_mask = mask_for_color.shape
        if not (0 <= start_row < h_mask and 0 <= start_col < w_mask) or \
           mask_for_color[start_row, start_col] != 255 or \
           self.drawn_mask[start_row, start_col]:
            return
        run_length = bool(getattr(self, "run_length_merge_enabled", False))
        astar_on = bool(getattr(self, "astar_bridge_enabled", False))
        mode = self._normalize_fill_traversal_mode(getattr(self, "fill_traversal_mode", "auto"))
        # Registry of experimental visit-order builders (REVIEW gap #3): adding a
        # strategy = one entry here, not another if-elif. Order choices must work
        # with run-length/A* both off too (the gilbert no-op lesson).
        order_builders = {
            "gilbert": self._gilbert_cell_order,
            "fermat": self._fermat_cell_order,
        }
        builder = order_builders.get(mode)
        if run_length or astar_on or builder is not None:
            order = None
            if builder is not None:
                order = builder(mask_for_color, start_row, start_col)
            if not order:
                if astar_on:
                    # A* on: pop order + bridge backtracks through on-color cells.
                    order = self._dfs_traversal_order(mask_for_color, start_row, start_col)
                else:
                    # A* off (no overdraw wanted): contiguous snake — long strokes, no
                    # scattered teleport dots, every cell drawn exactly once. The pen lifts
                    # a handful of times to the next stroke's start instead of dotting.
                    order = self._dfs_greedy_snake_order(mask_for_color, start_row, start_col)
            if order and bool(getattr(self, "fill_route_polish_enabled", False)):
                try:
                    # LS-MCPP-lite: re-chain the lift-separated runs (same cells,
                    # cheaper lifts); guaranteed never worse than the input order.
                    order = polish_cell_order(order)
                except Exception:
                    log.debug("route polish failed; keeping the original order", exc_info=True)
            self._draw_cell_sequence(order, mask_for_color, x0, y0)
            return
        # DFS pop order can jump across branches. Use the same renderer as the
        # planned routes: it lifts between non-adjacent cells, releases in finally,
        # and preserves pause/resume and truthful progress. Keep the visit order.
        order = self._dfs_traversal_order(mask_for_color, start_row, start_col)
        self._draw_cell_sequence(order, mask_for_color, x0, y0)

    def _collect_component_cells(self, component_mask_bool: np.ndarray) -> list[tuple[int, int]]:
        coords = np.argwhere(component_mask_bool)
        return [ (int(r), int(c)) for r, c in coords ]

    def _build_component_adjacency(self, component_cells: list[tuple[int, int]], neighbor_offsets: list[tuple[int, int]]):
        if not component_cells:
            return {}
        cell_set = {tuple(cell) for cell in component_cells}
        adjacency: dict[tuple[int, int], list[tuple[int, int]]] = {cell: [] for cell in cell_set}
        rows = [r for r, _ in cell_set]
        cols = [c for _, c in cell_set]
        min_r, max_r = min(rows), max(rows)
        min_c, max_c = min(cols), max(cols)
        centroid_r = sum(rows) / len(rows)
        centroid_c = sum(cols) / len(cols)

        def _neighbor_priority(cell: tuple[int, int]) -> tuple[float, float, int, int]:
            cr, cc = cell
            boundary = min(cr - min_r, max_r - cr, cc - min_c, max_c - cc)
            centroid_bias = abs(cr - centroid_r) + abs(cc - centroid_c)
            return (boundary, centroid_bias, cr, cc)

        for r, c in cell_set:
            for dr, dc in neighbor_offsets:
                nb = (r + dr, c + dc)
                if nb in adjacency:
                    adjacency[(r, c)].append(nb)
            adjacency[(r, c)].sort(key=_neighbor_priority)
        return adjacency

    def _build_euler_route(self, adjacency: dict[tuple[int, int], list[tuple[int, int]]],
                           preferred_start: tuple[int, int] | None = None) -> list[tuple[int, int]] | None:
        if not adjacency:
            return None

        def _canonical_edge(a: tuple[int, int], b: tuple[int, int]) -> tuple[tuple[int, int], tuple[int, int]]:
            return (a, b) if a <= b else (b, a)

        def _find_odd_vertices(adj: dict[tuple[int, int], list[tuple[int, int]]]) -> list[tuple[int, int]]:
            return [node for node, neigh in adj.items() if len(neigh) % 2 == 1]

        def _bfs_tree(start: tuple[int, int], adj: dict[tuple[int, int], list[tuple[int, int]]]):
            parents: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
            dists: dict[tuple[int, int], int] = {start: 0}
            queue = deque([start])
            while queue:
                v = queue.popleft()
                for nb in adj.get(v, []):
                    if nb not in parents:
                        parents[nb] = v
                        dists[nb] = dists[v] + 1
                        queue.append(nb)
            return parents, dists

        def _reconstruct_path(target: tuple[int, int], parents: dict[tuple[int, int], tuple[int, int] | None]) -> list[tuple[int, int]] | None:
            if target not in parents:
                return None
            path = [target]
            while parents[path[-1]] is not None:
                path.append(parents[path[-1]])
            path.reverse()
            return path

        def _pair_odd_vertices(adj: dict[tuple[int, int], list[tuple[int, int]]], odd_vertices: list[tuple[int, int]]) -> bool:
            odd_count = len(odd_vertices)
            if odd_count in (0, 2):
                return True
            if odd_count % 2 == 1:
                return False
            limit = getattr(self, "eulerization_max_odd_vertices", 12)
            try:
                limit = max(0, int(limit))
            except Exception:
                limit = 12
            if limit and odd_count > limit:
                # Beyond the exact-DP budget: greedily pair each odd vertex with its
                # NEAREST unmatched peer (early-exit BFS) and duplicate the connecting
                # path. Interior path vertices gain +2 degree (parity kept), endpoints
                # +1 (odd -> even). The last two stay unmatched as the route endpoints.
                # Opt-in: flag off = legacy give-up (caller falls back to DFS/snake).
                if not bool(getattr(self, "euler_greedy_pairing_enabled", False)):
                    return False
                return self._greedy_pair_odd_vertices(adj, odd_vertices)
            pair_dist: dict[tuple[int, int], int] = {}
            pair_paths: dict[tuple[int, int], list[tuple[int, int]]] = {}
            for idx, node in enumerate(odd_vertices):
                parents, dists = _bfs_tree(node, adj)
                for jdx in range(idx + 1, odd_count):
                    target = odd_vertices[jdx]
                    if target not in dists:
                        continue
                    path = _reconstruct_path(target, parents)
                    if not path:
                        continue
                    pair_dist[(idx, jdx)] = dists[target]
                    pair_paths[(idx, jdx)] = path
            total_pairs = (odd_count * (odd_count - 1)) // 2
            if len(pair_dist) < total_pairs:
                return False
            full_mask = (1 << odd_count) - 1
            memo: dict[int, tuple[int, list[tuple[int, int]]]] = {}

            def _solve(mask: int) -> tuple[int, list[tuple[int, int]]]:
                if mask == 0:
                    return 0, []
                if mask in memo:
                    return memo[mask]
                first_bit = mask & -mask
                first_idx = first_bit.bit_length() - 1
                best_cost = math.inf
                best_pairs: list[tuple[int, int]] | None = None
                remaining = mask ^ (1 << first_idx)
                temp = remaining
                while temp:
                    partner_bit = temp & -temp
                    partner_idx = partner_bit.bit_length() - 1
                    key = (first_idx, partner_idx) if first_idx < partner_idx else (partner_idx, first_idx)
                    dist = pair_dist.get(key)
                    if dist is not None:
                        next_mask = remaining ^ partner_bit
                        sub_cost, sub_pairs = _solve(next_mask)
                        if sub_cost != math.inf and dist + sub_cost < best_cost:
                            best_cost = dist + sub_cost
                            best_pairs = [(first_idx, partner_idx)] + sub_pairs
                    temp ^= partner_bit
                if best_pairs is None:
                    memo[mask] = (math.inf, [])
                else:
                    memo[mask] = (best_cost, best_pairs)
                return memo[mask]

            total_cost, pairing = _solve(full_mask)
            if total_cost == math.inf or not pairing:
                return False
            for i, j in pairing:
                key = (i, j) if i < j else (j, i)
                path = pair_paths.get(key)
                if not path or len(path) < 2:
                    return False
                for a, b in zip(path, path[1:]):
                    adj.setdefault(a, []).append(b)
                    adj.setdefault(b, []).append(a)
            return True

        local_adj = {node: list(neigh) for node, neigh in adjacency.items()}
        odd_vertices = _find_odd_vertices(local_adj)
        if not _pair_odd_vertices(local_adj, odd_vertices):
            return None
        updated_odds = _find_odd_vertices(local_adj)
        if updated_odds and len(updated_odds) not in (0, 2):
            return None
        def _nearest_to_preferred(cands):
            # Entry/exit bias: among equally valid route starts, take the one
            # closest to where the pen already is. None => legacy first pick.
            if preferred_start is None:
                return None
            pr, pc = preferred_start
            try:
                return min(cands, key=lambda v: (v[0] - pr) ** 2 + (v[1] - pc) ** 2)
            except ValueError:
                return None

        total_edges = sum(len(neigh) for neigh in local_adj.values()) // 2
        if total_edges == 0:
            start_vertex = updated_odds[0] if updated_odds else next(iter(local_adj))
            return [start_vertex]
        if len(updated_odds) == 2:
            start_vertex = _nearest_to_preferred(updated_odds) or updated_odds[0]
        else:
            start_vertex = _nearest_to_preferred(local_adj.keys()) or next(iter(local_adj))
        edge_capacity: dict[tuple[tuple[int, int], tuple[int, int]], int] = {}
        for node, neighbors in local_adj.items():
            for nb in neighbors:
                key = _canonical_edge(node, nb)
                edge_capacity[key] = edge_capacity.get(key, 0) + 1
        for key in list(edge_capacity.keys()):
            edge_capacity[key] //= 2
            if edge_capacity[key] <= 0:
                edge_capacity.pop(key, None)
        edge_usage: dict[tuple[tuple[int, int], tuple[int, int]], int] = {}
        stack: list[tuple[int, int]] = [start_vertex]
        route: list[tuple[int, int]] = []
        while stack:
            v = stack[-1]
            neighbors = local_adj.get(v)
            if neighbors:
                edge_used = False
                while neighbors:
                    u = neighbors.pop()
                    key = _canonical_edge(v, u)
                    capacity = edge_capacity.get(key)
                    if not capacity:
                        continue
                    used = edge_usage.get(key, 0)
                    if used >= capacity:
                        continue
                    edge_usage[key] = used + 1
                    stack.append(u)
                    edge_used = True
                    break
                if not edge_used:
                    route.append(stack.pop())
            else:
                route.append(stack.pop())
        if len(route) != total_edges + 1:
            return None
        route.reverse()
        simplified = self._simplify_route(route)
        if not simplified:
            return None
        return simplified

    def _greedy_pair_odd_vertices(self, adj, odd_vertices) -> bool:
        """Eulerize `adj` in place when there are too many odd vertices for the
        exact DP: repeatedly BFS from an unmatched odd vertex to its NEAREST
        unmatched peer and duplicate that path's edges. Heuristic (not min-weight)
        but linear-ish and never gives up on a connected component. Leaves exactly
        two vertices odd — they become the open route's endpoints."""
        pending = set(odd_vertices)
        while len(pending) > 2:
            start = min(pending)
            parents: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
            queue = deque([start])
            found = None
            while queue and found is None:
                v = queue.popleft()
                for nb in adj.get(v, []):
                    if nb in parents:
                        continue
                    parents[nb] = v
                    if nb in pending:
                        found = nb
                        break
                    queue.append(nb)
            if found is None:
                return False  # disconnected adjacency: let the caller fall back
            path = [found]
            while parents[path[-1]] is not None:
                path.append(parents[path[-1]])
            path.reverse()
            if len(path) < 2:
                return False
            for a, b in zip(path, path[1:]):
                adj.setdefault(a, []).append(b)
                adj.setdefault(b, []).append(a)
            pending.discard(start)
            pending.discard(found)
        return True

    def _simplify_route(self, route: list[tuple[int, int]]) -> list[tuple[int, int]]:
        if not route:
            return []
        cleaned: list[tuple[int, int]] = []
        visit_counts: dict[tuple[int, int], int] = {}
        for cell in route:
            if cleaned and cell == cleaned[-1]:
                continue
            while len(cleaned) >= 2 and cell == cleaned[-2]:
                mid = cleaned[-1]
                if visit_counts.get(mid, 0) <= 1:
                    break
                cleaned.pop()
                visit_counts[mid] = visit_counts.get(mid, 0) - 1
            if cleaned and cell == cleaned[-1]:
                continue
            cleaned.append(cell)
            visit_counts[cell] = visit_counts.get(cell, 0) + 1
        return cleaned

    def _notify_pixel_progress(self, force: bool = False):
        """Rate-limited pixel_update_callback (<=20 Hz + force-flush at region
        ends). The previous per-cell Qt event flood made the GUI thread part of
        the drawing hot loop; drawn_mask is still updated per cell — only the
        NOTIFICATION is throttled, so progress numbers lose nothing."""
        cb = self.pixel_update_callback
        if cb is None:
            return
        now = time.monotonic()
        if not force and (now - getattr(self, "_pixel_notify_last", 0.0)) < 0.05:
            return
        self._pixel_notify_last = now
        cb()

    def _mark_route_cell(self, row: int, col: int):
        if self.drawn_mask is None:
            return
        if 0 <= row < self.drawn_mask.shape[0] and 0 <= col < self.drawn_mask.shape[1]:
            if not self.drawn_mask[row, col]:
                self.drawn_mask[row, col] = True
                self._notify_pixel_progress()

    def _draw_route_with_brush(self, route_cells: list[tuple[int, int]], x0: int, y0: int, mask_shape: tuple[int, int],
                               component_mask=None) -> bool:
        if not route_cells:
            return True
        if self.drawn_mask is None:
            return False
        brush = self.brush_size
        h_mask, w_mask = mask_shape
        region_w, region_h = self._region_dimensions(fallback_cols=w_mask, fallback_rows=h_mask, brush=brush)
        first_r, first_c = route_cells[0]
        start_x = self._cell_center_axis(x0, region_w, first_c, brush)
        start_y = self._cell_center_axis(y0, region_h, first_r, brush)
        mouse_is_down = False
        try:
            self._move_abs(start_x, start_y); time.sleep(self.pen_settle_delay)
            self._pen_down(); time.sleep(self.pen_settle_delay); mouse_is_down = True
        except Exception:
            self.stop_flag = True
            return False
        self._mark_route_cell(first_r, first_c)
        self._route_exit_cell = ((int(first_r), int(first_c)), tuple(mask_shape))
        if len(route_cells) == 1:
            try:
                span = max(1, int(round(max(1, brush) * 0.5)))
                min_x = int(x0)
                min_y = int(y0)
                max_x = int(x0) + max(1, int(region_w)) - 1
                max_y = int(y0) + max(1, int(region_h)) - 1

                def _span_cell_ok(dr: int, dc: int) -> bool:
                    # With brush > 1 the half-brush stroke stays inside the cell's
                    # own footprint. With brush == 1 it lands on the NEIGHBOUR
                    # CELL — painting it sprayed stray ink onto other colors
                    # (live-class defect: 37 stray px on the noisy bench mask).
                    # Allow the stroke only when that neighbour is ours.
                    if brush > 1:
                        return True
                    if component_mask is None:
                        return False
                    rr, cc = first_r + dr, first_c + dc
                    return (0 <= rr < component_mask.shape[0]
                            and 0 <= cc < component_mask.shape[1]
                            and bool(component_mask[rr, cc]))

                end_x = start_x
                end_y = start_y
                if start_x + span <= max_x and _span_cell_ok(0, 1):
                    end_x = start_x + span
                elif start_x - span >= min_x and _span_cell_ok(0, -1):
                    end_x = start_x - span
                elif start_y + span <= max_y and _span_cell_ok(1, 0):
                    end_y = start_y + span
                elif start_y - span >= min_y and _span_cell_ok(-1, 0):
                    end_y = start_y - span
                if end_x != start_x or end_y != start_y:
                    self.draw_line(start_x, start_y, end_x, end_y)
                else:
                    # isolated 1px cell: a zero-length stroke still produces the
                    # press+move pattern for targets that ignore a bare click
                    self.draw_line(start_x, start_y, start_x, start_y)
            except Exception:
                log.debug('ignored exception in span = max(1, int(round(max(1, brush) * 0.5)))', exc_info=True)
            if mouse_is_down:
                self._mouse_up_with_settle()
            return True
        last_x, last_y = start_x, start_y
        resumed_just_now = False
        # Run-length merge: only MOVE the pen at direction-change vertices; the
        # straight line between two vertices covers every intermediate cell, so we
        # still mark each cell but send far fewer mouse moves. None => move every cell.
        vertex_flags = collinear_vertices_mask(route_cells) if getattr(self, "run_length_merge_enabled", False) else None
        motion_on = bool(getattr(self, "motion_profile_enabled", False))
        motion_corner = float(getattr(self, "motion_corner_factor", 1.0))
        motion_vmax = float(getattr(self, "motion_vmax_factor", 1.6))
        pace_prev_dir = None
        for idx in range(1, len(route_cells)):
            r, c = route_cells[idx]
            if self._automation_cancelled():
                break
            if not self.drawing_enabled:
                if mouse_is_down:
                    self._mouse_up_with_settle()
                    mouse_is_down = False   # always sync to pen-up so resume re-presses
                while not self.drawing_enabled and not self.stop_flag:
                    time.sleep(0.01)
                if self.drawing_enabled and not self.stop_flag:
                    resumed_just_now = True
            if self._automation_cancelled() or self.stop_flag:
                break
            next_x = self._cell_center_axis(x0, region_w, c, brush)
            next_y = self._cell_center_axis(y0, region_h, r, brush)
            if resumed_just_now:
                try:
                    self._move_abs(last_x, last_y); time.sleep(self.pen_repress_settle)
                    self._pen_down(); time.sleep(self.pen_settle_delay); mouse_is_down = True
                except Exception:
                    self.stop_flag = True
                    break
                resumed_just_now = False
            elif not mouse_is_down:
                try:
                    self._move_abs(last_x, last_y); time.sleep(self.pen_repress_settle)
                    self._pen_down(); time.sleep(self.pen_settle_delay); mouse_is_down = True
                except Exception:
                    self.stop_flag = True
                    break
            if vertex_flags is None or vertex_flags[idx]:
                if motion_on:
                    cur_dir = (next_y - last_y, next_x - last_x)
                    entry = junction_speed_factor(pace_prev_dir, cur_dir, motion_corner, motion_vmax)
                    self.draw_line(last_x, last_y, next_x, next_y, pace=(entry, motion_corner))
                    pace_prev_dir = cur_dir
                else:
                    self.draw_line(last_x, last_y, next_x, next_y)
                if self._automation_cancelled() or self.stop_flag:
                    break
                last_x, last_y = next_x, next_y
                # the pen's real position = the last vertex a stroke reached
                self._route_exit_cell = ((int(r), int(c)), tuple(mask_shape))
            self._mark_route_cell(r, c)
        if mouse_is_down:
            self._mouse_up_with_settle()
        return not self._automation_cancelled() and not self.stop_flag

    def _draw_component_with_route(
        self,
        component_mask_bool: np.ndarray,
        neighbor_offsets: list[tuple[int, int]],
        x0: int,
        y0: int,
        fallback_draw_fn,
        route_draw_fn=None,
    ):
        if component_mask_bool is None or not np.any(component_mask_bool):
            return True
        try:
            # When A* bridging is on and no route renderer was supplied (the plain DFS
            # fill, route_draw_fn=None), a built Euler route would be discarded anyway
            # in favour of the DFS/A* path -> short-circuit BEFORE the (non-trivial)
            # collect/adjacency/eulerise work instead of computing then throwing it away.
            if getattr(self, "astar_bridge_enabled", False) and not callable(route_draw_fn):
                return fallback_draw_fn()
            # An explicit visit-order mode (gilbert/fermat) owns the fill: skip the
            # Euler route for plain fills, or it silently wins on every simple region
            # and the toggle has no visible effect (outline modes keep their Euler).
            if not callable(route_draw_fn) and \
                    self._normalize_fill_traversal_mode(getattr(self, "fill_traversal_mode", "auto")) != "auto":
                return fallback_draw_fn()
            # Cheap parity pre-check (numpy) BEFORE the expensive python adjacency:
            # when the Euler route is GUARANTEED to fail (odd-degree vertices over
            # the DP limit and greedy pairing off), skip straight to the fallback.
            # Outcome-identical — such components paid the full adjacency build
            # only to get route=None. 4-dir grids only (the fill path).
            try:
                if set(tuple(d) for d in neighbor_offsets) == {(-1, 0), (1, 0), (0, -1), (0, 1)} \
                        and not bool(getattr(self, "euler_greedy_pairing_enabled", False)):
                    limit = getattr(self, "eulerization_max_odd_vertices", 12)
                    try:
                        limit = max(0, int(limit))
                    except Exception:
                        limit = 12
                    if limit:
                        comp_i8 = component_mask_bool.astype(np.int8)
                        deg = np.zeros_like(comp_i8)
                        deg[1:, :] += comp_i8[:-1, :]
                        deg[:-1, :] += comp_i8[1:, :]
                        deg[:, 1:] += comp_i8[:, :-1]
                        deg[:, :-1] += comp_i8[:, 1:]
                        odd = int(((deg % 2 == 1) & component_mask_bool).sum())
                        if odd > 2 and odd > limit:
                            return fallback_draw_fn()
            except Exception:
                log.debug("euler parity pre-check failed; building adjacency", exc_info=True)
            cells = self._collect_component_cells(component_mask_bool)
            adjacency = self._build_component_adjacency(cells, neighbor_offsets)
            preferred_start = None
            if not callable(route_draw_fn) and bool(getattr(self, "area_entry_exit_routing_enabled", False)):
                # Plain fills only: anchor is on the base grid; dynamic-brush
                # callers (route_draw_fn set) use coarse tier grids — skip there.
                anchor_xy = getattr(self, "_last_pen_cell", None)
                if anchor_xy is not None and component_mask_bool is not None:
                    try:
                        pr = int(round(float(anchor_xy[1])))
                        pc = int(round(float(anchor_xy[0])))
                        h_m, w_m = component_mask_bool.shape
                        preferred_start = (max(0, min(h_m - 1, pr)), max(0, min(w_m - 1, pc)))
                    except Exception:
                        preferred_start = None
            route = self._build_euler_route(adjacency, preferred_start=preferred_start)
            if route is None:
                return fallback_draw_fn()
            if callable(route_draw_fn):
                return bool(route_draw_fn(route, component_mask_bool, x0, y0))
            return self._draw_route_with_brush(route, x0, y0, component_mask_bool.shape,
                                               component_mask=component_mask_bool)
        except InputCancelled:
            raise                      # F4: stop, never redraw the region another way
        except Exception as exc:
            self._log(f"Euler route helper failure: {exc}", True)
            return fallback_draw_fn()

    def _ordered_component_labels(self, num_labels: int, stats, centroids=None, start_anchor=None) -> list[int]:
        """Order region labels (1..num_labels-1) for the current color.

        Default ('large_to_small' / 'small_to_large'): by pixel area, so regions of the
        SAME color are painted biggest- or smallest-first.
        'nearest': greedy nearest-neighbour chain over region centroids -- start at the
        region closest to `start_anchor` (where the pen finished the previous region/color,
        else the largest region), then always hop to the closest un-painted region
        regardless of size, minimising pen travel between regions.
        The palette/color order is handled separately."""
        labels = list(range(1, max(1, int(num_labels))))
        if stats is None or len(labels) <= 1:
            return labels
        seq = str(getattr(self, "area_sequence", "large_to_small") or "large_to_small")
        if seq in ("nearest", "nearest_neighbor", "nearest_neighbour"):
            try:
                ordered = self._nearest_neighbor_labels(labels, stats, centroids, start_anchor)
                if ordered:
                    return ordered
            except Exception:
                log.debug("nearest-neighbour area order failed; using area sort", exc_info=True)
        large_first = (seq == "large_to_small")
        try:
            labels.sort(key=lambda i: int(stats[i, cv2.CC_STAT_AREA]), reverse=large_first)
        except Exception:
            log.debug("ignored exception sorting component labels by area", exc_info=True)
        return labels

    def _nearest_neighbor_labels(self, labels, stats, centroids=None, start_anchor=None) -> list[int]:
        """Greedy nearest-neighbour ordering of component `labels` by centroid distance.

        `centroids`: the 4th return of cv2.connectedComponentsWithStats, indexed by label,
        each (x, y) in mask pixel coords. Falls back to the stats bounding-box centre when
        centroids are unavailable. `start_anchor`: an (x, y) point to start nearest from
        (the previous pen position); when None, starts from the largest region so the
        biggest fill anchors the tour. All distances are in the SAME (x, y) space -- never
        mixed with (row, col) -- so there is no axis-swap. Returns [] to fall back to the
        area-size sort."""
        n = len(labels)
        if n <= 1:
            return list(labels)
        # O(n^2) greedy. Vectorised numpy keeps the typical few-hundred/thousand case
        # sub-second; cap pathological counts to avoid stalling the draw thread.
        if n > 8000:
            log.debug("nearest-neighbour skipped: %d regions exceeds cap", n)
            return []
        pos = np.empty((n, 2), dtype=np.float64)
        for k, i in enumerate(labels):
            cxy = None
            if centroids is not None:
                try:
                    cxy = (float(centroids[i][0]), float(centroids[i][1]))
                except Exception:
                    cxy = None
            if cxy is None:
                cxy = (
                    float(stats[i, cv2.CC_STAT_LEFT]) + float(stats[i, cv2.CC_STAT_WIDTH]) / 2.0,
                    float(stats[i, cv2.CC_STAT_TOP]) + float(stats[i, cv2.CC_STAT_HEIGHT]) / 2.0,
                )
            pos[k, 0], pos[k, 1] = cxy
        visited = np.zeros(n, dtype=bool)
        if start_anchor is not None:
            ax, ay = float(start_anchor[0]), float(start_anchor[1])
            d0 = None
            if bool(getattr(self, "area_entry_exit_routing_enabled", False)) and stats is not None:
                # Entry/exit mode: pick the FIRST region by distance to its
                # nearest bbox point, not its centroid — a large region with a
                # close edge beats a small one with a close centre. Chain/2-opt
                # distances stay centroid-based (planning-time approximation).
                try:
                    lab_arr = np.asarray(labels, dtype=np.int64)
                    left = stats[lab_arr, cv2.CC_STAT_LEFT].astype(np.float64)
                    top = stats[lab_arr, cv2.CC_STAT_TOP].astype(np.float64)
                    right = left + stats[lab_arr, cv2.CC_STAT_WIDTH].astype(np.float64) - 1.0
                    bottom = top + stats[lab_arr, cv2.CC_STAT_HEIGHT].astype(np.float64) - 1.0
                    d0 = (np.clip(ax, left, right) - ax) ** 2 + (np.clip(ay, top, bottom) - ay) ** 2
                except Exception:
                    d0 = None
            if d0 is None:
                d0 = (pos[:, 0] - ax) ** 2 + (pos[:, 1] - ay) ** 2
            cur = int(np.argmin(d0))
        else:
            areas = np.array([int(stats[i, cv2.CC_STAT_AREA]) for i in labels])
            cur = int(np.argmax(areas))
        order = [cur]
        visited[cur] = True
        for _ in range(n - 1):
            d = (pos[:, 0] - pos[cur, 0]) ** 2 + (pos[:, 1] - pos[cur, 1]) ** 2
            d[visited] = np.inf
            cur = int(np.argmin(d))
            order.append(cur)
            visited[cur] = True
        # Greedy NN leaves typically 10-20% excess travel; bounded 2-opt and
        # Or-opt passes over the SAME regions only shorten transitions (what
        # gets drawn never changes). Flags off / oversized n => legacy order.
        len_greedy = self._open_path_length(pos, order)
        len_2opt = len_greedy
        if bool(getattr(self, "area_order_2opt_enabled", True)) and 3 <= n <= 2500:
            try:
                order = self._two_opt_improve_open_path(pos, order)
                len_2opt = self._open_path_length(pos, order)
            except Exception:
                log.debug("2-opt area order pass failed; keeping greedy order", exc_info=True)
        len_oropt = len_2opt
        if bool(getattr(self, "area_order_or_opt_enabled", True)) and 4 <= n <= 2500:
            try:
                order = self._or_opt_improve_open_path(pos, order)
                len_oropt = self._open_path_length(pos, order)
            except Exception:
                log.debug("or-opt area order pass failed; keeping current order", exc_info=True)
        if n >= 4 and len_greedy > 0:
            saved = 100.0 * (len_greedy - len_oropt) / len_greedy
            self._log(
                f"Region order ({n}): greedy {len_greedy:.0f} -> 2-opt {len_2opt:.0f} "
                f"-> or-opt {len_oropt:.0f} cells ({saved:+.1f}%)"
            )
            self._tm_count("order_len_greedy", len_greedy)
            self._tm_count("order_len_final", len_oropt)
        return [labels[k] for k in order]

    @staticmethod
    def _open_path_length(pos, order) -> float:
        """Total euclidean length of the open path pos[order[0]] -> ... -> pos[order[-1]]."""
        if len(order) < 2:
            return 0.0
        pts = pos[np.asarray(order, dtype=np.int64)]
        return float(np.sum(np.hypot(np.diff(pts[:, 0]), np.diff(pts[:, 1]))))

    def _or_opt_improve_open_path(self, pos, order, max_sweeps: int = 4, time_budget_s: float = 0.15):
        """Bounded Or-opt for an OPEN path with a FIXED start: relocate segments
        of 1-3 consecutive regions (optionally reversed) to a strictly better
        position. Escapes the local optima 2-opt cannot leave (2-opt only
        reverses, never moves). Vectorised over insertion positions per pivot;
        capped by sweeps and wall-clock so planning can never stall drawing."""
        n = len(order)
        if n < 4:
            return list(order)
        idx = [int(v) for v in order]
        t0 = time.perf_counter()

        for _ in range(max_sweeps):
            improved = False
            for seg_len in (1, 2, 3):
                i = 1
                while i + seg_len <= len(idx):
                    if (time.perf_counter() - t0) > time_budget_s:
                        return idx
                    a = idx[i - 1]
                    s0, s1 = idx[i], idx[i + seg_len - 1]
                    after = idx[i + seg_len] if i + seg_len < len(idx) else None
                    pa, p0, p1 = pos[a], pos[s0], pos[s1]
                    if after is None:
                        remove_delta = -math.hypot(pa[0] - p0[0], pa[1] - p0[1])
                    else:
                        pf = pos[after]
                        remove_delta = (math.hypot(pa[0] - pf[0], pa[1] - pf[1])
                                        - math.hypot(pa[0] - p0[0], pa[1] - p0[1])
                                        - math.hypot(p1[0] - pf[0], p1[1] - pf[1]))
                    rest = idx[:i] + idx[i + seg_len:]
                    rp = pos[np.asarray(rest, dtype=np.int64)]
                    d_j_s0 = np.hypot(rp[:, 0] - p0[0], rp[:, 1] - p0[1])
                    d_j_s1 = np.hypot(rp[:, 0] - p1[0], rp[:, 1] - p1[1])
                    edges = np.hypot(np.diff(rp[:, 0]), np.diff(rp[:, 1]))
                    # insert between rest[j] and rest[j+1] — forward or reversed
                    add_fwd = d_j_s0[:-1] + d_j_s1[1:] - edges
                    add_rev = d_j_s1[:-1] + d_j_s0[1:] - edges
                    # append after the open end — forward or reversed
                    end_fwd = float(d_j_s0[-1])
                    end_rev = float(d_j_s1[-1])
                    best_mid_fwd = int(np.argmin(add_fwd)) if add_fwd.size else -1
                    best_mid_rev = int(np.argmin(add_rev)) if add_rev.size else -1
                    candidates = []
                    if best_mid_fwd >= 0:
                        candidates.append((float(add_fwd[best_mid_fwd]), best_mid_fwd, False))
                    if best_mid_rev >= 0:
                        candidates.append((float(add_rev[best_mid_rev]), best_mid_rev, True))
                    candidates.append((end_fwd, len(rest) - 1, False))
                    candidates.append((end_rev, len(rest) - 1, True))
                    add_cost, j, reversed_seg = min(candidates, key=lambda t: t[0])
                    if remove_delta + add_cost < -1e-9:
                        seg = idx[i:i + seg_len]
                        if reversed_seg:
                            seg = seg[::-1]
                        idx = rest[:j + 1] + seg + rest[j + 1:]
                        improved = True
                        # re-scan from the start of this segment length
                        i = 1
                    else:
                        i += 1
            if not improved:
                break
        return idx

    def _two_opt_improve_open_path(self, pos, order, max_sweeps: int = 6, time_budget_s: float = 0.15):
        """Bounded 2-opt for an OPEN path with a FIXED start (index 0 stays — it
        is anchored to the pen position). Reversing order[i..j] is accepted when
        it shortens d(i-1,i)+d(j,j+1); for j at the path end only d(i-1,i) counts.
        Vectorised over j per pivot; capped by sweeps and wall-clock so the draw
        thread can never stall on a pathological layout."""
        n = len(order)
        if n < 3:
            return order
        pts = pos[np.asarray(order, dtype=np.int64)].astype(np.float64)
        idx = np.asarray(order, dtype=np.int64)
        t0 = time.perf_counter()
        for _ in range(max_sweeps):
            improved = False
            for i in range(1, n - 1):
                if (time.perf_counter() - t0) > time_budget_s:
                    return [int(v) for v in idx]
                a = pts[i - 1]
                d_a_i = math.hypot(pts[i, 0] - a[0], pts[i, 1] - a[1])
                d_a_j = np.hypot(pts[i:, 0] - a[0], pts[i:, 1] - a[1])  # j = i..n-1
                seg_next = np.hypot(
                    np.diff(pts[i - 1:, 0]), np.diff(pts[i - 1:, 1])
                )[1:]  # d(j, j+1) for j = i..n-2
                d_i_jnext = np.hypot(
                    pts[i + 1:, 0] - pts[i, 0], pts[i + 1:, 1] - pts[i, 1]
                )  # d(i, j+1) for j = i..n-2
                delta = np.empty(n - i, dtype=np.float64)
                delta[:-1] = (d_a_j[:-1] + d_i_jnext) - (d_a_i + seg_next)
                delta[-1] = d_a_j[-1] - d_a_i  # j = n-1: open end, no (j, j+1) edge
                best_off = int(np.argmin(delta))
                if delta[best_off] < -1e-9:
                    j = i + best_off
                    pts[i:j + 1] = pts[i:j + 1][::-1]
                    idx[i:j + 1] = idx[i:j + 1][::-1]
                    improved = True
            if not improved:
                break
        return [int(v) for v in idx]

    def dfs_4dir_fill_current_color(self, initial_undrawn_mask_u8, x0, y0, cluster_id_for_original_mask):
        if self.cluster_map is None:
             return
        try:
            num_labels, labels, stats, centroids = cv2.connectedComponentsWithStats(initial_undrawn_mask_u8, connectivity=4)
        except Exception:
            labels = None
            num_labels = 0
            stats = None
            centroids = None
        if labels is None or num_labels <= 1:
            return
        current_color_total_mask = (self.cluster_map == cluster_id_for_original_mask).astype(np.uint8) * 255
        # For the `nearest` area order, start from where the pen finished the previous
        # region/color so we always hop to the closest un-painted region next.
        anchor = getattr(self, "_last_pen_cell", None)
        for label_idx in self._ordered_component_labels(num_labels, stats, centroids, anchor):
            if self._automation_cancelled():
                break
            component_mask_bool = (labels == label_idx)
            if not np.any(component_mask_bool):
                continue
            coords = np.argwhere(component_mask_bool)
            if coords.size == 0:
                continue
            # A* bridges of EARLIER regions may have already painted this region's
            # first cell (the bridge routes through any on-color cell, including
            # other components across diagonal gaps). Seeding dfs_4dir_draw with a
            # drawn cell makes it bail out immediately and orphans the rest of the
            # region — seed from an UNDRAWN cell instead. With no bridges (legacy /
            # snake paths) nothing is pre-drawn, so the pick is unchanged.
            if self.drawn_mask is not None and self.drawn_mask.shape == labels.shape:
                undrawn = coords[~self.drawn_mask[coords[:, 0], coords[:, 1]]]
                if undrawn.shape[0] == 0:
                    continue  # region fully painted by earlier bridges
                coords = undrawn
            entry_exit = bool(getattr(self, "area_entry_exit_routing_enabled", False))
            cur_anchor = getattr(self, "_last_pen_cell", None)
            if entry_exit and cur_anchor is not None:
                # Enter the region at the CORNER-most cell nearest to where the
                # pen actually is, instead of the top-left argwhere cell. Corners
                # only: entering mid-edge splits the serpentine snake in two and
                # ADDS internal pen lifts (verified by flight simulation), while
                # a corner start keeps the snake whole and still shortens the
                # hop. _last_pen_cell is (x, y) = (col, row), coords are (row,
                # col) — mind the axis order.
                ax, ay = float(cur_anchor[0]), float(cur_anchor[1])
                rr = coords[:, 0].astype(np.float64)
                cc = coords[:, 1].astype(np.float64)
                corner_idx = sorted({int(np.argmin(rr + cc)), int(np.argmin(rr - cc)),
                                     int(np.argmax(rr + cc)), int(np.argmax(rr - cc))})
                cand = coords[corner_idx]
                k = int(np.argmin((cand[:, 1] - ax) ** 2 + (cand[:, 0] - ay) ** 2))
                sr, sc = int(cand[k][0]), int(cand[k][1])
            else:
                sr, sc = int(coords[0][0]), int(coords[0][1])
            # Phase-0 baseline metric: pen travel between regions (anchor -> the
            # entry cell actually used). Feeds the inter-region order workstream.
            try:
                travel = None
                if cur_anchor is not None:
                    travel = float(np.hypot(float(sc) - float(cur_anchor[0]),
                                            float(sr) - float(cur_anchor[1])))
                    self._tm_count("region_travel_px", travel)
                self._tm_count("regions")
                self._telemetry_emit(
                    "component", label=int(label_idx), size_px=int(coords.shape[0]),
                    travel_px=None if travel is None else round(travel, 1),
                )
            except Exception:
                pass
            self._route_exit_cell = None
            def fallback_draw(sr=sr, sc=sc):
                self.dfs_4dir_draw(current_color_total_mask, sr, sc, x0, y0)
                return not self._automation_cancelled() and not self.stop_flag
            if not self._draw_component_with_route(component_mask_bool, self.dfs_4dir_neighbor_offsets, x0, y0, fallback_draw):
                break
            # Remember the pen's last position so the next region (and the next
            # color's first region) is chosen nearest to it. Entry/exit mode uses
            # the REAL exit cell recorded by the renderer ((row, col) -> (x, y));
            # legacy keeps the centroid.
            updated_anchor = False
            if entry_exit:
                exit_info = getattr(self, "_route_exit_cell", None)
                if exit_info is not None:
                    try:
                        (er, ec), exit_shape = exit_info
                        if exit_shape == labels.shape:
                            self._last_pen_cell = (float(ec), float(er))
                            updated_anchor = True
                    except Exception:
                        pass
            if not updated_anchor and centroids is not None:
                try:
                    self._last_pen_cell = (float(centroids[label_idx][0]), float(centroids[label_idx][1]))
                except Exception:
                    pass


    def _quantize_dynamic_brush_value(self, value: float, *, min_v=None, max_v=None, step=None) -> float:
        # Points mode: the only reachable values ARE the preset labels — snap to
        # the nearest one instead of a min/step grid.
        if str(getattr(self, "dynamic_brush_control_mode", "text") or "text").strip().lower() == "points":
            labels = [
                float(p.get("value"))
                for p in (getattr(self, "dynamic_brush_points", None) or ())
                if isinstance(p, dict) and p.get("value") is not None
            ]
            if labels:
                return float(min(labels, key=lambda lv: abs(lv - float(value))))
        min_v = float(min_v if min_v is not None else getattr(self, "dynamic_brush_min_value", 0.05))
        max_v = float(max_v if max_v is not None else getattr(self, "dynamic_brush_max_value", 1.2))
        step = max(0.0001, float(step if step is not None else getattr(self, "dynamic_brush_step_value", 0.01)))
        base = min_v
        quantized = round((float(value) - base) / step) * step + base
        decimals = self._dynamic_brush_precision_from_step(step)
        clamped = max(min_v, min(max_v, quantized))
        return round(clamped, decimals)

    def _format_dynamic_brush_value(self, value: float) -> str:
        precision = self._dynamic_brush_precision()
        fmt = f"{{:.{precision}f}}"
        return fmt.format(value)

    def _apply_dynamic_brush_value(self, value: float, *, force: bool = False) -> bool:
        if self._automation_cancelled():
            return False
        mode = str(getattr(self, "dynamic_brush_control_mode", "text") or "text").strip().lower()
        if mode == "slider":
            params = getattr(self, "dynamic_brush_slider_params", None)
            if not isinstance(params, (tuple, list)) or len(params) != 4:
                err = tr("status_error_dynamic_brush_slider_missing")
                self._log(err, True)
                if self.status_callback:
                    try:
                        self.status_callback(err)
                    except Exception:
                        log.debug('ignored exception in self.status_callback(err)', exc_info=True)
                self.stop_flag = True
                self.drawing_enabled = False
                return False
        elif mode == "points":
            if not (isinstance(getattr(self, "dynamic_brush_points", None), list) and self.dynamic_brush_points):
                err = tr("status_dynamic_brush_points_missing")
                self._log(err, True)
                if self.status_callback:
                    try:
                        self.status_callback(err)
                    except Exception:
                        log.debug('ignored exception in self.status_callback(err)', exc_info=True)
                self.stop_flag = True
                self.drawing_enabled = False
                return False
        elif self.dynamic_brush_coord is None:
            err = tr("status_error_dynamic_brush_coord_missing")
            self._log(err, True)
            if self.status_callback:
                try:
                    self.status_callback(err)
                except Exception:
                    log.debug('ignored exception in self.status_callback(err)', exc_info=True)
            self.stop_flag = True
            self.drawing_enabled = False
            return False
        target = self._quantize_dynamic_brush_value(value)
        last = getattr(self, "_last_dynamic_brush_value", None)
        tolerance = max(0.0005, float(getattr(self, "dynamic_brush_step_value", 0.01)) / 2.0)
        if not force and last is not None and abs(last - target) <= tolerance:
            return True
        drag_enabled = bool(getattr(self, "dynamic_brush_drag_enabled", False))
        try:
            if mode == "slider":
                orientation, fixed_coord, coord_max, coord_min = self.dynamic_brush_slider_params
                min_v = float(getattr(self, "dynamic_brush_min_value", 0.05))
                max_v = float(getattr(self, "dynamic_brush_max_value", 1.2))
                span = max(1e-9, max_v - min_v)
                vertical = str(orientation).strip().lower() == "vertical"

                track_len = abs(float(coord_max) - float(coord_min))
                # The ends are the track's real ends (refine_slider_params finds
                # them on screen); stay one pixel inside so the press is on it.
                inset = min(0.2, 1.0 / max(1.0, track_len))
                # Presses keep clear of the ends: «Нарисуй меня!» ignores a press on
                # an end cap. The held thumb is then glided to the target — never
                # past an end, where the ◀ ▶ buttons sit (live 2026-10-01).
                edge = min(0.25, max(10.0, track_len * 0.08) / max(1.0, track_len))

                def point(moving: float) -> tuple[int, int]:
                    fixed = int(round(float(fixed_coord)))
                    return (fixed, int(round(moving))) if vertical else (int(round(moving)), fixed)

                def raw_norm(val: float) -> float:
                    return max(0.0, min(1.0, (float(val) - min_v) / span))

                def along(norm: float) -> float:
                    return float(coord_min) * (1.0 - norm) + float(coord_max) * norm

                def track_position(val: float) -> tuple[int, int]:
                    norm = raw_norm(val)
                    return point(along(inset + norm * (1.0 - 2.0 * inset)))

                def release_position(val: float) -> tuple[int, int]:
                    # The extremes are dragged to the very end of the track: the
                    # thumb stops at the true minimum/maximum (an inset press
                    # alone lost Paint's smallest size: 1.1 -> 4.5 px).
                    norm = raw_norm(val)
                    if norm <= 0.0 or norm >= 1.0:
                        return point(along(norm))
                    return track_position(val)

                norm_target = raw_norm(target)
                # The held button rocks along the track, not past its ends.
                rock = ((0, 2) if vertical else (2, 0)) if 2.0 / max(1.0, track_len) < norm_target < 1.0 - 2.0 / max(1.0, track_len) else None
                click_x, click_y = release_position(target)
                if drag_enabled:
                    # Grab-style slider: press where the thumb was left (the last
                    # value we set; the track's min end on the first touch), glide
                    # to the target position, release.
                    start_value = last if last is not None else min_v
                    start_x, start_y = track_position(float(start_value))
                    self._ui_drag(start_x, start_y, click_x, click_y, wiggle=rock)
                else:
                    # Press ON the track (at the target, or inside when the target
                    # is near an end), glide to the target, rock the held button
                    # and release: sliders that ignore a bare click still take a
                    # drag (live Paint).
                    press_x, press_y = point(along(min(1.0 - edge, max(edge, norm_target))))
                    self._ui_drag(press_x, press_y, click_x, click_y, wiggle=rock)
            elif mode == "points":
                def nearest_point(val: float):
                    return min(
                        (p for p in self.dynamic_brush_points if isinstance(p, dict) and p.get("value") is not None),
                        key=lambda p: abs(float(p.get("value")) - float(val)),
                    )

                entry = nearest_point(target)
                click_x = int(round(float(entry.get("x"))))
                click_y = int(round(float(entry.get("y"))))
                if drag_enabled:
                    # Draggable presets: press on the point of the CURRENT value
                    # and glide to the target point (press-hold-release in place
                    # when there is no previous value yet).
                    start_entry = nearest_point(last) if last is not None else entry
                    start_x = int(round(float(start_entry.get("x"))))
                    start_y = int(round(float(start_entry.get("y"))))
                    self._ui_drag(start_x, start_y, click_x, click_y)
                else:
                    self._ui_click_at(click_x, click_y, clicks=2, hold=0.05)
            else:
                formatted = self._format_dynamic_brush_value(target)
                self._ui_click_at(*self.dynamic_brush_coord, clicks=2, hold=.05)
                for command, text in ((self._input.send_keys, "ctrl+a"), (self._input.send_keys, "delete"),
                                      (self._input.write_text, formatted), (self._input.send_keys, "enter")):
                    if self._automation_cancelled():
                        return False
                    command(text)
                    if self._ui_input_wait(.05):
                        return False
            if self._automation_cancelled():
                return False
            # Targets apply a new size asynchronously (live Paint: the first stroke
            # after a slider click still used the previous, bigger brush and spilled
            # outside the color). Let the control take effect before painting.
            if self._ui_input_wait(float(getattr(self, "dynamic_brush_size_settle", 0.3) or 0.0)):
                return False
            self._last_dynamic_brush_value = target
            return True
        except Exception as exc:
            self._log(f"Dynamic brush value apply error: {exc}", True)
            if self.status_callback:
                try:
                    self.status_callback(tr("status_dynamic_brush_apply_error"))
                except Exception:
                    log.debug("ignored exception in self.status_callback(tr('status_dynamic_brush_apply_error'))", exc_info=True)
            self.stop_flag = True
            self.drawing_enabled = False
            return False











    def dfs_4dir_dynamic_fill_current_color(self, initial_undrawn_mask_u8, x0, y0, cluster_id_for_original_mask):
        """Dynamic brush v2 for one color.

        Coarse part: big-brush tiers only where the calibrated stamp fits inside
        this color (erosion) - they cannot touch other colors. Detail part: the
        rest with the detail size, which is ALWAYS applied explicitly - the
        control is never left at whatever size calibration or a tier set.
        With _brush_pass set, _draw_colors runs the coarse part for all colors
        first and the detail part afterwards (thin features end on top)."""
        if self.cluster_map is None:
            return
        brush_pass = getattr(self, "_brush_pass", None)
        if brush_pass not in ("detail", "thin"):
            if self._dynamic_brush_v2_color_has_tier_potential(initial_undrawn_mask_u8):
                full = self.cluster_map == cluster_id_for_original_mask
                self._pocket_fill(cluster_id_for_original_mask, x0, y0, self._pocket_coarse_radii(full))
            if brush_pass == "coarse" or self._automation_cancelled() or self.stop_flag:
                return
        detail_value = self._dynamic_brush_v2_detail_value()
        if self._automation_cancelled() or self.stop_flag:
            return
        if detail_value is not None and not self._apply_dynamic_brush_value(detail_value):
            return
        if self.drawn_mask is None:
            return
        if brush_pass != "thin" and detail_value is not None:
            radius_px = self._dynamic_brush_v2_radius_px_for_value(detail_value) or 0.5
            # Contour loops with the detail size: each edge in one continuous
            # stroke, strokes wider than a cell stay inset from the border.
            self._pocket_fill(cluster_id_for_original_mask, x0, y0, [radius_px])
            if brush_pass == "detail" or self._automation_cancelled() or self.stop_flag:
                return
        remaining_u8 = ((self.cluster_map == cluster_id_for_original_mask) & (~self.drawn_mask)).astype(np.uint8) * 255
        if np.any(remaining_u8):
            self.dfs_4dir_fill_current_color(remaining_u8, x0, y0, cluster_id_for_original_mask)

    def _dynamic_brush_v2_detail_value(self) -> float | None:
        """Size for edges and thin parts: the base size when the control reaches
        it, otherwise the smallest calibrated size (the closest the target can
        do). None only without a calibration table."""
        base_value = self._dynamic_brush_v2_ensure_base_value()
        if base_value is not None:
            return base_value
        if self._automation_cancelled() or self.stop_flag:
            return None
        table = self._dynamic_brush_v2_radius_table()
        if table is None:
            return None
        radii, values = table
        if not getattr(self, "_dyn2_detail_notice", False):
            self._dyn2_detail_notice = True
            self._log(
                f"Dynamic brush: smallest stamp is {float(radii[0]) * 2:.1f}px for a "
                f"{max(1, int(self.brush_size))}px cell; details use it and may overlap edges slightly."
            )
        return self._quantize_dynamic_brush_value(float(values[0]))

    def dynamic_brush_recommended_cell(self) -> int | None:
        """Grid cell (px) matching the smallest calibrated stamp, when it is clearly
        wider than the current cell. This is a sampling heuristic, not a
        guarantee of coverage for irregular or translucent stamps."""
        table = self._dynamic_brush_v2_radius_table()
        if table is None:
            return None
        diameter = float(table[0][0]) * 2.0
        cell = max(1, int(self.brush_size))
        # Both ways: a cell wider than the smallest stamp leaves seams between
        # strokes (a 1px stamp on a 2px grid paints every other pixel).
        recommended = max(1, int(round(diameter)))
        return recommended if recommended != cell else None

    def _dynamic_brush_passes(self) -> list:
        """['coarse', 'detail', 'thin'] when the calibrated dynamic brush drives this
        draw (DFS family only); [None] keeps the classic one-pass order."""
        if not bool(getattr(self, "dynamic_brush_session_active", False)):
            return [None]
        if self.drawing_algorithm in ("outline_and_fill", "outline_only", "line_cover"):
            return [None]
        if not self._dynamic_brush_active_for_current_draw() or self._dynamic_brush_v2_radius_table() is None:
            return [None]
        if getattr(self, "target_connects_points", None) is True:
            # Live Paint, 5 pictures: the detail size alone beat coarse+detail on
            # every one (size changes and extra colour picks cost more than rows saved).
            return ["thin"]
        return ["coarse", "detail", "thin"]

    def _brush_pass_pending_mask(self, cluster_id):
        if self.cluster_map is None or self.drawn_mask is None:
            return None
        pending = (self.cluster_map == cluster_id) & (~self.drawn_mask)
        phase = getattr(self, "_semantic_active_phase", None)
        if phase is not None:
            try:
                region_mask = self._semantic_region_planes_mask()
                if region_mask is not None and region_mask.shape == pending.shape:
                    pending &= (region_mask == int(phase))
            except Exception:
                log.debug("semantic region mask failed in brush pass", exc_info=True)
        return pending

    def _brush_pass_has_work(self, cluster_id, brush_pass) -> bool:
        pending = self._brush_pass_pending_mask(cluster_id)
        if pending is None or not np.any(pending):
            return False
        if brush_pass == "coarse":
            return self._dynamic_brush_v2_color_has_tier_potential(pending.astype(np.uint8) * 255)
        return True

    def _brush_pass_color_order(self, brush_pass):
        """Detail pass: largest remaining area first, thin/small parts last so
        they end on top of any 1px overlap from wider edge strokes."""
        if brush_pass not in ("detail", "thin") or self.cluster_map is None or self.drawn_mask is None:
            return None
        remaining = []
        for index, (_hex, cluster_id, _count, _rgb) in enumerate(self.color_palette):
            pending = self._brush_pass_pending_mask(cluster_id) if cluster_id is not None else None
            remaining.append((-(int(np.count_nonzero(pending)) if pending is not None else 0), index))
        return [index for _neg, index in sorted(remaining)]

    def line_cover_fill_current_color(self, mask_u8, x0, y0, cluster_id):
        """«Прямые отрезки»: the fewest straight strokes (see line_cover.py),
        rendered by the shared cell renderer (pause/stop/progress/straight strokes)."""
        from .line_cover import cover_segments, order_segments, segment_cells
        segments = cover_segments(np.asarray(mask_u8) == 255)
        if not segments:
            return
        anchor = getattr(self, "_last_pen_cell", None)  # (col, row) of the previous colour's exit
        start = (int(round(anchor[1])), int(round(anchor[0]))) if anchor is not None else segments[0][0]
        ordered = order_segments(segments, start)
        cells = segment_cells(ordered)
        merge = self.run_length_merge_enabled
        self.run_length_merge_enabled = True  # a segment is one stroke, not one move per cell
        try:
            self._draw_cell_sequence(cells, mask_u8, x0, y0)
        finally:
            self.run_length_merge_enabled = merge
        end_row, end_col = ordered[-1][1]
        self._last_pen_cell = (float(end_col), float(end_row))


    def _merge_identical_palette_colors(self, background_cluster_id) -> int:
        """Clusters that ended up with the SAME final colour (e.g. anti-alias
        fringes snapped to one palette swatch) become one cluster: one colour
        pick instead of several, and whole regions instead of 1-px slivers that a
        wide brush cannot paint. Skipped with manual mixing (per-cluster layer
        plans)."""
        if self.cluster_map is None or not self.color_palette:
            return 0
        if bool(getattr(self, "manual_palette_mix_enabled", False)):
            return 0
        keep: dict[str, int] = {}
        merged: list = []
        remap: dict[int, int] = {}
        counts: dict[int, int] = {}
        for hex_color, cluster_id, count, rgb in self.color_palette:
            if cluster_id is None or cluster_id == background_cluster_id:
                merged.append((hex_color, cluster_id, count, rgb))
                continue
            key = str(hex_color).upper()
            if key in keep:
                remap[int(cluster_id)] = keep[key]
                counts[keep[key]] += int(count)
            else:
                keep[key] = int(cluster_id)
                counts[int(cluster_id)] = int(count)
                merged.append((hex_color, cluster_id, count, rgb))
        if not remap:
            return 0
        for source, target in remap.items():
            self.cluster_map[self.cluster_map == source] = target
        self.color_palette = [
            (h, c, counts.get(int(c), n) if c is not None else n, rgb) for h, c, n, rgb in merged
        ]
        plans = getattr(self, "_manual_palette_cluster_plan", None)
        if isinstance(plans, dict):
            for source in remap:
                plans.pop(source, None)
        self._log(f"Identical colours merged: {len(remap)} cluster(s) joined their twins.")
        return len(remap)

    def _compute_small_component_mask(self, threshold: int, background_cluster_id: int) -> np.ndarray | None:
        if self.cluster_map is None or threshold <= 0:
            return None
        try:
            mask = np.zeros_like(self.cluster_map, dtype=bool)
        except Exception:
            return None
        unique_ids = np.unique(self.cluster_map)
        for cid in unique_ids:
            if cid == background_cluster_id or cid < 0:
                continue
            cluster_mask = (self.cluster_map == cid)
            if not np.any(cluster_mask):
                continue
            component_mask = cluster_mask.astype(np.uint8)
            num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(component_mask, connectivity=4)
            for label_idx in range(1, num_labels):
                if stats[label_idx, cv2.CC_STAT_AREA] < threshold:
                    mask |= (labels == label_idx)
        if not np.any(mask):
            return None
        return mask

    # A small region is a detail when it is clearly DARKER than what it would merge
    # into (a pupil, an eyebrow, a mouth line, an outline: OKLab L 0.12+ lower) or
    # of another colour at a similar lightness (red lips on skin). Light specks on
    # a darker area are highlights and edge noise: on real snapshots keeping them
    # sprinkled white dots over faces and doubled the regions to draw.
    _DETAIL_DARKER = 0.12
    _DETAIL_HUE = 0.10
    _DETAIL_LIGHTER = 0.06

    def _colour_gaps(self, colours: dict[int, tuple[int, int, int]]):
        """A function telling whether cluster `a` is a detail on cluster `b` (gap > 1)."""
        ids = [cid for cid, rgb in colours.items() if rgb is not None]
        lab = dict(zip(ids, rgb_to_oklab_numpy(np.asarray([colours[cid] for cid in ids], np.float64)))) if ids else {}

        def gap(a, b):
            if a not in lab or b not in lab:
                return 0.0
            darker = float(lab[b][0] - lab[a][0])
            hue = float(np.linalg.norm(lab[a][1:] - lab[b][1:]))
            if darker > self._DETAIL_DARKER:
                return 2.0
            if hue > self._DETAIL_HUE and darker > -self._DETAIL_LIGHTER:
                return 2.0
            return 0.0
        return gap

    def _merge_small_components_into_neighbors(self, center_map: dict[int, tuple[int, int, int]], background_cluster_id: int) -> int:
        if self.cluster_map is None:
            return 0
        threshold = int(getattr(self, "fast_min_region_area", 0))
        if threshold <= 0:
            return 0
        cluster_ids = list(center_map.keys())
        if not cluster_ids:
            return 0
        keep_details = bool(getattr(self, "prep_keep_details", True))
        gap = self._colour_gaps(center_map) if keep_details else None
        counts = {cid: int(np.count_nonzero(self.cluster_map == cid)) for cid in cluster_ids}
        neighbor_offsets = [(-1, 0), (1, 0), (0, -1), (0, 1)]
        merges_done = 0
        h_map, w_map = self.cluster_map.shape
        for cid in cluster_ids:
            mask = (self.cluster_map == cid).astype(np.uint8)
            if mask.sum() == 0:
                continue
            num_labels, labels, stats, _ = cv2.connectedComponentsWithStats(mask, connectivity=4)
            for label_idx in range(1, num_labels):
                area = int(stats[label_idx, cv2.CC_STAT_AREA])
                if area >= threshold:
                    continue
                component_coords = np.argwhere(labels == label_idx)
                if component_coords.size == 0:
                    continue
                neighbor_votes: dict[int, int] = {}
                touches_background = False
                for r, c in component_coords:
                    for dr, dc in neighbor_offsets:
                        nr, nc = r + dr, c + dc
                        if 0 <= nr < h_map and 0 <= nc < w_map:
                            neighbor_cid = self.cluster_map[nr, nc]
                            if neighbor_cid == cid:
                                continue
                            if neighbor_cid == background_cluster_id:
                                touches_background = True
                                continue
                            neighbor_votes[neighbor_cid] = neighbor_votes.get(neighbor_cid, 0) + 1
                if not neighbor_votes:
                    # A speck whose ONLY neighbours are the removed background is
                    # noise floating in the void — drop it INTO the background
                    # rather than leaving an orphaned dot (live report: «удаляет
                    # ячейки, но не объединяет — картинка рваная»). Without
                    # background removal there is no such case (a speck always
                    # borders a real region), so this only fires for edge noise.
                    if touches_background:
                        for r, c in component_coords:
                            self.cluster_map[r, c] = background_cluster_id
                        counts[cid] = max(0, counts.get(cid, 0) - area)
                        merges_done += 1
                    continue
                best_neighbor = max(neighbor_votes.items(), key=lambda item: (counts.get(item[0], 0), item[1], -item[0]))[0]
                if gap is not None and gap(cid, best_neighbor) > 1.0:
                    continue                      # a detail, not noise: keep it
                for r, c in component_coords:
                    self.cluster_map[r, c] = best_neighbor
                counts[cid] = max(0, counts.get(cid, 0) - area)
                counts[best_neighbor] = counts.get(best_neighbor, 0) + area
                merges_done += 1
        return merges_done

    def _apply_aggressive_despeckle(self, background_cluster_id: int) -> int:
        """Opt-in: relabel every sub-threshold component to the nearest large
        region (distance transform) so NO tiny dots survive. Runs post-quant on
        the final cluster_map; refreshes background_mask and drops now-empty
        palette entries. Off by default."""
        if not bool(getattr(self, "aggressive_despeckle_enabled", False)):
            return 0
        cm = getattr(self, "cluster_map", None)
        if cm is None or not hasattr(cm, "shape"):
            return 0
        min_area = int(getattr(self, "fast_min_region_area", 0))
        if min_area <= 1:
            min_area = 6      # self-sufficient default when «cell merge» is unset
        try:
            new_map = distance_relabel_specks(
                np.asarray(cm, dtype=int), min_area=min_area,
                background=background_cluster_id)
        except Exception:
            log.debug("aggressive despeckle failed", exc_info=True)
            return 0
        new_map = np.asarray(new_map, dtype=int)
        old_map = np.asarray(cm, dtype=int)
        if bool(getattr(self, "prep_keep_details", True)):
            colours = {int(entry[1]): entry[3] for entry in (getattr(self, "color_palette", None) or [])
                       if entry[1] is not None and entry[3] is not None}
            gap = self._colour_gaps(colours)
            moved = np.argwhere((new_map != old_map) & (new_map != background_cluster_id))
            pairs = {}
            for r, c in moved:
                pairs.setdefault((int(old_map[r, c]), int(new_map[r, c])), []).append((r, c))
            for (old, new), cells in pairs.items():
                if gap(old, new) > 1.0:
                    rows, cols = zip(*cells)
                    new_map[list(rows), list(cols)] = old      # a detail, not noise
        changed = int(np.count_nonzero(new_map != old_map))
        if changed == 0:
            return 0
        self.cluster_map = new_map
        self._semantic_region_mask_cache = None
        try:
            self.background_mask = self.cluster_map == background_cluster_id
        except Exception:
            log.debug("aggressive despeckle: background_mask refresh failed", exc_info=True)
        try:
            palette = getattr(self, "color_palette", None)
            if palette:
                self.color_palette = [e for e in palette if bool(np.any(self.cluster_map == e[1]))]
        except Exception:
            log.debug("aggressive despeckle: palette filter failed", exc_info=True)
        self._log(f"Aggressive despeckle: relabelled {changed} cell(s) (min area {min_area}).")
        return changed

    def _notify_skipped_components(self, skipped_components: list[dict[str, object]], overlay_img):
        try:
            count = len(skipped_components)
            threshold = int(getattr(self, "fast_min_region_area", 0))
            areas_preview = ", ".join(str(item.get("area")) for item in skipped_components[:5])
            message = f"warn: skipped {count} region(s) smaller than {threshold} cell(s). Areas: {areas_preview}"
            self._log(message)
            if self.status_callback:
                try:
                    self.status_callback(message)
                except Exception:
                    log.debug('ignored exception in self.status_callback(message)', exc_info=True)
            callback = getattr(self, "skipped_regions_callback", None)
            if callable(callback):
                payload = {
                    "regions": skipped_components,
                    "threshold": threshold,
                    "overlay": overlay_img,
                    "draw_region": tuple(self.draw_region) if isinstance(self.draw_region, tuple) else None,
                    "message": message,
                }
                try:
                    callback(payload)
                except Exception:
                    log.debug('ignored exception in callback(payload)', exc_info=True)
        except Exception:
            log.debug('ignored exception in count = len(skipped_components)', exc_info=True)




    def draw_contour_segment(self, contour_rc_points, x0, y0, brush_size):
        if not contour_rc_points or self.stop_flag:
            return
        path_points = list(contour_rc_points)
        if len(path_points) > 1 and path_points[0] != path_points[-1]:
            path_points.append(path_points[0])
        first_r, first_c = path_points[0]
        fallback_rows = fallback_cols = 1
        if isinstance(self.cluster_map, np.ndarray):
            fallback_rows, fallback_cols = self.cluster_map.shape[:2]
        region_w, region_h = self._region_dimensions(fallback_cols=fallback_cols, fallback_rows=fallback_rows, brush=brush_size)
        start_sx = self._cell_center_axis(x0, region_w, first_c, brush_size)
        start_sy = self._cell_center_axis(y0, region_h, first_r, brush_size)
        mouse_is_down = False
        try:
            self._mouse_up_with_settle()
            self._move_abs(start_sx, start_sy); self._input_click(button='left'); time.sleep(0.01)
            self._pen_down(); time.sleep(self.pen_settle_delay); mouse_is_down = True

            if 0 <= first_r < self.drawn_mask.shape[0] and 0 <= first_c < self.drawn_mask.shape[1] and not self.drawn_mask[first_r, first_c]:
                self.drawn_mask[first_r, first_c] = True
                if self.pixel_update_callback: self.pixel_update_callback()

            last_sx, last_sy = start_sx, start_sy
            resumed_just_now = False
            for i in range(1, len(path_points)):
                if self.stop_flag: break
                r_cluster, c_cluster = path_points[i]
                curr_sx = self._cell_center_axis(x0, region_w, c_cluster, brush_size)
                curr_sy = self._cell_center_axis(y0, region_h, r_cluster, brush_size)

                if not self.drawing_enabled:
                    if mouse_is_down:
                        self._mouse_up_with_settle()
                        mouse_is_down = False   # always sync to pen-up so resume re-presses
                    while not self.drawing_enabled and not self.stop_flag: time.sleep(0.01)
                    if self.drawing_enabled and not self.stop_flag: resumed_just_now = True
                if self.stop_flag: break

                if resumed_just_now:
                    self._move_abs(last_sx, last_sy); time.sleep(self.pen_repress_settle)
                    self._pen_down(); time.sleep(self.pen_settle_delay); mouse_is_down = True
                    resumed_just_now = False

                if not (last_sx == curr_sx and last_sy == curr_sy):
                    self.draw_line(last_sx, last_sy, curr_sx, curr_sy)
                last_sx, last_sy = curr_sx, curr_sy

                if 0 <= r_cluster < self.drawn_mask.shape[0] and 0 <= c_cluster < self.drawn_mask.shape[1] and not self.drawn_mask[r_cluster, c_cluster]:
                    self.drawn_mask[r_cluster, c_cluster] = True
                    self._notify_pixel_progress()
        except Exception as e: self._log(f"[ERROR] Contour drawing: {e}", True); traceback.print_exc(); self.stop_flag = True
        finally:
            self._notify_pixel_progress(force=True)  # flush the throttled preview
            if mouse_is_down:
                self._mouse_up_with_settle(warn_context="[WARN] Error mouse_up (contour)")

    def _draw_cluster_with_algorithm(self, cluster_id: int, x0: int, y0: int) -> bool:
        if self.cluster_map is None:
            return False
        self._ensure_drawn_mask_shape()
        if self.drawn_mask is None:
            return False
        mask_bool = (self.cluster_map == cluster_id) & (~self.drawn_mask)
        phase = getattr(self, "_semantic_active_phase", None)
        if phase is not None:
            # Two-pass draw: restrict the fill to this phase's regions. The
            # outline algorithms below ignore the phase (they recompute their
            # own masks) — drawn_mask still prevents any double painting.
            try:
                region_mask = self._semantic_region_planes_mask()
                if region_mask is not None and region_mask.shape == mask_bool.shape:
                    mask_bool &= (region_mask == int(phase))
            except Exception:
                log.debug("semantic region mask failed in fill", exc_info=True)
        mask_u8 = mask_bool.astype(np.uint8) * 255
        if not np.any(mask_u8):
            return False
        if self.drawing_algorithm == "outline_and_fill":
            self.outline_and_fill_current_color(mask_bool, x0, y0, cluster_id)
        elif self.drawing_algorithm == "outline_only":
            self.outline_only_current_color(mask_bool, x0, y0, cluster_id)
        elif self.drawing_algorithm == "line_cover":
            self.line_cover_fill_current_color(mask_u8, x0, y0, cluster_id)
        elif self._dynamic_brush_active_for_current_draw():
            self.dfs_4dir_dynamic_fill_current_color(mask_u8, x0, y0, cluster_id)
        else:
            self.dfs_4dir_fill_current_color(mask_u8, x0, y0, cluster_id)
        if self._automation_cancelled():
            return False
        return True

    def _manual_palette_candidate_indices(self, target_lab, manual_cache, limit: int = 12) -> list[int]:
        entries = manual_cache.get("entries") if manual_cache else None
        if not entries:
            return []
        scored: list[tuple[float, int]] = []
        for entry in entries:
            lab_val = entry.get("lab")
            idx = entry.get("index")
            if lab_val is None or idx is None:
                continue
            try:
                diff = target_lab - np.asarray(lab_val, dtype=np.float32)
                score = float(np.dot(diff, diff))
            except Exception:
                continue
            scored.append((score, int(idx)))
        if not scored:
            return []
        scored.sort(key=lambda item: item[0])
        limited = scored[:max(1, min(limit, len(scored)))]
        return [idx for _, idx in limited]

    def _project_clusters_to_manual_palette(self, manual_cache) -> bool:
        entries = manual_cache.get("entries") if manual_cache else None
        if not entries or not self.color_palette:
            return False

        def _normalize_rgb(value, hex_value=None):
            if isinstance(value, np.ndarray):
                value = value.tolist()
            if isinstance(value, (list, tuple)) and len(value) >= 3:
                try:
                    return tuple(int(max(0, min(255, round(float(value[idx]))))) for idx in range(3))
                except Exception:
                    log.debug('ignored exception in return tuple((int(max(0, min(255, round(float(value[idx]))))) for idx in rang...', exc_info=True)
            if isinstance(hex_value, str):
                cleaned = hex_value.strip().lstrip("#")
                if len(cleaned) == 6 and all(ch in "0123456789ABCDEFabcdef" for ch in cleaned):
                    try:
                        return tuple(int(cleaned[idx:idx + 2], 16) for idx in (0, 2, 4))
                    except Exception:
                        return None
            return None

        by_index = manual_cache.get("by_index") or {}
        mapped_palette = []
        plan_map: dict[int, dict] = {}
        target_rgb_map: dict[int, tuple[int, int, int]] = {}

        for hex_color, cluster_id, count, rgb in self.color_palette:
            try:
                cluster_key = int(cluster_id)
            except Exception:
                cluster_key = cluster_id

            target_rgb = _normalize_rgb(rgb, hex_color)
            mapped_rgb = target_rgb
            mapped_hex = str(hex_color or "").strip()
            plan = None

            if target_rgb is not None:
                try:
                    target_lab = np.asarray(self._srgb_to_lab_single(target_rgb), dtype=np.float32)
                except Exception:
                    target_lab = None
                if target_lab is not None and np.shape(target_lab)[-1] == 3:
                    candidate_indices = self._manual_palette_candidate_indices(target_lab, manual_cache, limit=1)
                    if candidate_indices:
                        candidate = by_index.get(candidate_indices[0])
                        if candidate is not None:
                            candidate_rgb = _normalize_rgb(candidate.get("rgb"), candidate.get("hex"))
                            if candidate_rgb is not None:
                                mapped_rgb = candidate_rgb
                                mapped_hex = str(candidate.get("hex") or "").strip() or "#{:02X}{:02X}{:02X}".format(*candidate_rgb)
                                delta_vec = target_lab - np.asarray(candidate.get("lab"), dtype=np.float32)
                                score = float(np.dot(delta_vec, delta_vec))
                                plan = {
                                    "cluster_id": cluster_key,
                                    "count": int(count),
                                    "primary_idx": int(candidate_indices[0]),
                                    "primary_rgb": candidate_rgb,
                                    "secondary_idx": None,
                                    "secondary_rgb": None,
                                    "tertiary_idx": None,
                                    "tertiary_rgb": None,
                                    "alpha1": 1.0,
                                    "alpha2": 0.0,
                                    "alpha3": 0.0,
                                    "result_rgb": candidate_rgb,
                                    "target_rgb": target_rgb,
                                    "error": score,
                                    "delta_e": float(np.linalg.norm(delta_vec)),
                                    "layers": [{"idx": int(candidate_indices[0]), "alpha": 1.0}],
                                }

            if mapped_rgb is None:
                mapped_rgb = target_rgb
            if mapped_rgb is None:
                continue
            if not mapped_hex:
                mapped_hex = "#{:02X}{:02X}{:02X}".format(*mapped_rgb)

            mapped_palette.append(
                {
                    "hex": mapped_hex,
                    "id": cluster_key,
                    "count": int(count),
                    "rgb": mapped_rgb,
                    "luminance": self._palette_lightness(mapped_rgb),
                }
            )
            if plan is not None:
                plan_map[cluster_key] = plan
                target_rgb_map[cluster_key] = target_rgb

        if not mapped_palette:
            return False

        self._manual_palette_cluster_plan = plan_map
        self._manual_palette_target_rgb = target_rgb_map
        self._manual_palette_mix_solution_cache = {}
        self._cluster_uses_manual_palette = bool(plan_map)

        sorted_palette = self._sort_palette_entries(mapped_palette)
        self.color_palette = [
            (entry["hex"], entry["id"], entry["count"], entry["rgb"])
            for entry in sorted_palette
        ]
        self._log(f"Manual palette mapping applied to {len(plan_map)}/{len(mapped_palette)} cluster(s).")
        return bool(plan_map)

    def _resolve_manual_mix_solution(
        self,
        target_rgb: tuple[int, int, int] | None,
        manual_cache: dict | None = None,
        primary_idx: int | None = None,
        candidate_indices: list[int] | None = None,
    ):
        if not self.manual_palette_mix_enabled:
            return None
        manual_cache = manual_cache or self._get_manual_palette_cache()
        if not manual_cache:
            return None
        target_tuple: tuple[int, int, int] | None = None
        if target_rgb is not None:
            try:
                target_tuple = tuple(int(max(0, min(255, round(float(v))))) for v in target_rgb)
            except Exception:
                target_tuple = None
        if target_tuple is None:
            return None
        by_index = manual_cache.get("by_index") or {}
        if not by_index:
            return None
    
        try:
            target_linear = self._srgb_to_linear_rgb(target_tuple)
        except Exception:
            return None
        target_lab = self._srgb_to_lab_single(target_tuple)
        if isinstance(target_lab, (list, tuple)):
            target_lab = np.asarray(target_lab, dtype=np.float32)
        if target_lab is None or np.shape(target_lab)[-1] != 3:
            return None
        bg_linear = self._srgb_to_linear_rgb(self.manual_mix_canvas_rgb)
    
        if candidate_indices is None or not candidate_indices:
            candidate_indices = self._manual_palette_candidate_indices(target_lab, manual_cache, limit=12)
    
        candidate_set: list[int] = []
        seen = set()
        if primary_idx is not None:
            try:
                pri = int(primary_idx)
                if pri in by_index:
                    seen.add(pri)
                    candidate_set.append(pri)
            except Exception:
                log.debug('ignored exception in pri = int(primary_idx)', exc_info=True)
        for idx in candidate_indices:
            if idx in seen or idx not in by_index:
                continue
            seen.add(idx)
            candidate_set.append(idx)
        if not candidate_set:
            candidate_set = list(by_index.keys())

        best_single_idx: int | None = None
        best_single_plan: dict | None = None
        best_single_delta: float | None = None
        tolerance = 1
        for idx in candidate_set:
            entry = by_index.get(idx)
            if not entry:
                continue
            entry_rgb = entry.get("rgb")
            entry_lab = entry.get("lab")
            if entry_rgb is None:
                continue
            try:
                palette_rgb = tuple(int(max(0, min(255, round(float(v))))) for v in entry_rgb)
            except Exception:
                continue
            try:
                palette_linear = self._srgb_to_linear_rgb(palette_rgb)
            except Exception:
                palette_linear = None
            palette_lab_arr = None
            if entry_lab is not None:
                try:
                    palette_lab_arr = np.asarray(entry_lab, dtype=np.float32)
                except Exception:
                    palette_lab_arr = None
            delta_single = None
            if palette_lab_arr is not None and palette_lab_arr.shape[-1] == 3:
                diff_lab = target_lab - palette_lab_arr
                delta_single = float(np.sqrt(np.dot(diff_lab, diff_lab)))
            if palette_linear is not None:
                single_result_linear = self._blend_linear_rgb(bg_linear, palette_linear, 1.0)
                single_error = float(np.sum((single_result_linear - target_linear) ** 2))
            else:
                single_result_linear = None
                single_error = float("inf")
            single_plan = {
                "primary_idx": idx,
                "primary_rgb": palette_rgb,
                "secondary_idx": None,
                "secondary_rgb": None,
                "tertiary_idx": None,
                "tertiary_rgb": None,
                "alpha1": 1.0,
                "alpha2": 0.0,
                "alpha3": 0.0,
                "result_rgb": palette_rgb,
                "target_rgb": target_tuple,
                "error": single_error,
                "layers": [
                    {"idx": idx, "alpha": 1.0},
                ],
            }
            if delta_single is not None:
                single_plan["delta_e"] = delta_single
                if best_single_delta is None or delta_single < best_single_delta:
                    best_single_delta = delta_single
                    best_single_plan = single_plan
                    best_single_idx = idx
            if best_single_plan is None:
                best_single_plan = single_plan

            if all(abs(palette_rgb[channel] - target_tuple[channel]) <= tolerance for channel in range(3)):
                return single_plan

        max_candidates = max(1, min(len(candidate_set), 6))
        candidate_subset = candidate_set[:max_candidates]
    
        linear_cache: dict[int, np.ndarray] = {}
        rgb_cache: dict[int, tuple[int, int, int]] = {}
        for idx in candidate_subset:
            entry = by_index.get(idx)
            if not entry:
                continue
            base_rgb = entry.get("rgb")
            if base_rgb is None:
                continue
            try:
                rgb_tuple = tuple(int(max(0, min(255, round(float(v))))) for v in base_rgb)
            except Exception:
                continue
            try:
                linear_cache[idx] = self._srgb_to_linear_rgb(rgb_tuple)
            except Exception:
                continue
            rgb_cache[idx] = rgb_tuple
    
        best_solution: dict | None = None
        best_error: float | None = None
        perfect_threshold = 8e-7
    
        def _record_solution(plan_dict: dict, result_linear: np.ndarray):
            nonlocal best_solution, best_error
            if result_linear is None:
                return
            error_val = float(np.sum((result_linear - target_linear) ** 2))
            plan = dict(plan_dict)
            plan["result_rgb"] = self._linear_to_srgb_rgb(result_linear)
            plan["target_rgb"] = target_tuple
            plan["error"] = error_val
            try:
                res_lab = self._srgb_to_lab_single(plan["result_rgb"])
                res_lab_arr = np.asarray(res_lab, dtype=np.float32)
                if res_lab_arr.shape[-1] == 3:
                    plan["delta_e"] = float(np.linalg.norm(res_lab_arr - target_lab))
            except Exception:
                log.debug("ignored exception in res_lab = self._srgb_to_lab_single(plan['result_rgb'])", exc_info=True)
            if best_error is None or error_val < best_error - 1e-9:
                best_solution = plan
                best_error = error_val
    
        for p_idx in candidate_subset:
            primary_linear = linear_cache.get(p_idx)
            if primary_linear is None:
                continue
            primary_rgb_int = rgb_cache.get(p_idx, (0, 0, 0))
            diff_primary = primary_linear - bg_linear
            denom = float(np.dot(diff_primary, diff_primary))
            if denom < 1e-9:
                alpha1 = 0.0
            else:
                alpha1 = float(np.dot(target_linear - bg_linear, diff_primary) / denom)
            alpha1 = max(0.0, min(1.0, alpha1))
            first_layer = self._blend_linear_rgb(bg_linear, primary_linear, alpha1)
            _record_solution(
                {
                    "primary_idx": p_idx,
                    "primary_rgb": primary_rgb_int,
                    "secondary_idx": None,
                    "secondary_rgb": None,
                    "tertiary_idx": None,
                    "tertiary_rgb": None,
                    "alpha1": float(alpha1),
                    "alpha2": 0.0,
                    "alpha3": 0.0,
                    "layers": [
                        {"idx": p_idx, "alpha": float(alpha1)},
                    ],
                },
                first_layer,
            )
            if best_error is not None and best_error <= perfect_threshold:
                break
    
        if len(candidate_subset) >= 2 and (best_error is None or best_error > perfect_threshold):
            for p_idx in candidate_subset:
                primary_linear = linear_cache.get(p_idx)
                if primary_linear is None:
                    continue
                primary_rgb_int = rgb_cache.get(p_idx, (0, 0, 0))
                for s_idx in candidate_subset:
                    if s_idx == p_idx:
                        continue
                    secondary_linear = linear_cache.get(s_idx)
                    if secondary_linear is None:
                        continue
                    secondary_rgb_int = rgb_cache.get(s_idx, (0, 0, 0))
                    try:
                        matrix = np.column_stack((primary_linear - bg_linear, secondary_linear - bg_linear))
                        weights, *_ = np.linalg.lstsq(matrix, target_linear - bg_linear, rcond=None)
                    except Exception:
                        continue
                    weights = np.asarray(weights, dtype=np.float32).reshape(-1)
                    if weights.size != 2:
                        continue
                    weights = np.clip(weights, 0.0, 1.0)
                    if float(np.sum(weights)) > 1.0 + 1e-6:
                        weights = self._project_simplex(weights, z=1.0)
                    alpha2 = float(max(0.0, min(1.0, weights[1])))
                    denom = 1.0 - alpha2
                    if denom <= 1e-6:
                        alpha1 = 0.0
                    else:
                        alpha1 = float(max(0.0, min(1.0, weights[0] / denom)))
                    first_layer = self._blend_linear_rgb(bg_linear, primary_linear, alpha1)
                    second_layer = self._blend_linear_rgb(first_layer, secondary_linear, alpha2)
                    _record_solution(
                        {
                            "primary_idx": p_idx,
                            "primary_rgb": primary_rgb_int,
                            "secondary_idx": s_idx,
                            "secondary_rgb": secondary_rgb_int,
                            "tertiary_idx": None,
                            "tertiary_rgb": None,
                            "alpha1": float(alpha1),
                            "alpha2": float(alpha2),
                            "alpha3": 0.0,
                            "layers": [
                                {"idx": p_idx, "alpha": float(alpha1)},
                                {"idx": s_idx, "alpha": float(alpha2)},
                            ],
                        },
                        second_layer,
                    )
                if best_error is not None and best_error <= perfect_threshold:
                    break
    
        if len(candidate_subset) >= 3 and (best_error is None or best_error > perfect_threshold):
            for p_idx in candidate_subset:
                primary_linear = linear_cache.get(p_idx)
                if primary_linear is None:
                    continue
                primary_rgb_int = rgb_cache.get(p_idx, (0, 0, 0))
                early_exit = False
                for s_idx in candidate_subset:
                    if s_idx == p_idx:
                        continue
                    secondary_linear = linear_cache.get(s_idx)
                    if secondary_linear is None:
                        continue
                    secondary_rgb_int = rgb_cache.get(s_idx, (0, 0, 0))
                    for t_idx in candidate_subset:
                        if t_idx == p_idx or t_idx == s_idx:
                            continue
                        tertiary_linear = linear_cache.get(t_idx)
                        if tertiary_linear is None:
                            continue
                        tertiary_rgb_int = rgb_cache.get(t_idx, (0, 0, 0))
                        try:
                            matrix = np.column_stack(
                                (
                                    primary_linear - bg_linear,
                                    secondary_linear - bg_linear,
                                    tertiary_linear - bg_linear,
                                )
                            )
                            weights, *_ = np.linalg.lstsq(matrix, target_linear - bg_linear, rcond=None)
                        except Exception:
                            continue
                        weights = np.asarray(weights, dtype=np.float32).reshape(-1)
                        if weights.size != 3:
                            continue
                        weights = np.clip(weights, 0.0, 1.0)
                        if float(np.sum(weights)) > 1.0 + 1e-6:
                            weights = self._project_simplex(weights, z=1.0)
                        alpha3 = float(max(0.0, min(1.0, weights[2])))
                        base = 1.0 - alpha3
                        if base <= 1e-6:
                            alpha2 = 0.0
                            alpha1 = 0.0
                        else:
                            alpha2 = float(max(0.0, min(1.0, weights[1] / base)))
                            base2 = base * (1.0 - alpha2)
                            if base2 <= 1e-6:
                                alpha1 = 0.0
                            else:
                                alpha1 = float(max(0.0, min(1.0, weights[0] / base2)))
                        first_layer = self._blend_linear_rgb(bg_linear, primary_linear, alpha1)
                        second_layer = self._blend_linear_rgb(first_layer, secondary_linear, alpha2)
                        third_layer = self._blend_linear_rgb(second_layer, tertiary_linear, alpha3)
                        _record_solution(
                            {
                                "primary_idx": p_idx,
                                "primary_rgb": primary_rgb_int,
                                "secondary_idx": s_idx,
                                "secondary_rgb": secondary_rgb_int,
                                "tertiary_idx": t_idx,
                                "tertiary_rgb": tertiary_rgb_int,
                                "alpha1": float(alpha1),
                                "alpha2": float(alpha2),
                                "alpha3": float(alpha3),
                                "layers": [
                                    {"idx": p_idx, "alpha": float(alpha1)},
                                    {"idx": s_idx, "alpha": float(alpha2)},
                                    {"idx": t_idx, "alpha": float(alpha3)},
                                ],
                            },
                            third_layer,
                        )
                        if best_error is not None and best_error <= perfect_threshold:
                            early_exit = True
                            break
                    if early_exit:
                        break
                if early_exit:
                    break
    
        if best_solution is None:
            return best_single_plan

        error_val = best_solution.get("error", None)
        delta_val = best_solution.get("delta_e", None)
        fallback_needed = False
        if error_val is not None and error_val > 8e-4:
            fallback_needed = True
        if delta_val is not None and delta_val > 6.0:
            fallback_needed = True
        if (
            best_single_plan is not None
            and (
                best_single_delta is not None and (delta_val is None or delta_val >= best_single_delta - 0.25)
                or best_solution.get("error", float("inf")) >= best_single_plan.get("error", float("inf")) - 1e-9
            )
        ):
            fallback_needed = True

        if fallback_needed:
            best_idx = None
            best_score = None
            for idx in candidate_set:
                entry = by_index.get(idx)
                if not entry:
                    continue
                lab_val = entry.get("lab")
                if lab_val is None:
                    continue
                try:
                    lab_arr = np.asarray(lab_val, dtype=np.float32)
                except Exception:
                    continue
                if lab_arr.shape[-1] != 3:
                    continue
                diff = target_lab - lab_arr
                score = float(np.dot(diff, diff))
                if best_score is None or score < best_score:
                    best_score = score
                    best_idx = idx
            if best_idx is not None:
                fallback_entry = by_index.get(best_idx)
                if fallback_entry is not None:
                    fallback_rgb = fallback_entry.get("rgb")
                    if fallback_rgb is not None:
                        try:
                            fallback_rgb = tuple(
                                int(max(0, min(255, round(float(v))))) for v in fallback_rgb
                            )
                        except Exception:
                            fallback_rgb = None
                    if fallback_rgb is not None:
                        try:
                            fallback_linear = self._srgb_to_linear_rgb(fallback_rgb)
                            fallback_layer = self._blend_linear_rgb(bg_linear, fallback_linear, 1.0)
                            fallback_plan = {
                                "primary_idx": best_idx,
                                "primary_rgb": fallback_rgb,
                                "secondary_idx": None,
                                "secondary_rgb": None,
                                "tertiary_idx": None,
                                "tertiary_rgb": None,
                                "alpha1": 1.0,
                                "alpha2": 0.0,
                                "alpha3": 0.0,
                                "layers": [
                                    {"idx": best_idx, "alpha": 1.0},
                                ],
                            }
                            fallback_error = float(np.sum((fallback_layer - target_linear) ** 2))
                            fallback_plan["result_rgb"] = self._linear_to_srgb_rgb(fallback_layer)
                            fallback_plan["target_rgb"] = target_tuple
                            fallback_plan["error"] = fallback_error
                            try:
                                fall_lab = self._srgb_to_lab_single(fallback_plan["result_rgb"])
                                fall_lab_arr = np.asarray(fall_lab, dtype=np.float32)
                                if fall_lab_arr.shape[-1] == 3:
                                    fallback_plan["delta_e"] = float(
                                        np.linalg.norm(fall_lab_arr - target_lab)
                                    )
                            except Exception:
                                log.debug("ignored exception in fall_lab = self._srgb_to_lab_single(fallback_plan['result_rgb'])", exc_info=True)
                            current_delta = best_solution.get("delta_e", float("inf"))
                            fallback_delta = fallback_plan.get("delta_e", float("inf"))
                            if (
                                fallback_delta + 1e-6 < current_delta
                                or fallback_plan["error"] + 1e-9 < best_solution.get("error", float("inf"))
                            ):
                                best_solution = fallback_plan
                        except Exception:
                            log.debug('ignored exception in fallback_linear = self._srgb_to_linear_rgb(fallback_rgb)', exc_info=True)
            if (
                best_single_plan is not None
                and best_single_delta is not None
                and best_solution.get("delta_e", float("inf")) >= best_single_delta - 0.25
            ):
                return best_single_plan

        if (
            best_single_plan is not None
            and best_single_delta is not None
            and best_solution.get("delta_e", float("inf")) >= best_single_delta - 0.25
        ):
            return best_single_plan

        return best_solution

    def _apply_manual_palette_universal(self, pixels_for_clustering, manual_cache, h_brush_map: int, w_brush_map: int) -> bool:
        entries = manual_cache.get("entries") or []
        if not entries or pixels_for_clustering.size == 0:
            return False
        unique_pixels = np.unique(pixels_for_clustering, axis=0)
        k_actual = min(self.k_clusters, len(unique_pixels))
        if k_actual < 1:
            return False
        if len(pixels_for_clustering) < k_actual:
            k_actual = len(pixels_for_clustering)
            if k_actual < 1:
                return False

        self._manual_palette_cluster_plan = {}
        self._manual_palette_target_rgb = {}
        self._manual_palette_mix_solution_cache = {}

        try:
            canvas_hex = "#{:02X}{:02X}{:02X}".format(*self.manual_mix_canvas_rgb)
        except Exception:
            canvas_hex = "#{:02X}{:02X}{:02X}".format(255, 255, 255)
        self._log(f"Manual mix canvas tone applied: {canvas_hex}")

        try:
            self._log(f"Manual universal mode: clustering {len(pixels_for_clustering)} px into {k_actual} colors.")
            kmeans = KMeans(
                n_clusters=k_actual,
                random_state=42,
                init="k-means++",
                n_init="auto",
                algorithm="lloyd",
            )
            pixels_float = pixels_for_clustering.astype(float)
            kmeans.fit(pixels_float)
            non_bg_labels = kmeans.labels_
        except Exception as exc:
            self._log(f"Manual universal mode KMeans error: {exc}", True)
            return False

        current_non_bg_idx = 0
        total_labels = len(non_bg_labels)
        for r_idx in range(h_brush_map):
            for c_idx in range(w_brush_map):
                if self.background_mask[r_idx, c_idx]:
                    continue
                if current_non_bg_idx >= total_labels:
                    break
                self.cluster_map[r_idx, c_idx] = int(non_bg_labels[current_non_bg_idx])
                current_non_bg_idx += 1

        centers = kmeans.cluster_centers_.astype(float)
        counts = np.bincount(non_bg_labels, minlength=k_actual)

        temp_palette_color = []
        plan_map: dict[int, dict] = {}
        target_rgb_map: dict[int, tuple[int, int, int]] = {}
        mix_errors: list[float] = []

        for cid in range(k_actual):
            count_val = int(counts[cid])
            if count_val <= 0:
                continue
            center_vec = centers[cid]
            center_rgb = tuple(
                int(max(0, min(255, round(float(v))))) for v in center_vec
            )
            plan = self._resolve_manual_mix_solution(center_rgb, manual_cache)
            if not plan:
                fallback_idx = None
                candidates = self._manual_palette_candidate_indices(
                    self._srgb_to_lab_single(center_rgb),
                    manual_cache,
                    limit=1,
                )
                if candidates:
                    fallback_idx = candidates[0]
                if fallback_idx is None:
                    continue
                fallback_entry = manual_cache.get("by_index", {}).get(fallback_idx)
                if fallback_entry is None:
                    continue
                fallback_rgb = tuple(
                    int(max(0, min(255, round(float(v))))) for v in fallback_entry.get("rgb", (0, 0, 0))
                )
                plan = {
                    "primary_idx": fallback_idx,
                    "primary_rgb": fallback_rgb,
                    "secondary_idx": None,
                    "secondary_rgb": None,
                    "tertiary_idx": None,
                    "tertiary_rgb": None,
                    "alpha1": 1.0,
                    "alpha2": 0.0,
                    "alpha3": 0.0,
                    "result_rgb": fallback_rgb,
                    "target_rgb": center_rgb,
                    "error": 0.0,
                    "layers": [
                        {"idx": fallback_idx, "alpha": 1.0},
                    ],
                }
            plan["cluster_id"] = cid
            plan["count"] = count_val
            plan["alpha1"] = max(0.0, min(1.0, float(plan.get("alpha1", self.manual_palette_mix_alpha))))
            plan["alpha2"] = max(0.0, min(1.0, float(plan.get("alpha2", 0.0))))
            plan["alpha3"] = max(0.0, min(1.0, float(plan.get("alpha3", 0.0))))
            layers = plan.get("layers") or []
            normalized_layers: list[dict[str, float | int]] = []
            for layer_idx, layer in enumerate(layers):
                try:
                    palette_idx = int(layer.get("idx"))
                except Exception:
                    continue
                alpha_val = layer.get("alpha", plan["alpha1"] if layer_idx == 0 else 1.0)
                try:
                    alpha_float = float(alpha_val)
                except Exception:
                    alpha_float = plan["alpha1"] if layer_idx == 0 else 1.0
                normalized_layers.append({
                    "idx": palette_idx,
                    "alpha": max(0.0, min(1.0, alpha_float)),
                })
            if not normalized_layers:
                primary_idx = plan.get("primary_idx")
                if primary_idx is not None:
                    try:
                        normalized_layers.append({"idx": int(primary_idx), "alpha": plan["alpha1"]})
                    except Exception:
                        log.debug("ignored exception in normalized_layers.append({'idx': int(primary_idx), 'alpha': plan['alpha1']})", exc_info=True)
            plan["layers"] = normalized_layers
            display_rgb = plan.get("result_rgb") or plan.get("target_rgb") or center_rgb
            display_rgb = tuple(int(max(0, min(255, round(float(v))))) for v in display_rgb)
            temp_palette_color.append(
                {
                    "hex": "#{:02X}{:02X}{:02X}".format(*display_rgb),
                    "id": cid,
                    "count": count_val,
                    "rgb": display_rgb,
                    "luminance": self._calculate_luminance(display_rgb),
                }
            )
            plan_map[cid] = plan
            target_rgb_map[cid] = tuple(int(v) for v in plan.get("target_rgb", center_rgb))
            if "error" in plan and isinstance(plan["error"], (int, float)):
                mix_errors.append(float(plan["error"]))

        if not plan_map:
            return False

        self._manual_palette_cluster_plan = plan_map
        self._manual_palette_target_rgb = target_rgb_map
        self._cluster_uses_manual_palette = True

        sorted_palette_color = self._sort_palette_entries(temp_palette_color)
        self.color_palette = [(p["hex"], p["id"], p["count"], p["rgb"]) for p in sorted_palette_color]
        sort_desc = f"tone={self.tone_sequence}, areas={self.area_sequence}"
        self._log(f"Manual universal palette prepared ({sort_desc}): {len(self.color_palette)} colors")
        if mix_errors:
            avg_err = float(np.mean(mix_errors))
            max_err = float(np.max(mix_errors))
            self._log(f"Manual universal mix error stats: avg={avg_err:.6f}, max={max_err:.6f}")
        return True

    def _draw_with_manual_mix(self, cluster_id: int, hex_color: str, x0: int, y0: int, initial_mask_bool: np.ndarray) -> bool:
        if not self.manual_palette_mix_enabled:
            return False
        manual_cache = self._get_manual_palette_cache()
        if not manual_cache:
            return False
        self._ensure_drawn_mask_shape()
        plan = self._manual_palette_cluster_plan.get(cluster_id) if cluster_id is not None else None
        if plan is None:
            return False
        manual_by_index = manual_cache.get("by_index") or {}

        layers_data = plan.get("layers") or []
        if not layers_data:
            layers_data = []
            for key, alpha_key in (("primary_idx", "alpha1"), ("secondary_idx", "alpha2"), ("tertiary_idx", "alpha3")):
                idx_val = plan.get(key)
                if idx_val is None:
                    continue
                alpha_val = plan.get(alpha_key, self.manual_palette_mix_alpha if key == "primary_idx" else 1.0)
                layers_data.append({"idx": idx_val, "alpha": alpha_val})

        valid_layers: list[dict[str, float | int]] = []
        for idx_layer, layer in enumerate(layers_data):
            try:
                palette_idx = int(layer.get("idx"))
            except Exception:
                continue
            alpha_val = layer.get("alpha", self.manual_palette_mix_alpha if idx_layer == 0 else 1.0)
            try:
                alpha_float = float(alpha_val)
            except Exception:
                alpha_float = self.manual_palette_mix_alpha if idx_layer == 0 else 1.0
            if idx_layer > 0 and alpha_float <= 1e-4:
                continue
            valid_layers.append({"idx": palette_idx, "alpha": max(0.0, min(1.0, alpha_float))})

        if not valid_layers:
            return False

        requires_alpha_slider = any(abs(layer["alpha"] - 1.0) > 1e-3 for layer in valid_layers)
        if requires_alpha_slider and self.alpha_slider_params is None:
            if not getattr(self, "_manual_mix_warned_alpha", False):
                self._log(tr("status_error_alpha_slider_not_calibrated"), True)
                self._manual_mix_warned_alpha = True
            return False
        self._manual_mix_warned_alpha = False

        rows, cols = np.where(initial_mask_bool)
        if self.drawn_mask is not None:
            try:
                mask_valid = (
                    (rows >= 0)
                    & (cols >= 0)
                    & (rows < self.drawn_mask.shape[0])
                    & (cols < self.drawn_mask.shape[1])
                )
                if not np.all(mask_valid):
                    rows = rows[mask_valid]
                    cols = cols[mask_valid]
            except Exception:
                log.debug('ignored exception in mask_valid = (rows >= 0) & (cols >= 0) & (rows < self.drawn_mask.shape[0]) & ...', exc_info=True)
        if rows.size == 0:
            return True

        rows = rows.astype(np.intp, copy=False)
        cols = cols.astype(np.intp, copy=False)
        multi_pass = len(valid_layers) > 1

        try:
            if len(valid_layers) >= 2:
                try:
                    primary_entry = manual_by_index.get(valid_layers[0]["idx"])
                    secondary_entry = manual_by_index.get(valid_layers[1]["idx"])
                    if primary_entry and secondary_entry:
                        primary_hex = primary_entry.get("hex", hex_color)
                        secondary_hex = secondary_entry.get("hex", hex_color)
                        alpha1 = valid_layers[0]["alpha"]
                        alpha2 = valid_layers[1]["alpha"]
                        self._log(
                            tr(
                                "status_info_manual_mix_solution",
                                primary=primary_hex,
                                secondary=secondary_hex,
                                alpha1=alpha1,
                                alpha2=alpha2,
                            )
                        )
                except Exception:
                    log.debug("ignored exception in primary_entry = manual_by_index.get(valid_layers[0]['idx'])", exc_info=True)
                if len(valid_layers) > 2:
                    try:
                        extras = []
                        for extra_layer in valid_layers[2:]:
                            extra_entry = manual_by_index.get(extra_layer["idx"])
                            if not extra_entry:
                                continue
                            extras.append(f"{extra_entry.get('hex', '#000000')}@{extra_layer['alpha']:.2f}")
                        if extras:
                            self._log(f"Manual mix extra layers: {', '.join(extras)}")
                    except Exception:
                        log.debug('ignored exception in extras = []', exc_info=True)

            for layer_idx, layer in enumerate(valid_layers):
                palette_idx = layer.get("idx")
                entry = manual_by_index.get(palette_idx)
                if entry is None:
                    continue
                alpha_val = float(layer.get("alpha", 1.0))
                self._manual_palette_force_entry = palette_idx
                self._manual_palette_force_alpha = alpha_val
                self._manual_palette_force_disable_mix = True
                hex_value = entry.get("hex", hex_color)
                self.pick_color(hex_value, cluster_id if layer_idx == 0 else palette_idx)
                if self._automation_cancelled():
                    return True
                if layer_idx == 0:
                    if not np.any(initial_mask_bool):
                        return True
                    if not self._draw_cluster_with_algorithm(cluster_id, x0, y0) or self._automation_cancelled():
                        return True
                    if self.drawn_mask is not None and multi_pass:
                        self.drawn_mask[rows, cols] = False
                else:
                    self._draw_cluster_with_algorithm(cluster_id, x0, y0)
                    if self._automation_cancelled():
                        return True
            return True
        finally:
            if self.drawn_mask is not None:
                try:
                    self.drawn_mask[rows, cols] = True
                except Exception:
                    log.debug('ignored exception in self.drawn_mask[rows, cols] = True', exc_info=True)
            self._manual_palette_force_entry = None
            self._manual_palette_force_alpha = None
            self._manual_palette_force_disable_mix = False

    def _perform_draw_for_one_color(self, color_data_tuple):
        if self.draw_region is None or self.cluster_map is None or self.drawn_mask is None:
            return
        x0, y0, _, _ = self.draw_region
        hex_color, cluster_id, pcount, _ = color_data_tuple

        phase = getattr(self, "_semantic_active_phase", None)
        if phase is not None and cluster_id is not None:
            # Two-pass draw: a color with nothing to paint in THIS phase is
            # skipped before pick_color — zero wasted palette clicks.
            region_mask = None
            try:
                region_mask = self._semantic_region_planes_mask()
            except Exception:
                log.debug("semantic region mask failed mid-draw", exc_info=True)
            if region_mask is not None and region_mask.shape == self.cluster_map.shape:
                pending = ((self.cluster_map == cluster_id) & (~self.drawn_mask)
                           & (region_mask == int(phase)))
                if not bool(np.any(pending)):
                    self._telemetry_emit("color_skipped", color=hex_color,
                                         cluster_id=cluster_id, phase=int(phase))
                    return

        brush_pass = getattr(self, "_brush_pass", None)
        if brush_pass is not None and cluster_id is not None and not self._brush_pass_has_work(cluster_id, brush_pass):
            return

        try:
            log_idx = self.color_palette.index(color_data_tuple) + 1
        except ValueError:
            log_idx = self.current_color_index + 1

        phase_note = f", phase {phase}" if phase is not None else ""
        if brush_pass is not None:
            phase_note += f", {brush_pass} pass"
        self._log(f"[DRAW {log_idx}/{len(self.color_palette)}] Color {hex_color} (ID:{cluster_id}, ~{pcount}px{phase_note})...")
        self._telemetry_emit("color_start", color=hex_color, cluster_id=cluster_id, approx_px=pcount,
                             phase=(int(phase) if phase is not None else None))
        _tm_color_t0 = time.perf_counter()
        try:
            attempt_manual_mix = (
                self.color_picking_method == "manual_palette"
                and self.manual_palette_mix_enabled
                and cluster_id is not None
            )
            if attempt_manual_mix:
                try:
                    initial_mask_bool = (self.cluster_map == cluster_id) & (~self.drawn_mask)
                    if phase is not None:
                        region_mask = self._semantic_region_planes_mask()
                        if region_mask is not None and region_mask.shape == initial_mask_bool.shape:
                            initial_mask_bool &= (region_mask == int(phase))
                except Exception:
                    initial_mask_bool = None
                if initial_mask_bool is not None and np.any(initial_mask_bool):
                    if self._draw_with_manual_mix(cluster_id, hex_color, x0, y0, initial_mask_bool):
                        self._log(f"Completed processing {hex_color}.")
                        return

            self.pick_color(hex_color, cluster_id)
            if self._automation_cancelled():
                return

            if self._draw_cluster_with_algorithm(cluster_id, x0, y0):
                self._log(f"Completed processing {hex_color}.")
        finally:
            self._telemetry_emit(
                "color_end", color=hex_color,
                duration_s=round(time.perf_counter() - _tm_color_t0, 3),
                phase=(int(phase) if phase is not None else None),
            )
    def draw_image(self):
        if self._drawing_cancel.is_set() or self._quiet():
            return False
        self._cancel_active_captures()
        if self._reset_progress_on_next_start:
            self._reset_draw_progress(notify=False)
            self._reset_progress_on_next_start = False

        if self.color_picking_method == "hex_field":
            if self.hex_input_coord is None:
                err_msg = tr("status_error_hex_not_set")
                self._log(err_msg, True)
                self.status_callback("show_hex_error_popup:" + err_msg)
                return False
        elif self.color_picking_method == "hsv_palette":
            if not self.circle_params_calib:
                err_msg = tr("status_error_hsv_circle_not_calibrated")
                self._log(err_msg, True)
                self.status_callback("show_calibration_error_popup:" + err_msg)
                return False
            if not self.slider_params_calib:
                err_msg = tr("status_error_hsv_slider_not_calibrated")
                self._log(err_msg, True)
                self.status_callback("show_calibration_error_popup:" + err_msg)
                return False
        elif self.color_picking_method == "manual_palette":
            if not getattr(self, 'manual_palette_coords', None):
                err_msg = tr("status_error_manual_palette_not_set")
                self._log(err_msg, True)
                self.status_callback(err_msg)
                return False
        elif self.color_picking_method == "screen_palette":
            sp = getattr(self, "screen_palette_calib", None)
            if not (isinstance(sp, dict) and sp.get("lab") is not None and len(sp["lab"]) > 0):
                err_msg = tr("Палитра не откалибрована! Откалибруйте «Палитру рамкой» в Настройках.")
                self._log(err_msg, True)
                self.status_callback("show_calibration_error_popup:" + err_msg)
                return False
        elif self.color_picking_method == "wheel_square":
            from engine.olegpainter import wheel_picker
            if not wheel_picker.valid(getattr(self, "wheel_square_calib", None)):
                err_msg = "Цветовое колесо не откалибровано: обведите его рамкой (кнопка «Настроить цвет»)."
                self._log(err_msg, True)
                self.status_callback("show_calibration_error_popup:" + err_msg)
                return False
        else:
            unknown_method_err = tr("status_error_unknown_color_method", method=self.color_picking_method)
            self._log(unknown_method_err, True)
            self.status_callback("show_hex_error_popup:" + unknown_method_err)
            return False

        if self.draw_region is None or not self.color_palette or self.cluster_map is None:
            self._log(tr("status_error_no_area_or_image"), True)
            self.status_callback(tr("status_error_no_area_or_image"))
            return False

        if self.draw_with_layers_enabled and not self.target_app_layer_coords:
            self._log(tr("status_error_no_app_layers_defined"), True)
            self.status_callback(tr("status_error_no_app_layers_defined"))
            return False

        if self.drawing_enabled:
            self.drawing_enabled = False
            if self.pause_start_time is None:
                self.pause_start_time = time.time()
            self._log("Paused.")
            self._emit_drawing_event(DrawingPhase.PAUSED)
            self.status_callback(tr("status_paused"))
            return False
        else:
            self.stop_flag = False
            if self.pause_start_time is not None:
                self.paused_time_accumulated += time.time() - self.pause_start_time
                self.pause_start_time = None
            self.dynamic_brush_session_active = False
            should_prepare_dynamic = self._dynamic_brush_requested_for_current_draw() and (
                self.drawing_thread is None or not self.drawing_thread.is_alive()
            )
            if should_prepare_dynamic:
                self.prepare_dynamic_brush_for_draw()
            if self._drawing_cancel.is_set() or self._quiet():
                self.stop_flag = True
                self.drawing_enabled = False
                return False
            self.drawing_enabled = True
            if self.drawing_start_time is None:
                self.drawing_start_time = time.time()
            self._log("Start/Resume.")
            self.status_callback(tr("status_drawing"))

            if self.drawn_mask is not None and self.cluster_map is not None and \
                    self.cluster_map.size > 0 and \
                    np.all(self.drawn_mask[self.cluster_map != -1]):
                self._log("Drawing already completed (all pixels seem drawn). Resetting progress.")
                self._reset_draw_progress()
                self.drawing_enabled = True
                self.drawing_start_time = time.time()
                self.status_callback(tr("status_drawing"))

            if self.current_color_index >= len(self.color_palette) and len(self.color_palette) > 0:
                self._log("Palette was fully processed in the previous run. Resetting progress for a new drawing.")
                self._reset_draw_progress(notify=False)
                self.stop_flag = False
                self.drawing_enabled = True
                self.drawing_start_time = time.time()


            if self.drawing_thread is None or not self.drawing_thread.is_alive():
                if self._drawing_cancel.is_set() or self._quiet():
                    self.stop_flag = True
                    self.drawing_enabled = False
                    return False
                self._log("Создание нового потока рисования.")
                self.drawing_thread = threading.Thread(target=self.draw_colors_thread, daemon=True,
                                                       args=(self.drawing_run_id,),
                                                       name="OlegPainterThread")
                self._emit_drawing_event(DrawingPhase.RUNNING)
                try:
                    self.drawing_thread.start()
                except Exception:
                    self.drawing_thread = None
                    self.drawing_enabled = False
                    self.drawing_start_time = None
                    raise
            else:

                self._log("Возобновление существующего потока рисования.")
                self._emit_drawing_event(DrawingPhase.RUNNING)
            return True

    def _color_layer_distribution(self, total_palette_colors: int | None = None, num_app_layers: int | None = None) -> list[int]:
        if total_palette_colors is None:
            total_palette_colors = len(self.color_palette or [])
        if num_app_layers is None:
            num_app_layers = len(self.target_app_layer_coords or [])
        try:
            total_palette_colors = max(0, int(total_palette_colors))
            num_app_layers = max(0, int(num_app_layers))
        except Exception:
            return []
        if num_app_layers <= 0:
            return []
        base_count = total_palette_colors // num_app_layers
        remainder = total_palette_colors % num_app_layers
        distribution = [base_count] * num_app_layers
        for idx in range(remainder):
            distribution[idx] += 1
        return distribution

    def _color_layer_assignments(self) -> dict[int, dict[str, object]]:
        assignments: dict[int, dict[str, object]] = {}
        if not getattr(self, "draw_with_layers_enabled", False):
            return assignments
        coords = getattr(self, "target_app_layer_coords", None) or []
        if not coords:
            return assignments
        distribution = self._color_layer_distribution(len(self.color_palette or []), len(coords))
        palette_cursor_idx = 0
        total_layers = len(coords)
        for layer_idx, color_count in enumerate(distribution):
            if palette_cursor_idx >= len(self.color_palette or []):
                break
            coord = coords[layer_idx]
            for _ in range(max(0, int(color_count))):
                if palette_cursor_idx >= len(self.color_palette or []):
                    break
                color_data = self.color_palette[palette_cursor_idx]
                try:
                    cluster_id = int(color_data[1])
                except Exception:
                    palette_cursor_idx += 1
                    continue
                assignments[cluster_id] = {
                    "layer_index": int(layer_idx),
                    "display_num": int(layer_idx + 1),
                    "coord": coord,
                    "total_layers": int(total_layers),
                    "colors_on_layer": int(color_count),
                }
                palette_cursor_idx += 1
        return assignments

    def _activate_app_layer(self, layer_index: int, *, colors_on_this_layer: int | None = None) -> bool:
        coords = getattr(self, "target_app_layer_coords", None) or []
        if not coords:
            return False
        try:
            layer_index = int(layer_index)
        except Exception:
            return False
        if layer_index < 0 or layer_index >= len(coords):
            return False
        current_app_layer_coord = coords[layer_index]
        current_app_layer_display_num = layer_index + 1
        total_layers = len(coords)
        self._log(
            f"Selecting app layer {current_app_layer_display_num}/{total_layers} at {current_app_layer_coord}."
        )
        if colors_on_this_layer is not None and self.status_callback:
            try:
                self.status_callback(
                    tr(
                        "status_selecting_app_layer_auto",
                        current=current_app_layer_display_num,
                        total_app_layers=total_layers,
                        colors_on_this_layer=max(0, int(colors_on_this_layer)),
                    )
                )
            except Exception:
                log.debug("ignored exception in self.status_callback(tr('status_selecting_app_layer_auto', current=current_ap...", exc_info=True)
        try:
            self._click_abs(current_app_layer_coord[0], current_app_layer_coord[1])
            self._input_click(button='left')
        except Exception as exc:
            self._log(f"App layer selection failed for layer {current_app_layer_display_num}: {exc}", True)
            self.stop_flag = True
            return False
        if not self._sleep_with_abort(0.25, step=0.05):
            return False
        return not self._automation_cancelled()

    def _expand_post_draw_repair_cluster_mask(self, cluster_mask: np.ndarray, cluster_id: int) -> np.ndarray | None:
        if self.cluster_map is None:
            return None
        try:
            mask = np.asarray(cluster_mask, dtype=bool)
        except Exception:
            return None
        if mask.shape != self.cluster_map.shape:
            return None
        if not np.any(mask):
            return mask.astype(bool, copy=True)
        try:
            allowed = np.asarray(self.cluster_map == int(cluster_id), dtype=bool)
        except Exception:
            return mask.astype(bool, copy=True)
        iterations = max(0, int(getattr(self, "_POST_DRAW_REPAIR_CELL_DILATION", 1) or 0))
        expanded = mask.astype(np.uint8, copy=False)
        if iterations > 0:
            kernel = np.ones((3, 3), dtype=np.uint8)
            expanded = cv2.dilate(expanded, kernel, iterations=iterations)
        return expanded.astype(bool, copy=False) & allowed

    def _cluster_cell_mask_to_pixel_mask(self, cluster_mask: np.ndarray, region_w: int, region_h: int) -> np.ndarray | None:
        try:
            cell_mask = np.asarray(cluster_mask, dtype=bool)
        except Exception:
            return None
        if region_w <= 0 or region_h <= 0 or cell_mask.ndim != 2 or not np.any(cell_mask):
            return None
        brush = max(1, int(getattr(self, "brush_size", 1) or 1))
        pixel_mask = np.zeros((region_h, region_w), dtype=bool)
        rows, cols = np.where(cell_mask)
        if rows.size == 0:
            return None
        for row, col in zip(rows.tolist(), cols.tolist()):
            top = max(0, int(row) * brush)
            left = max(0, int(col) * brush)
            bottom = min(region_h, top + brush)
            right = min(region_w, left + brush)
            if bottom > top and right > left:
                pixel_mask[top:bottom, left:right] = True
        return pixel_mask







    def _run_post_draw_repair(self, baseline_rgb) -> dict:
        payload = {
            "pass_index": 0,
            "total_passes": max(1, int(getattr(self, "post_draw_repair_passes", 1) or 1)),
            "detected_cells": 0,
            "repaired_cells": 0,
            "remaining_cells": 0,
            "unverifiable_cells": 0,
            "regions": [],
            "overlay": None,
            "draw_region": getattr(self, "draw_region", None),
            "message": "",
        }
        if not bool(getattr(self, "post_draw_repair_enabled", False)):
            self._notify_post_draw_repair(payload)
            return payload

        total_passes = max(1, min(3, int(getattr(self, "post_draw_repair_passes", 1) or 1)))
        payload["total_passes"] = total_passes
        target_mask = self._expected_drawn_cell_mask()
        expected_preview = getattr(self, "quantized_preview_image", None)
        if target_mask is None or not np.any(target_mask) or not isinstance(expected_preview, Image.Image):
            self._notify_post_draw_repair(payload)
            return payload

        try:
            expected_rgb = np.asarray(expected_preview.convert("RGB"), dtype=np.uint8)
        except Exception:
            expected_rgb = None
        baseline_samples = self._compute_cell_sample_map_from_rgb(baseline_rgb) if baseline_rgb is not None else None
        expected_samples = self._compute_cell_sample_map_from_rgb(expected_rgb) if expected_rgb is not None else None
        if baseline_samples is None or expected_samples is None:
            warn_message = tr("status_post_draw_repair_capture_failed")
            self._log(warn_message, True)
            if self.status_callback:
                try:
                    self.status_callback(warn_message)
                except Exception:
                    log.debug('ignored exception in self.status_callback(warn_message)', exc_info=True)
            payload["message"] = warn_message
            self._notify_post_draw_repair(payload)
            return payload

        if self.status_callback:
            try:
                self.status_callback(tr("status_post_draw_repair_started"))
            except Exception:
                log.debug("ignored exception in self.status_callback(tr('status_post_draw_repair_started'))", exc_info=True)
        layer_assignments = self._color_layer_assignments()

        def _capture_analysis():
            while not self.drawing_enabled and not self.stop_flag:
                if not self._sleep_with_abort(0.1, step=0.05):
                    return None
            if self._automation_cancelled():
                return None
            self._mouse_up_with_settle()
            self._mouse_up_with_settle("right")
            if not self._sleep_with_abort(self._POST_DRAW_REPAIR_CAPTURE_SETTLE, step=0.02):
                return None
            final_rgb = self._capture_draw_region_rgb()
            if final_rgb is None:
                return None
            final_samples = self._compute_cell_sample_map_from_rgb(final_rgb)
            if final_samples is None:
                return None
            analysis = self._analyze_post_draw_repair_samples(
                baseline_samples,
                final_samples,
                expected_samples,
                target_mask,
            )
            if analysis is None:
                return None
            partial_holes = self._compute_partial_post_draw_repair_holes(
                baseline_rgb,
                final_rgb,
                expected_rgb,
                target_mask,
            )
            if isinstance(partial_holes, np.ndarray) and partial_holes.shape == analysis.get("hole_mask", np.zeros((0, 0))).shape:
                combined_holes = np.asarray(analysis.get("hole_mask"), dtype=bool) | partial_holes.astype(bool, copy=False)
                analysis = dict(analysis)
                analysis["partial_hole_mask"] = partial_holes.astype(bool, copy=False)
                analysis["hole_mask"] = combined_holes
                analysis["detected_cells"] = int(np.count_nonzero(combined_holes))
            return analysis

        analysis = _capture_analysis()
        if analysis is None:
            warn_message = tr("status_post_draw_repair_capture_failed")
            self._log(warn_message, True)
            if self.status_callback:
                try:
                    self.status_callback(warn_message)
                except Exception:
                    log.debug('ignored exception in self.status_callback(warn_message)', exc_info=True)
            payload["message"] = warn_message
            self._notify_post_draw_repair(payload)
            return payload

        initial_detected = int(analysis.get("detected_cells", 0))
        unverifiable_cells = int(analysis.get("unverifiable_cells", 0))
        remaining_mask = np.asarray(analysis.get("hole_mask"), dtype=bool)
        payload["detected_cells"] = initial_detected
        payload["remaining_cells"] = initial_detected
        payload["unverifiable_cells"] = unverifiable_cells

        if initial_detected <= 0:
            message = tr("status_post_draw_repair_no_holes")
            payload["message"] = message
            if self.status_callback:
                try:
                    self.status_callback(message)
                except Exception:
                    log.debug('ignored exception in self.status_callback(message)', exc_info=True)
            if unverifiable_cells > 0 and self.status_callback:
                try:
                    self.status_callback(tr("status_post_draw_repair_unverifiable", count=unverifiable_cells))
                except Exception:
                    log.debug("ignored exception in self.status_callback(tr('status_post_draw_repair_unverifiable', count=unverif...", exc_info=True)
            self._notify_post_draw_repair(payload)
            return payload

        if self.status_callback:
            try:
                self.status_callback(tr("status_post_draw_repair_found", count=initial_detected))
            except Exception:
                log.debug("ignored exception in self.status_callback(tr('status_post_draw_repair_found', count=initial_detect...", exc_info=True)

        completed_passes = 0
        for pass_index in range(1, total_passes + 1):
            if self._automation_cancelled() or not np.any(remaining_mask):
                break
            hole_count = int(np.count_nonzero(remaining_mask))
            if self.status_callback:
                try:
                    self.status_callback(
                        tr(
                            "status_post_draw_repair_pass",
                            current=pass_index,
                            total=total_passes,
                            count=hole_count,
                        )
                    )
                except Exception:
                    log.debug("ignored exception in self.status_callback(tr('status_post_draw_repair_pass', current=pass_index, t...", exc_info=True)
            self._perform_post_draw_repair_pass(remaining_mask, layer_assignments)
            analysis = _capture_analysis()
            if analysis is None:
                # проход прерван (cancel) — не засчитываем его как выполненный
                break
            completed_passes = pass_index
            remaining_mask = np.asarray(analysis.get("hole_mask"), dtype=bool)

        remaining_cells = int(np.count_nonzero(remaining_mask)) if isinstance(remaining_mask, np.ndarray) else 0
        repaired_cells = max(0, initial_detected - remaining_cells)
        payload["pass_index"] = completed_passes
        payload["repaired_cells"] = repaired_cells
        payload["remaining_cells"] = remaining_cells

        if remaining_cells > 0 and isinstance(remaining_mask, np.ndarray):
            regions, overlay = self._build_post_draw_repair_regions(remaining_mask)
            payload["regions"] = regions
            payload["overlay"] = overlay
            message = tr(
                "status_post_draw_repair_remaining",
                remaining=remaining_cells,
                passes=completed_passes,
                total=total_passes,
            )
        else:
            message = tr(
                "status_post_draw_repair_fixed",
                repaired=repaired_cells,
                passes=completed_passes,
            )
        payload["message"] = message
        if self.status_callback:
            try:
                self.status_callback(message)
            except Exception:
                log.debug('ignored exception in self.status_callback(message)', exc_info=True)
        if unverifiable_cells > 0 and self.status_callback:
            try:
                self.status_callback(tr("status_post_draw_repair_unverifiable", count=unverifiable_cells))
            except Exception:
                log.debug("ignored exception in self.status_callback(tr('status_post_draw_repair_unverifiable', count=unverif...", exc_info=True)
        self._notify_post_draw_repair(payload)
        return payload

    def _execute_drawing_with_target_layers(self):
        self._log("Executing drawing with App Layers (automatic color distribution).")

        total_palette_colors = len(self.color_palette)
        num_app_layers = len(self.target_app_layer_coords)

        if total_palette_colors == 0:
            self._log("Нет цветов в палитре для рисования по слоям.", is_error=True)
            self.status_callback(tr("Ошибка: Палитра пуста для слоевого рисования."))
            return
        if num_app_layers == 0:
            self._log("Слои приложения не определены для рисования по слоям.", is_error=True)
            return

        colors_per_layer_distribution = self._color_layer_distribution(total_palette_colors, num_app_layers)
        palette_cursor_idx = 0
        for current_layer_idx in range(num_app_layers):
            if self._automation_cancelled():
                break
            if palette_cursor_idx >= total_palette_colors:
                break

            num_colors_for_this_app_layer = colors_per_layer_distribution[current_layer_idx]

            if num_colors_for_this_app_layer == 0:
                self._log(f"Слой {current_layer_idx + 1}/{num_app_layers} пропускается (0 цветов для рисования).")
                continue

            current_app_layer_coord = self.target_app_layer_coords[current_layer_idx]
            current_app_layer_display_num = current_layer_idx + 1

            self._log(
                f"Выбор слоя приложения {current_app_layer_display_num}/{num_app_layers} в {current_app_layer_coord} для {num_colors_for_this_app_layer} цветов.")
            if not self._activate_app_layer(current_layer_idx, colors_on_this_layer=num_colors_for_this_app_layer):
                break
            if self._automation_cancelled():
                break

            for _ in range(num_colors_for_this_app_layer):
                if self._automation_cancelled() or palette_cursor_idx >= total_palette_colors:
                    break

                while not self.drawing_enabled and not self.stop_flag:
                    if not self._sleep_with_abort(0.1):
                        break
                if self._automation_cancelled():
                    break

                self.current_color_index = palette_cursor_idx
                color_data_to_draw = self.color_palette[palette_cursor_idx]

                hex_c, _, _, _ = color_data_to_draw
                self.status_callback(tr("status_drawing_on_layer",
                                        layer_num=current_app_layer_display_num,
                                        total_layers=num_app_layers,
                                        color_idx=palette_cursor_idx + 1,
                                        total_colors=total_palette_colors,
                                        hex_color=hex_c))

                self._perform_draw_for_one_color(color_data_to_draw)
                if self._automation_cancelled():
                    break
                palette_cursor_idx += 1

        if not self.stop_flag and palette_cursor_idx >= total_palette_colors:
            self.current_color_index = total_palette_colors
            if self.pixel_update_callback:
                self.pixel_update_callback()
        return

    def _execute_drawing_sequentially(self):
        self._log("Executing drawing sequentially.")
        while self.current_color_index < len(self.color_palette) and not self.stop_flag:
            if self._automation_cancelled():
                break
            while not self.drawing_enabled and not self.stop_flag:
                if not self._sleep_with_abort(0.1):
                    break
            if self._automation_cancelled():
                break

            order = getattr(self, "_color_draw_order", None)
            palette_index = order[self.current_color_index] if order else self.current_color_index
            color_data = self.color_palette[palette_index]
            hex_color, _, _, _ = color_data
            self.status_callback(f"Рисуется цвет {self.current_color_index + 1} из {len(self.color_palette)}: {hex_color}")

            self._perform_draw_for_one_color(color_data)
            if self._automation_cancelled():
                break
            self.current_color_index += 1

    # ---------- Session telemetry (phase 0; pure diagnostics, never raises) ----------

    def _telemetry_begin_session(self):
        self._tm_counters = {}
        self._perf = PerfStats()
        try:
            self._telemetry = TelemetrySession(enabled=bool(getattr(self, "telemetry_enabled", True)))
        except Exception:
            self._telemetry = None
            return
        try:
            import importlib.util
            flags = {
                "drawing_algorithm": getattr(self, "drawing_algorithm", None),
                "brush_size": getattr(self, "brush_size", None),
                "area_sequence": getattr(self, "area_sequence", None),
                "tone_sequence": getattr(self, "tone_sequence", None),
                "run_length_merge_enabled": bool(getattr(self, "run_length_merge_enabled", False)),
                "astar_bridge_enabled": bool(getattr(self, "astar_bridge_enabled", False)),
                "fill_traversal_mode": getattr(self, "fill_traversal_mode", "auto"),
                "euler_greedy_pairing_enabled": bool(getattr(self, "euler_greedy_pairing_enabled", False)),
                "area_order_2opt_enabled": bool(getattr(self, "area_order_2opt_enabled", True)),
                "manual_match_space": getattr(self, "manual_match_space", None),
                "dynamic_brush_enabled": bool(getattr(self, "dynamic_brush_enabled", False)),
                "post_draw_repair_enabled": bool(getattr(self, "post_draw_repair_enabled", False)),
                "draw_with_layers_enabled": bool(getattr(self, "draw_with_layers_enabled", False)),
            }
            grid = None
            if self.cluster_map is not None:
                grid = {"h": int(self.cluster_map.shape[0]), "w": int(self.cluster_map.shape[1]),
                        "target_cells": int((self.cluster_map != -1).sum())}
            self._telemetry.emit(
                "session_start",
                flags=flags,
                grid=grid,
                palette_colors=len(getattr(self, "color_palette", []) or []),
                numba=importlib.util.find_spec("numba") is not None,
            )
        except Exception:
            log.debug("telemetry session_start failed", exc_info=True)

    def _tm_count(self, key: str, value: float = 1.0):
        try:
            counters = self._tm_counters
            counters[key] = counters.get(key, 0.0) + value
        except Exception:
            pass

    def _telemetry_emit(self, event: str, **fields):
        session = getattr(self, "_telemetry", None)
        if session is not None:
            session.emit(event, **fields)

    def _telemetry_end_session(self):
        session = getattr(self, "_telemetry", None)
        if session is None:
            return
        try:
            drawn_share = None
            if self.drawn_mask is not None and self.cluster_map is not None:
                target = self.cluster_map != -1
                total = int(target.sum())
                if total:
                    drawn_share = round(float(self.drawn_mask[target].sum()) / total, 4)
            counters = {k: (int(v) if float(v).is_integer() else round(float(v), 1))
                        for k, v in (self._tm_counters or {}).items()}
            session.close(
                stop_flag=bool(self.stop_flag),
                colors_processed=int(getattr(self, "current_color_index", 0) or 0),
                drawn_share=drawn_share,
                counters=counters,
                perf=self._perf.snapshot() if self._perf is not None else {},
            )
        except Exception:
            log.debug("telemetry session_end failed", exc_info=True)
        finally:
            self._telemetry = None

    def drawing_progress_snapshot(self, *, run_id=None):
        remaining, total = self.get_remaining_pixel_count()
        palette = self.color_palette or []
        colors_done = max(0, min(int(self.current_color_index), len(palette)))
        current_color = palette[min(colors_done, len(palette) - 1)][0] if palette else None
        regions_done = int((getattr(self, "_tm_counters", None) or {}).get("regions", 0))
        regions_total = max(regions_done, int(getattr(self, "_eta_total_regions", 0) or 0))
        smart = bool(self.smart_eta_enabled)
        remaining_work, total_work = (0.0, 0.0) if smart else self.get_remaining_work()
        return DrawingProgress(
            self.drawing_run_id if run_id is None else run_id, time.monotonic(),
            max(0, int(total) - int(remaining)), int(total),
            float(self.get_elapsed_drawing_time()), len(palette), colors_done,
            current_color, regions_done, regions_total, smart, remaining_work, total_work,
        )

    def _emit_drawing_event(self, phase, *, run_id=None, error="", progress=None):
        if progress is None and not phase.terminal:
            progress = self.drawing_progress_snapshot(run_id=run_id)
        event = DrawingEvent(self.drawing_run_id if run_id is None else run_id, phase, error,
                             progress)
        if self.drawing_event_callback is not None:
            self.drawing_event_callback(event)

    def draw_colors_thread(self, run_id=None):
        """Publish one terminal event after drawing and repair have finished."""
        run_id = self.drawing_run_id if run_id is None else run_id
        error = ""
        progress = None
        self._drawing_failure_message = None
        try:
            with self._owned_automation_input("рисование"):
                phase = self._draw_colors()
                if self.stop_flag or self._automation_cancelled():
                    phase = DrawingPhase.STOPPED
                progress = self.drawing_progress_snapshot(run_id=run_id)
        except InputCancelled:
            phase = DrawingPhase.STOPPED
        except Exception as exc:
            log.exception("Drawing execution failed")
            phase, error = DrawingPhase.FAILED, self._drawing_failure_message or str(exc)
            self.stop_flag = True
        finally:
            self.drawing_enabled = False
            self.drawing_start_time = None
            self._telemetry_end_session()
        self._emit_drawing_event(phase, run_id=run_id, error=error, progress=progress)

    def _draw_colors(self):
        self._log("Drawing thread started.")
        self._telemetry_begin_session()
        self._refresh_screen_metrics()  # once per draw session (not per move)
        try:
            self._eta_total_components = self._count_target_components(undrawn_only=False)
        except Exception:
            self._eta_total_components = 0
        try:
            # Smart-ETA denominator (one-time, NOT per GUI tick): per-color region
            # count in the same units as the draw loop's `regions` counter.
            self._eta_total_regions = self._count_regions_per_color_total()
        except Exception:
            self._eta_total_regions = 0
        self.last_drawing_duration = None
        self._notify_post_draw_repair(
            {
                "pass_index": 0,
                "total_passes": max(1, int(getattr(self, "post_draw_repair_passes", 1) or 1)),
                "detected_cells": 0,
                "repaired_cells": 0,
                "remaining_cells": 0,
                "unverifiable_cells": 0,
                "regions": [],
                "overlay": None,
                "draw_region": getattr(self, "draw_region", None),
                "message": "",
            }
        )
        baseline_rgb = None
        self._canvas_matched_mask = None
        repair_on = bool(getattr(self, "post_draw_repair_enabled", False))
        skip_on = bool(getattr(self, "skip_matching_canvas", False))
        if (
            (repair_on or skip_on)
            and self.draw_region is not None
            and self.cluster_map is not None
            and isinstance(getattr(self, "quantized_preview_image", None), Image.Image)
        ):
            self._mouse_up_with_settle()
            self._mouse_up_with_settle("right")
            if self._sleep_with_abort(self._POST_DRAW_REPAIR_CAPTURE_SETTLE, step=0.02):
                baseline_rgb = self._capture_draw_region_rgb()
            if skip_on and baseline_rgb is not None:
                self._mark_canvas_matching_cells(baseline_rgb)
            if not repair_on:
                baseline_rgb = None
        with self._perf.span("draw") if self._perf is not None else contextlib.nullcontext():
            if self.draw_with_layers_enabled and self.target_app_layer_coords:
                self._execute_drawing_with_target_layers()
            else:
                phases = None
                try:
                    phases = self._semantic_split_phases()
                except Exception:
                    log.debug("semantic split unavailable", exc_info=True)
                brush_passes = self._dynamic_brush_passes()
                if phases:
                    # Two-pass draw (opt-in): a mixed color is painted twice —
                    # its backdrop regions in the backdrop phase, its object
                    # regions in the object phase (drawn_mask keeps it exact).
                    self._log(f"Semantic region split: two-pass draw, phase order {phases}.")
                if brush_passes != [None]:
                    self._log("Dynamic brush: coarse, inset-detail and thin passes for all colors.")
                if phases or brush_passes != [None]:
                    try:
                        for phase in (phases or [None]):
                            for brush_pass in brush_passes:
                                if self.stop_flag or self._automation_cancelled():
                                    break
                                self._semantic_active_phase = int(phase) if phase is not None else None
                                self._brush_pass = brush_pass
                                self._color_draw_order = self._brush_pass_color_order(brush_pass)
                                self.current_color_index = 0
                                if (brush_pass == "coarse" and phase is None
                                        and not bool(getattr(self, "manual_palette_mix_enabled", False))):
                                    x0, y0, _w, _h = self.draw_region
                                    self._draw_coarse_size_major(x0, y0)
                                    continue
                                self._execute_drawing_sequentially()
                    finally:
                        self._semantic_active_phase = None
                        self._brush_pass = None
                        self._color_draw_order = None
                else:
                    self._execute_drawing_sequentially()

        if self.stop_flag:
            self._log("Drawing thread stopped.")
            return DrawingPhase.STOPPED
        elif self.drawn_mask is not None and self.cluster_map is not None and \
             np.all(self.drawn_mask[self.cluster_map != -1]):
            self._log("Drawing thread completed (all pixels drawn).")
            if not self.stop_flag:
                with self._perf.span("repair") if self._perf is not None else contextlib.nullcontext():
                    self._run_post_draw_repair(baseline_rgb)
            self._record_drawing_duration()
            return DrawingPhase.COMPLETED
        elif self.current_color_index >= len(self.color_palette):
            self._log("Drawing thread completed (all palette colors processed).")
            if not self.stop_flag:
                with self._perf.span("repair") if self._perf is not None else contextlib.nullcontext():
                    self._run_post_draw_repair(baseline_rgb)
            self._record_drawing_duration()
            return DrawingPhase.COMPLETED
        else:
            raise RuntimeError("Drawing ended before all palette colors were processed")

    def get_remaining_pixel_count(self):
        if self.drawn_mask is None or self.cluster_map is None:
            return 0, 0

        target_pixels_mask = np.zeros_like(self.cluster_map, dtype=bool)

        non_background_pixels = (self.cluster_map != -1)

        if self.mode == 'color':
            target_pixels_mask = non_background_pixels
        elif self.mode == 'bw':
            if self.bw_draw_mode == 'both':
                target_pixels_mask = non_background_pixels
            elif self.bw_draw_mode == 'black_only':
                target_pixels_mask = (self.cluster_map == 1) & non_background_pixels
            elif self.bw_draw_mode == 'white_only':
                target_pixels_mask = (self.cluster_map == 0) & non_background_pixels

        total_target_pixels = np.count_nonzero(target_pixels_mask)

        drawn_target_pixels = np.count_nonzero(self.drawn_mask & target_pixels_mask)

        remaining_target_pixels = total_target_pixels - drawn_target_pixels
        return remaining_target_pixels, total_target_pixels

    def _component_work_weight(self) -> float:
        """How many 'pixels worth' of time one connected component costs, derived
        from the current delays: each region pays a fixed pen down+up overhead
        (settle x2 + button x2 + release) regardless of size, while each pixel
        costs ~draw_delay. This is the ratio overhead/per-pixel, so a 1px region
        weighs ~ (1 + this) -> scattered tiny regions inflate the ETA correctly."""
        overhead = (2.0 * float(getattr(self, "pen_settle_delay", 0.01))
                    + 2.0 * float(getattr(self, "pen_button_delay", 0.03))
                    + float(getattr(self, "mouse_release_settle", 0.003)))
        per_pixel = max(1e-4, float(getattr(self, "draw_delay", 0.0055)))
        return max(0.0, overhead / per_pixel)

    def _count_target_components(self, *, undrawn_only: bool = False) -> int:
        """Number of 4-connected components in the target (optionally only the
        not-yet-drawn part). Used to weight the ETA by per-region overhead."""
        if self.cluster_map is None:
            return 0
        target = (self.cluster_map != -1)
        if undrawn_only and self.drawn_mask is not None and self.drawn_mask.shape == target.shape:
            target = target & (~self.drawn_mask)
        if not np.any(target):
            return 0
        try:
            n, _ = cv2.connectedComponents(target.astype(np.uint8), connectivity=4)
            return max(0, int(n) - 1)
        except Exception:
            return 0

    def _count_regions_per_color_total(self) -> int:
        """Total number of per-color 4-connected regions in the target — the same
        unit the draw loop's `regions` counter increments, so the smart ETA can
        regress elapsed time on (pixels_done, regions_done) consistently."""
        if self.cluster_map is None:
            return 0
        total = 0
        try:
            ids = np.unique(self.cluster_map)
            for cid in ids:
                if int(cid) == -1:
                    continue
                mask = (self.cluster_map == cid).astype(np.uint8)
                n, _ = cv2.connectedComponents(mask, connectivity=4)
                total += max(0, int(n) - 1)
        except Exception:
            return 0
        return total

    def get_eta_inputs(self):
        """O(grid) pixel counts + O(1) region counters for the smart ETA:
        (done_px, total_px, regions_done, regions_total). regions_done comes from
        the telemetry counter incremented at each region start (no per-tick
        connectedComponents — that used to cost tens of ms on every GUI tick)."""
        rem_px, total_px = self.get_remaining_pixel_count()
        done_px = max(0, int(total_px) - int(rem_px))
        try:
            regions_done = int((self._tm_counters or {}).get("regions", 0))
        except Exception:
            regions_done = 0
        regions_total = int(getattr(self, "_eta_total_regions", 0) or 0)
        return done_px, int(total_px), regions_done, max(regions_total, regions_done)

    def _record_drawing_duration(self) -> float | None:
        """Snapshot the session's wall time (pauses excluded) BEFORE the start
        timestamp is cleared, log it, and keep it for the completion notice."""
        try:
            elapsed = float(self.get_elapsed_drawing_time())
        except Exception:
            return None
        if elapsed <= 0:
            return None
        self.last_drawing_duration = elapsed
        minutes, seconds = divmod(int(round(elapsed)), 60)
        self._log(f"Drawing completed in {minutes}:{seconds:02d} ({elapsed:.1f}s).")
        return elapsed

    def get_remaining_work(self):
        """ETA-oriented sibling of get_remaining_pixel_count: returns
        (remaining_work, total_work) where work = pixels + weight * components.
        Counting each remaining region's fixed pen overhead makes the ETA realistic
        when many tiny scattered regions remain (otherwise time-per-pixel looks
        constant and the ETA badly underestimates them)."""
        rem_px, total_px = self.get_remaining_pixel_count()
        weight = self._component_work_weight()
        rem_comp = self._count_target_components(undrawn_only=True)
        total_comp = int(getattr(self, "_eta_total_components", 0)) or rem_comp
        rem_work = float(rem_px) + weight * float(rem_comp)
        total_work = float(total_px) + weight * float(total_comp)
        return rem_work, total_work

    def get_elapsed_drawing_time(self):
        if self.drawing_start_time is None:
            return 0
        elapsed = time.time() - self.drawing_start_time
        elapsed -= self.paused_time_accumulated
        if not self.drawing_enabled and self.pause_start_time is not None:
            elapsed -= time.time() - self.pause_start_time
        return max(elapsed, 0)

    def begin_drawing_run(self, run_id):
        thread = self.drawing_thread
        if thread is not None and thread.is_alive():
            raise RuntimeError("Previous drawing thread is still running")
        self.drawing_run_id = run_id
        self._drawing_cancel.clear()

    def request_drawing_stop(self):
        """Latch cancellation immediately; never wait or issue system input here."""
        self._drawing_cancel.set()
        self.stop_flag = True
        self.drawing_enabled = False
        self._reset_progress_on_next_start = True

        session = getattr(self, "_capture_session", None)
        if session is not None:
            session.request_cancel()

    def finish_drawing_stop(self):
        """Return to editable idle only after the input-producing thread exited."""
        if self.drawing_thread is not None and self.drawing_thread.is_alive():
            raise RuntimeError("Drawing has not stopped yet")
        self.drawing_thread = None
        self._drawing_cancel.clear()
        self.stop_flag = False

    def stop_script(self):
        self.request_drawing_stop()
        self.stop_flag = True; self.drawing_enabled = False; self.drawing_start_time = None
        self.pause_start_time = None; self.paused_time_accumulated = 0
        errors = []
        try:
            self._cancel_active_captures()
            self._end_capture_session(wait=True)
        except Exception as exc:
            errors.append(str(exc))
        try:
            self._release_automation_input()
        except Exception as exc:
            errors.append(str(exc))
        if errors:
            raise RuntimeError("; ".join(errors))
        self._log("Stop signal received.")

    def reset(self):
        self._log("Resetting OlegPainter..."); self.stop_flag=True; self.drawing_enabled=False
        self.drawing_start_time = None
        self.pause_start_time = None; self.paused_time_accumulated = 0
        self._cancel_active_captures()

        if hasattr(self, 'mouse_listener_hex') and self.mouse_listener_hex and self.mouse_listener_hex.is_alive():
            try: self.mouse_listener_hex.stop()
            except Exception as e: self._log(f"Error stopping hex listener on reset: {e}", True)
        self.mouse_listener_hex = None
        if hasattr(self, 'mouse_listener_app_layer') and self.mouse_listener_app_layer and self.mouse_listener_app_layer.is_alive():
            try: self.mouse_listener_app_layer.stop()
            except Exception as e: self._log(f"Error stopping app layer listener on reset: {e}", True)
        self.mouse_listener_app_layer = None

        if self.drawing_thread and self.drawing_thread.is_alive():
            self._log("Waiting for drawing thread to exit..."); self.drawing_thread.join(1.0)
            if self.drawing_thread.is_alive(): self._log("Drawing thread did not exit cleanly!", True)
        self.drawing_thread=None
        self.color_palette=[]; self.current_color_index=0
        self.drawn_mask=None; self.cluster_map=None; self.image_rgb=None
        self.quantized_preview_image = None; self.small_components_preview_mask = None; self.stop_flag=False

        self.layer_sort_strategy = "luminance"
        self.tone_sequence = "light_to_dark"
        self.area_sequence = "large_to_small"
        self._last_pen_cell = None
        if self.stencil_enabled:
            self.stencil_enabled = False
            if self.hide_stencil_callback: self.hide_stencil_callback(blocking=True)

        self.draw_with_layers_enabled = False
        self.target_app_layer_coords = []
        self._notify_layers_changed()
        self.is_capturing_app_layer_coords = False

        self.manual_palette_coords = []
        self._invalidate_manual_palette_cache()
        self._notify_manual_palette_changed()
        self.is_capturing_manual_palette = False
        for slot in ("pre", "post"):
            bucket = self._actions_bucket(slot)
            bucket["events"] = []
            bucket["enabled"] = False
        self._capture_events_buffer = []
        self._capture_last_event_time = None
        self._capture_ignore_until = 0.0
        self.is_capturing_extra_actions = False
        self.active_extra_actions_slot = None
        self.mouse_listener_extra_actions = None
        self.keyboard_hook_extra_actions = None
        self._notify_extra_actions_changed()

        self.image_original_rgba = None
        self.has_transparency = False
        self.background_mask = None

        self.circle_params_calib = None
        self.slider_params_calib = None
        self.screen_palette_calib = None
        self.wheel_square_calib = None
        self._wheel_last_square = None

        self.source_pil_image = None

        self.bw_draw_mode = "both"
        self.remove_background = False
        self.bg_tolerance = 25
        self.alpha_threshold = 10
        self.background_removal_enabled = False
        self.background_removal_mode = "corner"
        self.background_color_tolerance = 25
        self.background_alpha_threshold = 10
        self.background_reference_rgb = None

        # UI window rect (x1,y1,x2,y2) to ignore clicks inside app
        self.app_window_rect = None
        self.ignore_clicks_until = 0.0

        if self.status_callback: self.status_callback("reset_progress")
        if self.preview_update_callback: self.preview_update_callback(None)
        self._log("OlegPainter reset complete.")

    def cleanup_before_exit(self):
        self._log("Cleaning up OlegPainter..."); self.stop_flag=True; self.drawing_enabled=False; self.drawing_start_time=None
        self.pause_start_time = None; self.paused_time_accumulated = 0
        self._cancel_active_captures()
        if hasattr(self, 'mouse_listener_hex') and self.mouse_listener_hex and self.mouse_listener_hex.is_alive():
            try: self.mouse_listener_hex.stop()
            except: pass
        if hasattr(self, 'mouse_listener_app_layer') and self.mouse_listener_app_layer and self.mouse_listener_app_layer.is_alive():
            try: self.mouse_listener_app_layer.stop()
            except: pass
        if self.drawing_thread and self.drawing_thread.is_alive(): self.drawing_thread.join(0.5)
        if self.stencil_enabled and self.hide_stencil_callback: self.hide_stencil_callback(blocking=True)
        try: keyboard.unhook_all(); self._log("Hotkeys unhooked.")
        except Exception as e: self._log(f"Hotkey removal error: {e}", True)
        self._log("OlegPainter cleanup complete.")

    def toggle_stencil(self):
        if self._quiet():
            return False

        self.stencil_enabled = not self.stencil_enabled
        if self.stencil_enabled:
            if self.draw_region and self.quantized_preview_image:
                self.status_callback(tr("status_stencil_enabled"))
            else:
                self.stencil_enabled = False
                self.status_callback(tr("status_stencil_error_no_data"))
        else:
            self.status_callback(tr("status_stencil_disabled"))
        return bool(self.stencil_enabled)

    def _update_active_stencil(self, image_has_changed=False):
        # Legacy compatibility shim: the service owns overlay refresh and marshals it to the Qt thread.
        if self._quiet():
            return False

        if not self.stencil_enabled:
            return False

        if image_has_changed:
            self._log("Данные изображения изменились, принудительный перезапуск трафарета.")

        if self.draw_region and self.quantized_preview_image:
            return True
        self.status_callback("show_stencil_error_popup:" + tr("status_stencil_error_no_data"))
        return False


    def handle_f8_for_layer_coords(self):
        if self.drawing_enabled:
            self._log(tr("status_error_cannot_define_app_layers_drawing"), True)
            self.status_callback(tr("status_error_cannot_define_app_layers_drawing"))
            return
        if self.is_capturing_app_layer_coords:
            self._finish_app_layer_coord_capture()
        else:
            self.start_app_layer_coord_capture()

    @capture_start
    def start_app_layer_coord_capture(self):
        from .coordinate_capture import start_layers
        start_layers(self)


    def finish_app_layer_coord_capture(self):
        """Публичный стоп. Клик по кнопке остановки не считается координатой."""
        # игнорируем ближайшие клики (клик по кнопке «Задать слои»)
        try:
            import time as _time
            self.ignore_clicks_until = _time.time() + 0.25
        except Exception:
            log.debug('ignored exception in import time as _time', exc_info=True)
        self._finish_app_layer_coord_capture()

    def _finish_app_layer_coord_capture(self):
        from .coordinate_capture import finish_layers
        finish_layers(self)


    def _automation_cancelled(self) -> bool:
        """Return True if drawing automation should abort immediately."""
        return bool(self.stop_flag or self._drawing_cancel.is_set() or self._quiet())

    def _mouse_up_with_settle(self, button: str = "left", *, warn_context: str | None = None) -> bool:
        """Release mouse button and optionally wait a short moment so apps catch up.

        Both pen_button_delay and mouse_release_settle are cooperative waits;
        the common transport sends the release without a hidden library delay."""
        released = True
        exc: Exception | None = None
        if button == "left" and getattr(self, "pen_press_nudge", False) and self._pen_pos is not None:
            try:
                self._input.move(*self._pen_pos)  # repeat the end: the last segment is applied late
                time.sleep(self._NUDGE_STEP_WAIT)
                back = self._release_step_point()
                if back is not None:
                    self._input.move(*back)
                    time.sleep(self._RELEASE_STEP_WAIT)
            except Exception:
                log.debug("pointer repeat before release failed", exc_info=True)
        self._pen_prev = None
        split = self._split_state if button == "left" else None
        if split is not None:
            self._split_state = None
            if split == "lifted":
                try:
                    self._input.button_down(button)  # the stroke ended on a skipped step: dot its last cell
                except Exception:
                    log.debug("straight-stroke end dot failed", exc_info=True)
        try:
            self._input.button_up(button)
        except Exception as err:
            released = False
            exc = err
        if released and split is not None:
            gap = self._split_gap()
            self._sleep_with_abort(gap, step=min(gap, 0.01), check_target=False)
        btn_delay = float(getattr(self, "pen_button_delay", 0.03))
        if released and btn_delay > 0:
            self._sleep_with_abort(btn_delay, step=min(btn_delay, 0.01), check_target=False)
        delay = getattr(self, "mouse_release_settle", 0.0) or 0.0
        if delay > 0:
            self._sleep_with_abort(delay, step=min(delay, 0.01), check_target=False)
        if not released and warn_context:
            try:
                self._log(f"{warn_context}: {exc}", True)
            except Exception:
                log.debug("ignored exception in self._log(f'{warn_context}: {exc}', True)", exc_info=True)
        return released


    def handle_f9_for_manual_palette(self):
        if self.drawing_enabled:
            self._log(tr("status_error_cannot_define_manual_palette_drawing"), True)
            self.status_callback(tr("status_error_cannot_define_manual_palette_drawing"))
            return
        if self.is_capturing_manual_palette:
            self.finish_manual_palette_capture()
        else:
            self.start_manual_palette_capture()

    @capture_start
    def start_manual_palette_capture(self):
        from .palette_capture import start_palette_capture
        start_palette_capture(self)

    def finish_manual_palette_capture(self):
        work = getattr(self, "_palette_capture_work", None)
        if work is not None and not work.session.closed:
            self.status_callback("Завершаю захват: ожидаю выбранные образцы…")
            work.finish()
        else:
            self._finish_manual_palette_capture()

    def _finish_manual_palette_capture(self):
        self._end_capture_session()
        listener = getattr(self, 'mouse_listener_manual_palette', None)
        if listener and getattr(listener, 'is_alive', lambda: False)():
            try:
                listener.stop()
            except Exception:
                log.debug('ignored exception in listener.stop()', exc_info=True)
        self.mouse_listener_manual_palette = None
        self.is_capturing_manual_palette = False
        self._manual_palette_last_click_at = 0.0
        self._manual_palette_last_click_pos = None
        self._invalidate_manual_palette_cache()
        self._notify_manual_palette_changed()
        try:
            count = len(self.manual_palette_coords or [])
        except Exception:
            count = 0
        try:
            self.status_callback(tr("status_manual_palette_capture_end", count=count))
        except Exception:
            self.status_callback(f"Manual palette capture finished. {count} color(s).")
        self._log(f"Manual palette capture finished. Collected: {count}.")

    def handle_f10_for_extra_actions(self):
        self.toggle_extra_actions_capture("pre")

    def toggle_extra_actions_capture(self, slot="pre"):
        slot_key = self._normalize_actions_slot(slot)
        if self.is_capturing_extra_actions and self.active_extra_actions_slot == slot_key:
            self.finish_extra_actions_capture()
        else:
            self.start_extra_actions_capture(slot_key)

    @capture_start
    def start_extra_actions_capture(self, slot="pre", ignore_key=None):
        if self.drawing_enabled:
            self._log(tr("status_error_cannot_define_extra_actions_drawing"), True)
            if self.status_callback:
                try:
                    self.status_callback(tr("status_error_cannot_define_extra_actions_drawing"))
                except Exception:
                    log.debug("ignored exception in self.status_callback(tr('status_error_cannot_define_extra_actions_drawing'))", exc_info=True)
            return
        slot_key = self._normalize_actions_slot(slot)

        if mouse is None:
            self._log("Extra actions capture unavailable: pynput.mouse missing.", True)
            if self.status_callback:
                try:
                    self.status_callback(tr("status_extra_actions_capture_mouse_missing"))
                except Exception:
                    self.status_callback("Extra actions capture unavailable.")
            self.is_capturing_extra_actions = False
            return

        self._begin_capture_session("запись дополнительных действий")
        self.is_capturing_extra_actions = True
        self.active_extra_actions_slot = slot_key
        self._capture_events_buffer = []
        self._capture_last_event_time = None
        try:
            now = time.time()
            self._capture_ignore_until = now + 0.25
        except Exception:
            self._capture_ignore_until = 0.0
        label = self._actions_slot_label(slot_key)
        self._notify_extra_actions_changed()
        self._log(f"Starting extra actions capture ({slot_key}).")
        if self.status_callback:
            try:
                self.status_callback(tr("status_extra_actions_capture_start_slot", slot=label))
            except Exception:
                self.status_callback(f"Recording extra actions {label}.")

        try:
            if self.mouse_listener_extra_actions and self.mouse_listener_extra_actions.is_alive():
                self.mouse_listener_extra_actions.stop()
        except Exception:
            log.debug('ignored exception in if self.mouse_listener_extra_actions and self.mouse_listener_extra_actions.is...', exc_info=True)

        try:
            self.mouse_listener_extra_actions = self._capture_listener(mouse.Listener, on_click=self._on_mouse_capture_event)
            self.mouse_listener_extra_actions.start()
        except Exception as exc:
            self._log(f"Extra actions mouse error: {exc}", True)
            if self.status_callback:
                try:
                    self.status_callback(tr("status_extra_actions_capture_mouse_error", error=str(exc)))
                except Exception:
                    self.status_callback(f"Extra actions mouse error: {exc}")
            self.is_capturing_extra_actions = False
            self.active_extra_actions_slot = None
            self._capture_events_buffer = []
            self._notify_extra_actions_changed()
            return

        self.keyboard_hook_extra_actions = None

    def finish_extra_actions_capture(self):
        self._finish_extra_actions_capture()


    def _finish_extra_actions_capture(self, auto=False):
        session = getattr(self, "_capture_session", None)
        if session is not None:
            session.publish(lambda: self._commit_extra_actions_capture(auto=auto))

    def _commit_extra_actions_capture(self, auto=False):
        if not self.is_capturing_extra_actions:
            return
        self._end_capture_session()
        slot_key = self.active_extra_actions_slot or "pre"
        bucket = self._actions_bucket(slot_key)
        events = list(self._capture_events_buffer)

        self._stop_actions_listeners()
        self.is_capturing_extra_actions = False
        self.active_extra_actions_slot = None
        try:
            self._capture_ignore_until = time.time() + 0.25
        except Exception:
            self._capture_ignore_until = 0.0
        self._capture_last_event_time = None
        self._capture_events_buffer = []

        # An empty recording keeps the previous one: the default record keys [ and ]
        # are the Russian letters х and ъ, so typing in any program started and
        # finished a recording and wiped Gartic's colour-box opening (2026-09-30).
        if events:
            bucket["events"] = events
            bucket["enabled"] = True

        self._notify_extra_actions_changed()

        label = self._actions_slot_label(slot_key)
        count = len(events)
        if self.status_callback:
            try:
                key = "status_extra_actions_capture_end_slot" if count else "status_extra_actions_capture_empty_slot"
                self.status_callback(tr(key, slot=label, count=count))
            except Exception:
                try:
                    self.status_callback(f"Extra actions capture finished for {label}: {count} event(s).")
                except Exception:
                    log.debug("ignored exception in self.status_callback(f'Extra actions capture finished for {label}: {count} ev...", exc_info=True)
        self._log(f"Extra actions capture finished ({slot_key}). Collected: {count}.")


    # ---- Manual image placement helpers -------------------------------------------------

    def get_stretch_to_area(self) -> bool:
        return bool(getattr(self, "stretch_to_area", True))

    def set_stretch_to_area(self, enabled: bool) -> None:
        self.stretch_to_area = bool(enabled)

    def get_manual_image_rect(self):
        rect = getattr(self, "manual_image_rect", None)
        if rect is None:
            return None
        x, y, w, h = rect
        return (float(x), float(y), float(w), float(h))

    def set_manual_image_rect(self, rect) -> None:
        if rect is None:
            self.manual_image_rect = None
            return
        try:
            x, y, w, h = rect
        except Exception:
            raise ValueError("manual image rect must be a 4-item iterable")
        limit = self._manual_fit_size_limit()
        min_size = 1.0
        w = max(min_size, min(float(w), limit))
        h = max(min_size, min(float(h), limit))
        self.manual_image_rect = (float(x), float(y), w, h)

    def ensure_manual_image_rect(self):
        if self.manual_image_rect is None and self.draw_region:
            _, _, w, h = self.draw_region
            self.manual_image_rect = (0.0, 0.0, float(w), float(h))
        return self.manual_image_rect

    def reset_manual_image_rect(self) -> None:
        if self.draw_region:
            _, _, w, h = self.draw_region
            self.manual_image_rect = (0.0, 0.0, float(w), float(h))
        else:
            self.manual_image_rect = None

    def _manual_fit_size_limit(self) -> float:
        """Upper bound for manual scaling to avoid runaway memory usage."""
        limit = 8192.0
        try:
            candidates = []
            if self.draw_region:
                candidates.extend([abs(self.draw_region[2]), abs(self.draw_region[3])])
            if self.source_pil_image is not None:
                w, h = self.source_pil_image.size
                candidates.extend([w, h])
            if candidates:
                # Allow up to 4x the largest relevant dimension but keep a sane cap.
                limit = min(8192.0, max(candidates) * 4.0)
        except Exception:
            log.debug('ignored exception in candidates = []', exc_info=True)
        return float(max(1.0, limit))

    def _render_manual_fit(self, area_w: int, area_h: int, rect, resample_mode) -> Image.Image:
        """Render the source image onto an area-sized canvas using manual placement."""
        if self.image_original_rgba is None or rect is None:
            return Image.new("RGBA", (area_w, area_h), (0, 0, 0, 0))

        try:
            x, y, w, h = rect
        except Exception:
            x = y = 0.0
            w, h = float(area_w), float(area_h)

        w_int = max(1, int(round(w)))
        h_int = max(1, int(round(h)))
        paste_x = int(round(x))
        paste_y = int(round(y))

        scaled = self.image_original_rgba.resize((w_int, h_int), resample_mode)
        canvas = Image.new("RGBA", (area_w, area_h), (0, 0, 0, 0))

        # Compute overlap between scaled image and area canvas.
        dest_left = max(0, paste_x)
        dest_top = max(0, paste_y)
        dest_right = min(area_w, paste_x + w_int)
        dest_bottom = min(area_h, paste_y + h_int)

        if dest_right <= dest_left or dest_bottom <= dest_top:
            # Entire image lies outside area; nothing to paste.
            return canvas

        src_left = dest_left - paste_x
        src_top = dest_top - paste_y
        src_right = src_left + (dest_right - dest_left)
        src_bottom = src_top + (dest_bottom - dest_top)
        cropped = scaled.crop((src_left, src_top, src_right, src_bottom))
        canvas.paste(cropped, (dest_left, dest_top), cropped)
        return canvas
