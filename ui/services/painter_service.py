from __future__ import annotations

from PySide6.QtCore import Qt, QRect, QObject, Signal, QThread, Slot, QTimer, QCoreApplication
from PySide6.QtGui import QGuiApplication, QImage, QPixmap, QKeySequence, QClipboard
from PySide6.QtWidgets import QWidget
from PIL import Image, ImageQt
from pathlib import Path
from copy import deepcopy
from io import BytesIO
import time, os, traceback, json, math, inspect
import logging
log = logging.getLogger("olegpainter.painter_service")
from typing import Callable, Dict

from app_paths import get_app_paths
try:
    from ui.helpers.global_hotkey import GlobalHotkeyManager, HotkeyRegistrationError
except Exception:
    GlobalHotkeyManager = None  # type: ignore
    HotkeyRegistrationError = RuntimeError  # type: ignore

from engine.olegpainter.core import OlegPainter
from engine.olegpainter.drawing_events import DrawingPhase
from ui.services.config_service import ConfigServiceMixin

from ui.services.ai_service import AiServiceMixin
from ui.services.ai_tools import AiToolsMixin

from engine.olegpainter.viewport_state import (
    VIEWPORT_UNSET,
    ViewportState,
    normalize_area_rect,
    normalize_desktop_rect,
)
from engine.ai import (
    AiFeatureDisabled,
    AiModelDescriptor,
    AiModelRegistry,
    DisabledAiBackend,
    ModelTask,
    ai_deferred_message,
    describe_execution_provider,
)
from ui.helpers.config_migration import migrate_json_file_in_place, sanitize_painter_config


def solve_two_term_eta(samples, px_remaining, regions_remaining):
    """Least-squares (through the origin) fit of elapsed ≈ a*pixels + b*regions
    over the session's progress samples, then ETA = a*px_rem + b*regions_rem.

    This replaces the config-derived region weight: with tiny draw_delay values
    the OS sleep granularity dominates the real per-pixel cost, so weights
    computed from the config wildly misjudge how expensive the remaining tiny
    regions are (live report: 'ETA пишет 5 сек, хотя там ещё дохуя рисовать').
    Measured rates self-correct for any delay configuration.

    samples: iterable of (elapsed_s, pixels_done, regions_done), cumulative.
    Returns the ETA in seconds, or None when the fit is not trustworthy yet
    (too few samples / degenerate regressors) — caller falls back to legacy.
    """
    pts = [(float(t), float(px), float(rg)) for t, px, rg in samples
           if t > 0 and (px > 0 or rg > 0)]
    if len(pts) < 4:
        return None
    sxx = sxc = scc = sxt = sct = 0.0
    for t, px, rg in pts:
        sxx += px * px
        sxc += px * rg
        scc += rg * rg
        sxt += px * t
        sct += rg * t
    det = sxx * scc - sxc * sxc
    a = b = None
    if det > 1e-9 and sxx > 0 and scc > 0:
        a = (sxt * scc - sct * sxc) / det
        b = (sct * sxx - sxt * sxc) / det
        # Negative rates mean the regressors are nearly collinear on this
        # picture (e.g. region count moves in lockstep with pixels) — the
        # split is meaningless, collapse to the single-rate model below.
        if a < 0 or b < 0:
            a = b = None
    if a is None:
        t_last, px_last, rg_last = pts[-1]
        if px_last <= 0:
            return None
        rate = t_last / px_last
        return rate * max(0.0, float(px_remaining))
    return a * max(0.0, float(px_remaining)) + b * max(0.0, float(regions_remaining))
from ui.helpers.viewport_mapper import monitor_binding_for_rect, qrect_from_tuple
from ui.widgets.top_overlay import TopOverlay
from ui.helpers.hotkey_definitions import (
    HOTKEY_DEFINITIONS,
    HotkeyDefinition,
    default_hotkey_profile,
    definition_for_code,
    definitions_by_scope,
    scopes as hotkey_scopes,
)
from ui.i18n import tr


from ui.services.image_utils import pil_to_qimage  # noqa: F401


SESSION_PROFILE_KEYS = {
    "drawing_algorithm",
    "color_picking_method",
    "prep_color_space",
    "prep_quantization_mode",
    "prep_cleanup_mode",
    "k_clusters",
    "draw_delay",
    "brush_size",
    "dynamic_brush_enabled",
    "dynamic_brush_control_mode",
    "dynamic_brush_min_value",
    "dynamic_brush_max_value",
    "dynamic_brush_default_value",
    "dynamic_brush_step_value",
    "dynamic_brush_coord",
    "dynamic_brush_profile",
    "dynamic_brush_slider_params",
    "dynamic_brush_points",
    "dynamic_brush_scratch_zone",
    "dynamic_brush_drag_enabled",
    "dynamic_brush_verify_at_draw",
    "dynamic_brush_text_auto",
    "dynamic_brush_calibration",
    "mode",
    "bw_draw_mode",
    "remove_background",
    "background_removal_enabled",
    "background_removal_mode",
    "bg_tolerance",
    "background_color_tolerance",
    "alpha_threshold",
    "background_alpha_threshold",
    "background_reference_rgb",
    "tone_sequence",
    "area_sequence",
    "layer_sort_strategy",
    "draw_with_layers_enabled",
    "manual_mix_enabled",
    "manual_mix_alpha",
    "fast_min_region_area",
    "post_draw_repair_enabled",
    "post_draw_repair_passes",
    "post_draw_repair_mode",
    "post_draw_repair_sensitivity",
    "post_draw_repair_color_mismatch_sensitivity",
    "outline_fill_tool_mode",
    "outline_fill_brush_key",
    "outline_fill_fill_key",
    "outline_fill_brush_coord",
    "outline_fill_fill_coord",
    "outline_fill_tiny_area_factor",
    "outline_fill_small_area_factor",
    "outline_fill_brush_small_cap",
    "hex_add_hash",
    # Colour calibrations and pen timings belong to the place: Gartic Phone must
    # not inherit the Paint palette, the Roblox HEX field or Spray Paint's nudge.
    "pen_button_delay",
    "pen_settle_delay",
    "pen_press_nudge",
    "area_fill_delay",
    "pen_max_step",
    "pen_split_strokes",
    "pen_stroke_gap",
    "hex_input_coord",
    "hex_field_opened_by_actions",
    "manual_palette_coords",
    "manual_mix_canvas_rgb",
    "alpha_slider_params",
    "target_app_layer_coords",
    "pre_color_actions",
    "post_color_actions",
    "pre_actions_enabled",
    "post_actions_enabled",
    "circle_params_calib",
    "slider_params_calib",
    "screen_palette_calib",
    "wheel_square_calib",
    "palette_rotation_direction_calib",
}

PREP_COLOR_SPACE_VALUES = {"legacy_rgb", "cielab", "oklab"}
PREP_QUANTIZATION_VALUES = {"kmeans", "minibatch", "imagequant"}
PREP_CLEANUP_VALUES = {"off", "vectorized", "vectorized_plus_stray_merge"}

from ui.services._sentinel import _UNSET
from ui.services.hotkey_binding import HotkeyBindingMixin
from ui.services.drawing_lifecycle import DrawingLifecycleMixin
from ui.services.viewport_state_mixin import ViewportStateMixin
from ui.services.shutdown_lifecycle import ShutdownLifecycleMixin
from ui.services.brush_learning import BrushLearningMixin
from ui.services.manual_palette_service import ManualPaletteServiceMixin
from ui.services.input_sequences import InputSequencesMixin
from ui.services.image_link_service import ImageLinkMixin
from ui.services.image_edits import ImageEditsMixin
from ui.services.preview_pipeline import PreviewPipelineMixin
from ui.services.stencil_sync import StencilSyncMixin
from ui.services.draw_worker import _Worker, _AiWorker, thread_is_running as _thread_is_running_fn


class PainterService(QObject, AiToolsMixin, AiServiceMixin, ConfigServiceMixin, StencilSyncMixin, PreviewPipelineMixin, HotkeyBindingMixin, ManualPaletteServiceMixin, DrawingLifecycleMixin, ViewportStateMixin, ShutdownLifecycleMixin, BrushLearningMixin, InputSequencesMixin, ImageLinkMixin, ImageEditsMixin):
    """Main-thread UI/engine coordinator; workers perform expensive operations.

    Use QThread.isMainThread() for dispatch checks. QObject.thread() in PySide
    can parent the main-thread wrapper to this temporary service, allowing cyclic
    GC to destroy Qt's native main thread (see test_service_lifetime.py).
    """
    previewReady = Signal(QImage)
    previewStateChanged = Signal()
    aiChanged = Signal()
    brushLearningChanged = Signal()
    brushLearningFinished = Signal(bool)
    shutdownFinished = Signal(str)
    statusChanged = Signal(str)
    areaChanged   = Signal(QRect)
    overlayVisibleChanged = Signal(bool)

    progressChanged = Signal(int)
    etaChanged      = Signal(str)
    drawingStateChanged = Signal(str)
    drawingEvent = Signal(object)
    drawingStatsChanged = Signal(object)   # structured live stats for the HUD overlay
    toggleOverlayRequested = Signal()      # global hotkey -> UI toggles the HUD overlay
    layoutEditRequested = Signal()         # global hotkey (Alt+F2) -> UI opens unified layout edit
    kalkaToggleRequested = Signal()        # stencil toggle -> UI shows/hides the Kalka overlay
    kalkaEditRequested = Signal()          # edit stencil (Alt+F2) -> UI shows + activates Kalka
    kalkaSelectAreaRequested = Signal()    # select area (F1) -> UI starts Kalka rubber-band placement
    screenCalibrationRequested = Signal(str)
    brushCalibrationRequested = Signal(object)
    captureStateChanged = Signal(object)   # layer/palette/extra-actions capture mode for the HUD
    desktopInteractionChanged = Signal(str)

    configLoaded = Signal(object)
    appLayersChanged = Signal(int)
    manualPaletteChanged = Signal(int)
    extraActionsChanged = Signal(object)
    manualMixCanvasChanged = Signal(tuple)
    outlineFillSettingsChanged = Signal(dict)
    dynamicBrushSettingsChanged = Signal(dict)
    dynamicBrushLearningRequested = Signal(dict)
    manualPlacementChanged = Signal(object)
    viewportStateChanged = Signal(object)
    sessionStateChanged = Signal(object)

    aiDownloadStarted = Signal(str)
    aiDownloadProgress = Signal(str, int)
    aiDownloadStatus = Signal(str, str)
    aiDownloadFinished = Signal(str)
    aiDownloadFailed = Signal(str, str)
    aiDownloadCancelled = Signal(str)
    aiTaskStarted = Signal(str, str)
    aiTaskProgress = Signal(str, int)
    aiTaskFinished = Signal(str)
    aiTaskFailed = Signal(str, str)
    aiTaskCancelled = Signal(str)

    hotkeysChanged = Signal(object)
    hotkeyCaptureChanged = Signal(object)
    _PREVIEW_DEBOUNCE_MS = 160
    _PREVIEW_DEBOUNCE_STRICT_MS = 260
    _PREVIEW_HEAVY_DEBOUNCE_MS = 700
    _PREVIEW_RETRY_MS = 220
    _INVALIDATE_AREA = "AREA"
    _INVALIDATE_PLACEMENT = "PLACEMENT"
    _INVALIDATE_RENDER = "RENDER"
    _INVALIDATE_PALETTE_ORDER = "PALETTE_ORDER"
    _RU_TO_EN_LAYOUT = {
        "й": "q", "ц": "w", "у": "e", "к": "r", "е": "t", "н": "y", "г": "u", "ш": "i", "щ": "o", "з": "p",
        "х": "[", "ъ": "]",
        "ф": "a", "ы": "s", "в": "d", "а": "f", "п": "g", "р": "h", "о": "j", "л": "k", "д": "l", "ж": ";", "э": "'",
        "я": "z", "ч": "x", "с": "c", "м": "v", "и": "b", "т": "n", "ь": "m", "б": ",", "ю": ".", "ё": "`",
    }

    _GLOBAL_HOTKEY_ROUTES = {
        "select_area": ("select_area",),
        "toggle_stencil": ("toggle_stencil",),
        # Alt+F2 → the stencil editor (Kalka) directly; legacy HUD layout-edit is the fallback.
        "edit_stencil": ("edit_stencil", "request_layout_edit"),
        "edit_overlay": ("request_layout_edit",),   # Ctrl+Shift+E → HUD layout editor
        "start_pause": ("start_pause",),
        "stop": ("stop",),
        "capture_hex_palette": ("start_hex_coord_capture",),
        "define_app_layers": ("toggle_app_layer_coord_capture", "start_app_layer_coord_capture"),
        "define_manual_palette": ("toggle_manual_palette_capture", "start_manual_palette_capture"),
        "record_pre_color_actions": ("toggle_pre_color_actions_capture", "start_pre_color_actions_capture"),
        "record_post_color_actions": ("toggle_post_color_actions_capture", "start_post_color_actions_capture"),
        "toggle_overlay": ("toggle_overlay",),
    }


    def __init__(self, parent=None):
        if QCoreApplication.instance() is None or not QThread.isMainThread():
            raise RuntimeError("PainterService requires the Qt application main thread")
        super().__init__(parent)
        self._is_shutting_down = False
        self.logger = logging.getLogger("PainterService")
        self.engine = OlegPainter(
            status_callback=self._on_status_from_engine,
            pixel_update_callback=self._on_progress_from_engine,
            preview_update_callback=self._on_preview_from_engine,
            drawing_event_callback=self._on_drawing_event,
        )
        self._prep_color_space = "oklab"   # perceptual colour by default (was legacy gamma sRGB)
        self._prep_quantization_mode = "kmeans"
        self._prep_cleanup_mode = "off"
        self._sync_prep_settings_to_engine()
        self._last_qimg: QImage | None = None
        self._last_source_qimg: QImage | None = None
        self._session_image_kind: str | None = None
        self._session_image_original_path: str | None = None
        self._session_image_descriptor_cache: dict | None = None
        self._session_image_png_cache: bytes | None = None
        self._session_image_revision = 0
        self._session_image_png_cache_token = None
        self._close_pending = False
        self.ai_backend = getattr(self.engine, "ai_backend", DisabledAiBackend())
        self._init_ai_tools()
        self._init_image_edits()
        self.ai_registry: AiModelRegistry | None = None
        self._ai_downloads: Dict[str, object] = {}
        self._ai_task_thread: QThread | None = None
        self._ai_task_worker: _AiWorker | None = None
        self._ai_task_id: str | None = None
        self._ai_task_on_result: Callable[[object], None] | None = None
        self._preview_thread: QThread | None = None
        self._preview_worker: _Worker | None = None
        self._preview_timer: QTimer | None = None
        self._preview_pending: str | None = None
        self._preview_pending_delay_ms: int | None = None
        self._overlay: TopOverlay | None = None
        self._skipped_overlay_timer: QTimer | None = None
        self._last_skipped_regions_payload: dict | None = None
        self._last_post_draw_repair_payload: dict | None = None
        self._stencil_edit_active = False
        self._stencil_enabled_requested = bool(getattr(self.engine, "stencil_enabled", False))
        self._stencil_overlay_visible = False
        self._stencil_dirty = False
        self._stencil_refresh_deferred = False
        self._stencil_revision_applied = 0
        self._render_revision = 0
        self._preview_requested_revision = 0
        self._preview_active_revision = 0
        self._preview_display_revision = 0
        self._pending_stencil_reason: str | None = None
        self._pending_stencil_revision = 0
        self._pending_stencil_force_hide = False
        self._pending_stencil_allow_disabled = False
        self._pending_stencil_qimage: QImage | None = None
        self._pending_stencil_area: tuple[int, int, int, int] | None = None
        self._deferred_stencil_reason: str | None = None
        self._deferred_stencil_revision = 0
        self._deferred_stencil_force_hide = False
        self._deferred_stencil_allow_disabled = False
        self._deferred_stencil_qimage: QImage | None = None
        self._deferred_stencil_area: tuple[int, int, int, int] | None = None
        self._stencil_sync_timer = QTimer(self)
        self._stencil_sync_timer.setSingleShot(True)
        self._stencil_sync_timer.timeout.connect(self._flush_stencil_sync)

        if hasattr(self.engine, "show_stencil_callback"):
            self.engine.show_stencil_callback = self._engine_show_stencil
        if hasattr(self.engine, "hide_stencil_callback"):
            self.engine.hide_stencil_callback = self._engine_hide_stencil
        if hasattr(self.engine, "layers_changed_callback"):
            self.engine.layers_changed_callback = self._on_engine_layers_changed
        self._emit_app_layers_changed()
        if hasattr(self.engine, "manual_palette_changed_callback"):
            self.engine.manual_palette_changed_callback = self._on_engine_manual_palette_changed
        self._emit_manual_palette_changed()
        if hasattr(self.engine, "manual_mix_canvas_changed_callback"):
            self.engine.manual_mix_canvas_changed_callback = self._on_engine_manual_mix_canvas_changed
        self._emit_manual_mix_canvas_changed()
        if hasattr(self.engine, "extra_actions_changed_callback"):
            self.engine.extra_actions_changed_callback = self._on_engine_extra_actions_changed
        self._emit_extra_actions_changed()
        if hasattr(self.engine, "outline_fill_settings_changed_callback"):
            self.engine.outline_fill_settings_changed_callback = self._on_engine_outline_fill_settings_changed
        self._emit_outline_fill_settings_changed()
        if hasattr(self.engine, "dynamic_brush_settings_changed_callback"):
            self.engine.dynamic_brush_settings_changed_callback = self._on_engine_dynamic_brush_settings_changed
        self._emit_dynamic_brush_settings_changed()
        if hasattr(self.engine, "skipped_regions_callback"):
            self.engine.skipped_regions_callback = self._engine_on_skipped_regions
        if hasattr(self.engine, "post_draw_repair_callback"):
            self.engine.post_draw_repair_callback = self._engine_on_post_draw_repair
        self._runtime_initialized = False

        self._is_drawing = False
        self._drawing_run_id = 0
        self._drawing_terminal = False
        self.drawing_phase = DrawingPhase.IDLE
        self.desktop_interaction = ""
        self._stop_pending = False
        self._stop_requested = False
        self._stop_error = ""
        self._stop_worker_thread = None
        self._stop_worker = None
        self._stop_poll_timer = QTimer(self)
        self._stop_poll_timer.setInterval(20)
        self._stop_poll_timer.timeout.connect(self._poll_drawing_stop)
        self._hotkey_manager: GlobalHotkeyManager | None = None
        self._using_native_hotkeys = False
        self._hotkeys: Dict[str, str] = {
            code: self._normalize_sequence(seq) for code, seq in default_hotkey_profile().items()
        }
        self._registered_hotkey_ids: list[int] = []
        self._hotkey_capture_active = False
        self._hotkey_capture_epoch = 0
        self._hotkey_capture_token = 0
        self._hotkey_capture_deadline = 0.0
        self._hotkey_capture_timer = QTimer(self)
        self._hotkey_capture_timer.setSingleShot(True)
        self._hotkey_capture_timer.timeout.connect(self._expire_hotkey_capture)
        self._toggle_bounce: Dict[str, float] = {}
        self._progress_last_emit = 0.0
        self._progress_capture_last = 0.0
        self._progress_applied_at = 0.0
        self._last_drawing_progress = None
        self._progress_emit_min_interval = 0.4
        self._progress_snapshot = {"percent": None, "eta_text": None}
        self._progress_idle_text = self._format_eta(None, None, None)
        # Detect headless/offscreen Qt up front: under such platforms the native
        # global-hotkey filter (installNativeEventFilter) is both pointless and
        # crash-prone (access violation), so the manager must not even be
        # constructed. Real platforms (windows/cocoa/xcb) are unaffected.
        self._headless_qt_platform = os.environ.get("QT_QPA_PLATFORM", "").strip().lower() in {"offscreen", "minimal"}
        self._hotkey_manager = None
        if GlobalHotkeyManager is not None and not self._headless_qt_platform:
            try:
                candidate = GlobalHotkeyManager()
                if getattr(candidate, "available", False):
                    self._hotkey_manager = candidate
                else:
                    self._hotkey_manager = None
            except Exception:
                self._hotkey_manager = None
        self._is_paused  = False
        self._started_at: float | None = None

        self._worker_thread: QThread | None = None
        self._worker: _Worker | None = None
        self._shutdown_started = False
        self._shutdown_completed = False
        self._hotkeys_warned_unavailable = False

        if not self._headless_qt_platform:
            try:
                QTimer.singleShot(0, self._register_global_hotkeys)
            except Exception:
                log.debug('ignored exception in QTimer.singleShot(0, self._register_global_hotkeys)', exc_info=True)

        from application.profiles import ProfileController
        self.profiles = ProfileController(self)

    def initialize_runtime(self) -> None:
        if self._runtime_initialized:
            return
        self._runtime_initialized = True
        try:
            if hasattr(self.engine, "initialize_runtime"):
                self.engine.initialize_runtime()
        except Exception as exc:
            self.logger.error("Runtime initialization failed: %s", exc)
            try:
                self.statusChanged.emit(f"warn: runtime initialization failed: {exc}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'warn: runtime initialization failed: {exc}')", exc_info=True)

    def set_main_window_hwnd(self, hwnd) -> None:
        setter = getattr(self.engine, "set_main_window_hwnd", None)
        if callable(setter):
            try:
                setter(hwnd)
            except Exception as exc:
                self.logger.debug("set_main_window_hwnd failed: %s", exc)

    def _copy_qimage(self, image) -> QImage | None:
        if image is None:
            return None
        if isinstance(image, QImage):
            return None if image.isNull() else image.copy()
        if isinstance(image, Image.Image):
            qimg = pil_to_qimage(image)
            return None if qimg.isNull() else qimg
        return None

    def _set_session_image_source(self, kind: str | None, original_path: str | None = None) -> None:
        self._session_image_revision += 1
        self._session_image_kind = str(kind or "").strip().lower() or None
        cleaned = str(original_path or "").strip()
        self._session_image_original_path = cleaned or None
        self._session_image_descriptor_cache = None
        self._session_image_png_cache = None

    def _get_source_pil_for_session(self) -> Image.Image | None:
        source = getattr(self.engine, "source_pil_image", None)
        if isinstance(source, Image.Image):
            try:
                pil = source.copy()
                return pil.convert("RGBA") if pil.mode != "RGBA" else pil
            except Exception:
                return None
        copied = self._copy_qimage(self._last_source_qimg)
        if copied is not None:
            try:
                return self._to_pil(copied).convert("RGBA")
            except Exception:
                return None
        try:
            pil = self._get_current_pil()
        except Exception:
            return None
        return pil.convert("RGBA") if pil.mode != "RGBA" else pil

    def session_image_token(self):
        return (self._session_image_revision, id(self.engine.source_pil_image),
                self._session_image_kind, self._session_image_original_path)

    def snapshot_session_image(self):
        from application.session_image import SessionImage
        # Copy pixels on the owner thread. Encoding and all disk work happen in
        # the writer; it cannot reach this QObject or a changing engine image.
        token = self.session_image_token()
        png = self._session_image_png_cache if self._session_image_png_cache_token == token else None
        return SessionImage(token, pixels=self._get_source_pil_for_session() if png is None else None, png=png)

    def accept_session_image_png(self, token, png):
        if token == self.session_image_token():
            self._session_image_png_cache = png
            self._session_image_png_cache_token = token

    def _emit_session_state_changed(self, reason: str, *, sections=("painter",)) -> None:
        payload = {
            "reason": str(reason or "changed"),
            "sections": [str(section) for section in sections if section],
        }
        try:
            self.sessionStateChanged.emit(payload)
        except Exception:
            log.debug('ignored exception in self.sessionStateChanged.emit(payload)', exc_info=True)

    @staticmethod
    def _normalize_prep_value(value, allowed: set[str], default: str) -> str:
        cleaned = str(value or "").strip().lower()
        return cleaned if cleaned in allowed else default

    def _sync_prep_settings_to_engine(self) -> None:
        for attr, value in (
            ("prep_color_space", self._prep_color_space),
            ("prep_quantization_mode", self._prep_quantization_mode),
            ("prep_cleanup_mode", self._prep_cleanup_mode),
        ):
            try:
                setattr(self.engine, attr, value)
            except Exception:
                log.debug('ignored exception in setattr(self.engine, attr, value)', exc_info=True)

    def _set_prep_setting(self, attr: str, value, *, emit: bool = True, invalidate: bool = True) -> bool:
        if attr == "prep_color_space":
            normalized = self._normalize_prep_value(value, PREP_COLOR_SPACE_VALUES, "legacy_rgb")
        elif attr == "prep_quantization_mode":
            normalized = self._normalize_prep_value(value, PREP_QUANTIZATION_VALUES, "kmeans")
        elif attr == "prep_cleanup_mode":
            normalized = self._normalize_prep_value(value, PREP_CLEANUP_VALUES, "off")
        else:
            return False
        previous = getattr(self, f"_{attr}", None)
        if value is _UNSET:
            try:
                setattr(self.engine, attr, previous)
            except Exception:
                log.debug('ignored exception in setattr(self.engine, attr, previous)', exc_info=True)
            return False
        changed = previous != normalized
        setattr(self, f"_{attr}", normalized)
        try:
            setattr(self.engine, attr, normalized)
        except Exception:
            log.debug('ignored exception in setattr(self.engine, attr, normalized)', exc_info=True)
        if changed and invalidate:
            self._invalidate_heavy_preview(strict=True)
        if changed and emit:
            self._emit_session_state_changed(attr.replace("_", "-"), sections=("painter",))
        return changed


    def _mark_render_dirty(self, reason: str) -> int:
        self._emit_session_state_changed(reason or "render-dirty", sections=("painter",))
        self._render_revision += 1
        self._preview_requested_revision = max(self._preview_requested_revision, self._render_revision)
        self._stencil_dirty = True
        return self._render_revision













    def _should_bounce_toggle(self, key: str, cooldown: float = 0.25) -> bool:
        """Ignore duplicate toggles triggered within a short time window."""
        now = time.monotonic()
        last = self._toggle_bounce.get(key, 0.0)
        if now - last < cooldown:
            return True
        self._toggle_bounce[key] = now
        return False

    def _normalize_actions_slot(self, slot: str | None) -> str:
        value = (slot or "pre").strip().lower()
        if value in ("post", "after", "post_color", "post_color_actions"):
            return "post"
        return "pre"

    _thread_is_running = staticmethod(_thread_is_running_fn)

    def _invoke_engine_preprocess(self) -> None:
        preprocess = getattr(self.engine, "preprocess_image", None)
        if not callable(preprocess):
            return

        source = getattr(self.engine, "source_pil_image", None)
        if source is None:
            source = getattr(self.engine, "image_original_rgba", None)

        result = None
        try:
            sig = inspect.signature(preprocess)
            required_positional = [
                p
                for p in sig.parameters.values()
                if p.kind in (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
                and p.default is inspect.Parameter.empty
            ]
            if required_positional:
                result = preprocess(source)
            else:
                result = preprocess()
        except (TypeError, ValueError):
            # Signature introspection can fail for wrapped callables.
            try:
                result = preprocess()
            except TypeError:
                result = preprocess(source)

        if isinstance(result, Image.Image):
            try:
                self.engine.source_pil_image = result
            except Exception:
                log.debug('ignored exception in self.engine.source_pil_image = result', exc_info=True)

    # ---------- Mode / B&W ----------
    def set_mode(self, mode: str):
        if mode not in ("color", "bw"):
            raise ValueError("Неизвестный режим рисунка.")
        if self.engine.mode != mode:
            self.engine.mode = mode
            self._invalidate_visuals(self._INVALIDATE_RENDER)

    def set_mode_color(self):
        """Switch engine to full-color mode and rebuild preview (non-blocking)."""
        self.set_mode("color")

    def set_mode_bw(self):
        """Switch engine to black-and-white mode and rebuild preview (non-blocking)."""
        self.set_mode("bw")

    def set_bw_draw_mode(self, mode: str):
        if mode not in ("black_only", "white_only", "both"):
            raise ValueError("Неизвестный выбор чёрно-белых цветов.")
        if self.engine.bw_draw_mode != mode:
            self.engine.bw_draw_mode = mode
            self._invalidate_visuals(self._INVALIDATE_RENDER)

    def set_bw_mode(self, mode: str):
        """
        Accepts: 'black_only', 'white_only', 'both'.
        Updates engine.bw_draw_mode and rebuilds preview.
        """
        self.set_bw_draw_mode(mode)

    def set_tone_sequence(self, sequence: str):
        try:
            setter = getattr(self.engine, 'set_tone_sequence', None)
            if callable(setter):
                setter(sequence)
        except Exception as e:
            self.statusChanged.emit(f"error: set_tone_sequence failed: {e}")

    def set_area_sequence(self, sequence: str):
        try:
            setter = getattr(self.engine, 'set_area_sequence', None)
            if callable(setter):
                setter(sequence)
        except Exception as e:
            self.statusChanged.emit(f"error: set_area_sequence failed: {e}")

    def _emit_app_layers_changed(self, coords=None):
        if coords is None:
            coords = self.engine.layer_capture_points() if hasattr(self, 'engine') else None
        try:
            count = len(coords or [])
        except Exception:
            count = 0
        try:
            self.appLayersChanged.emit(int(count))
        except Exception:
            log.debug('ignored exception in self.appLayersChanged.emit(int(count))', exc_info=True)
        self._emit_capture_state()
        self._emit_session_state_changed("app-layers", sections=("painter",))

    def set_desktop_interaction(self, mode: str):
        if mode not in ("", "stencil", "hud", "pick", "capture", "measure"):
            raise ValueError("Unknown desktop interaction")
        if self.desktop_interaction != mode:
            self.desktop_interaction = mode
            self.desktopInteractionChanged.emit(mode)

    def _compute_capture_state(self) -> dict:
        """Detect which interactive capture is currently active (one at a time)."""
        engine = getattr(self, "engine", None)
        kind = None
        count = 0
        maximum = 0
        slot = None
        session = getattr(engine, "_capture_session", None)
        if engine is not None and not (session is not None and session.closed):
            if getattr(engine, "is_waiting_for_hex_click", False):
                kind = "hex"
            elif getattr(engine, "is_capturing_app_layer_coords", False):
                kind = "layers"
                count = len(engine.layer_capture_points())
                maximum = int(getattr(engine, "max_definable_app_layers", 0) or 0)
            elif getattr(engine, "is_capturing_manual_palette", False):
                kind = "palette"
                count = len(getattr(engine, "manual_palette_coords", []) or [])
                maximum = int(getattr(engine, "max_manual_palette_colors", 0) or 0)
            elif getattr(engine, "is_capturing_extra_actions", False):
                kind = "extra"
                slot = getattr(engine, "active_extra_actions_slot", None)
                count = len(engine._serialize_actions(slot, include_draft=True))
                maximum = int(engine.max_extra_action_events)
            elif getattr(engine, "is_capturing_dynamic_brush_coord", False):
                kind = "brush"
            elif getattr(engine, "is_capturing_outline_fill_coords", False):
                kind = "outline_fill"
            elif getattr(engine, "is_capturing_manual_mix_background", False):
                kind = "background"
        return {"active": kind is not None, "kind": kind, "count": int(count), "max": int(maximum), "slot": slot}

    def _emit_capture_state(self):
        """Emit the active capture (layers/palette/HEX/extra/brush) for the HUD.

        Some captures (HEX, dynamic brush) have no count-changed emitter, so a
        lightweight watch timer polls the engine flags while any capture runs and
        stops itself once everything is idle. Emissions are de-duplicated."""
        if not QThread.isMainThread():
            self._invoke_on_qt(self._emit_capture_state)
            return
        if self._is_shutting_down:
            return
        payload = self._compute_capture_state()
        previous = getattr(self, "_last_capture_payload", None)
        if payload != previous:
            self._last_capture_payload = payload
            try:
                self.captureStateChanged.emit(payload)
            except Exception:
                log.debug('ignored exception in self.captureStateChanged.emit(payload)', exc_info=True)
        self._update_capture_watch(payload["active"])
        if previous and previous["active"] and not payload["active"] and self._preview_pending:
            self._schedule_preview_rebuild(self._preview_pending == "strict",
                                           delay_ms=self._preview_pending_delay_ms)

    def _update_capture_watch(self, active: bool):
        timer = getattr(self, "_capture_watch_timer", None)
        if active:
            if timer is None:
                timer = QTimer(self)
                timer.setInterval(120)
                timer.timeout.connect(self._emit_capture_state)
                self._capture_watch_timer = timer
            if not timer.isActive():
                timer.start()
        elif timer is not None and timer.isActive():
            timer.stop()

    def _on_engine_layers_changed(self, coords=None):
        self._emit_app_layers_changed(coords)






    def _emit_manual_placement_changed(self):
        self._emit_viewport_state()



    def _emit_outline_fill_settings_changed(self, settings=None):
        if settings is None:
            getter = getattr(self.engine, "get_outline_fill_settings", None)
            if callable(getter):
                try:
                    settings = getter()
                except Exception:
                    settings = None
        if settings is None:
            settings = {}
        try:
            self.outlineFillSettingsChanged.emit(dict(settings))
        except Exception:
            log.debug('ignored exception in self.outlineFillSettingsChanged.emit(dict(settings))', exc_info=True)
        self._emit_session_state_changed("outline-fill", sections=("painter",))

    def _on_engine_outline_fill_settings_changed(self, settings=None):
        self._emit_outline_fill_settings_changed(settings)

    def get_outline_fill_settings(self) -> dict:
        getter = getattr(self.engine, "get_outline_fill_settings", None)
        if callable(getter):
            try:
                settings = getter()
            except Exception:
                settings = {}
        else:
            settings = {}
        return dict(settings) if isinstance(settings, dict) else {}

    def _emit_dynamic_brush_settings_changed(self, settings=None):
        if settings is None:
            getter = getattr(self.engine, "get_dynamic_brush_settings", None)
            if callable(getter):
                try:
                    settings = getter()
                except Exception:
                    settings = None
        if settings is None:
            settings = {}
        try:
            self.dynamicBrushSettingsChanged.emit(dict(settings))
        except Exception:
            log.debug('ignored exception in self.dynamicBrushSettingsChanged.emit(dict(settings))', exc_info=True)
        self._emit_session_state_changed("dynamic-brush", sections=("painter",))

    def _on_engine_dynamic_brush_settings_changed(self, settings=None):
        self._emit_dynamic_brush_settings_changed(settings)

    def get_dynamic_brush_settings(self) -> dict:
        getter = getattr(self.engine, "get_dynamic_brush_settings", None)
        if callable(getter):
            try:
                settings = getter()
            except Exception:
                settings = {}
        else:
            settings = {}
        return dict(settings) if isinstance(settings, dict) else {}

    def request_dynamic_brush_learning(self, *, auto_start: bool = False, reason: str = "manual") -> None:
        payload = {"auto_start": bool(auto_start), "reason": str(reason or "manual")}
        try:
            self.dynamicBrushLearningRequested.emit(payload)
        except Exception:
            log.debug('ignored exception in self.dynamicBrushLearningRequested.emit(payload)', exc_info=True)

    def _guard_image_mutation(self, action_hint: str) -> bool:
        """
        Prevent replacing the working image while drawing is active.
        action_hint is a short description used in log/status feedback.
        """
        if self._is_shutting_down or self._close_pending or self.brush_learning_active:
            return False
        if self._is_drawing:
            message = "warn: Идёт рисование — остановите его, прежде чем менять картинку."
            try:
                self.statusChanged.emit(message)
            except Exception:
                log.debug('ignored exception in self.statusChanged.emit(message)', exc_info=True)
            try:
                self.logger.warning("Blocked image change while drawing (%s)", action_hint)
            except Exception:
                log.debug("ignored exception in self.logger.warning('Blocked image change while drawing (%s)', action_hint)", exc_info=True)
            return False
        return True

    # ---------- Image ----------
    def open_image(self, path: str):
        if not self._guard_image_mutation("opening a new image"):
            return False
        with Image.open(path) as im:
            pil = im.convert("RGBA")
        self.cancel_image_download()
        self._engine_set_image(pil, origin=path)
        self._set_session_image_source("file", path)
        revision = self._mark_render_dirty("open_image")
        self.schedule_stencil_sync("open_image", revision, force_hide=True)
        self._rebuild_preview_if_possible()
        self._emit_session_state_changed("open-image", sections=("image", "painter"))
        self._picture_inserted()
        return True


    def _engine_set_image(self, pil_img: Image.Image, origin: str | None = None):
        """Единая точка установки изображения в движок с автосовместимостью по сигнатурам."""
        eng = self.engine
        origin = origin or "unknown"
        self._session_image_revision += 1
        self._session_image_descriptor_cache = None
        self._session_image_png_cache = None

        try:
            self._last_source_qimg = pil_to_qimage(pil_img)
        except Exception:
            self._last_source_qimg = None

        # 1) Самый частый вариант: set_image_from_pil
        if hasattr(eng, "set_image_from_pil"):
            m = getattr(eng, "set_image_from_pil")
            try:
                # попытка: позиционно (img, origin)
                return m(pil_img, origin)
            except TypeError:
                log.debug('ignored exception in return m(pil_img, origin)', exc_info=True)
            try:
                # попытка: именованный source_name
                return m(pil_img, source_name=origin)
            except TypeError:
                log.debug('ignored exception in return m(pil_img, source_name=origin)', exc_info=True)
            try:
                # попытка: только img
                return m(pil_img)
            except TypeError:
                log.debug('ignored exception in return m(pil_img)', exc_info=True)

        # 2) Альтернатива: set_source_image
        if hasattr(eng, "set_source_image"):
            m = getattr(eng, "set_source_image")
            try:
                # попытка: именованный source
                return m(pil_img, source=origin)
            except TypeError:
                log.debug('ignored exception in return m(pil_img, source=origin)', exc_info=True)
            try:
                # попытка: именованный source_name
                return m(pil_img, source_name=origin)
            except TypeError:
                log.debug('ignored exception in return m(pil_img, source_name=origin)', exc_info=True)
            try:
                # попытка: позиционно (img, origin)
                return m(pil_img, origin)
            except TypeError:
                log.debug('ignored exception in return m(pil_img, origin)', exc_info=True)
            try:
                # попытка: только img
                return m(pil_img)
            except TypeError:
                log.debug('ignored exception in return m(pil_img)', exc_info=True)

        raise AttributeError("Движок не содержит подходящего метода для установки изображения.")

    
    def _to_pil(self, img) -> Image.Image:
        # Уже PIL?
        if isinstance(img, Image.Image):
            return img

        # QPixmap -> QImage
        if isinstance(img, QPixmap):
            if img.isNull():
                raise ValueError("QPixmap is null")
            img = img.toImage()

        # QImage -> PIL (через прямой доступ к пикселям)
        if isinstance(img, QImage):
            if img.isNull():
                raise ValueError("QImage is null")

            if img.hasAlphaChannel():
                img = img.convertToFormat(QImage.Format_RGBA8888)
                mode, raw = "RGBA", "RGBA"
            else:
                img = img.convertToFormat(QImage.Format_RGB888)
                mode, raw = "RGB", "RGB"

            w, h = img.width(), img.height()
            stride = img.bytesPerLine()

            # В PySide6 bits()/constBits() -> memoryview; setsize там НЕТ.
            ptr = img.bits()
            # Берём байты всего буфера (с учётом stride)
            data = ptr.tobytes() if hasattr(ptr, "tobytes") else bytes(ptr)

            # Важно: передаём stride в PIL, чтобы учесть выравнивание строк
            pil = Image.frombuffer(mode, (w, h), data, "raw", raw, stride, 1).copy()
            return pil

        raise TypeError(f"Неподдерживаемый тип изображения: {type(img)}")

    def paste_image(self, img_from_ui, origin=None):
        if not self._guard_image_mutation("pasting a new image"):
            return False
        self.cancel_image_download()
        try:
            pil = self._to_pil(img_from_ui)
        except Exception as e:
            self.logger.error(f"Clipboard import failed: {e}")
            try:
                self.statusChanged.emit("error: Не удалось прочитать картинку из буфера обмена.")
            except Exception:
                log.debug("ignored exception reporting an unreadable clipboard image", exc_info=True)
            return False

        try:
            self._engine_set_image(pil, origin=origin or tr("clipboard_source_name"))
            self._set_session_image_source("clipboard_cache", None)
            try:
                revision = self._mark_render_dirty("paste_image")
                self.schedule_stencil_sync("paste_image", revision, force_hide=True)
                self._rebuild_preview_if_possible()
            except Exception:
                log.debug("ignored exception in revision = self._mark_render_dirty('paste_image')", exc_info=True)
            self._refresh_hotkeys_async()
            self._emit_session_state_changed("paste-image", sections=("image", "painter"))
            self._picture_inserted()
            return True
        except Exception as e:
            self.logger.error(f"Engine rejected image from clipboard: {e}")
            try:
                self.statusChanged.emit("error: Картинку из буфера не удалось подготовить к рисованию.")
            except Exception:
                log.debug("ignored exception reporting a rejected clipboard image", exc_info=True)
            return False

    _CLIPBOARD_NO_IMAGE = ("warn: В буфере обмена нет картинки. Скопируйте само изображение "
                           "(в браузере — «Копировать картинку»), файл картинки или ссылку на неё "
                           "и нажмите «Из буфера» ещё раз.")
    _CLIPBOARD_FILE_SUFFIXES = (".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif")

    @classmethod
    def _clipboard_image_file(cls, mime):
        """A picture file copied in Explorer arrives as a file URL, not as pixels."""
        try:
            if mime is None or not mime.hasUrls():
                return None
            for url in mime.urls():
                if url.isLocalFile():
                    path = url.toLocalFile()
                    if path.lower().endswith(cls._CLIPBOARD_FILE_SUFFIXES) and os.path.isfile(path):
                        return path
        except Exception:
            log.debug("ignored exception reading clipboard file urls", exc_info=True)
        return None

    @staticmethod
    def _clipboard_image_link(mime):
        from application import image_sources
        try:
            if mime is None:
                return None
            if mime.hasText():
                link = image_sources.image_url_from_text(mime.text())
                if link is not None:
                    return link
            if mime.hasHtml():
                return image_sources.image_url_from_html(mime.html())
        except Exception:
            log.debug("ignored exception reading clipboard text", exc_info=True)
        return None

    def paste_from_clipboard(self):
        try:
            clipboard_obj = QGuiApplication.clipboard()
        except Exception as exc:
            try:
                self.statusChanged.emit(f"error: Не удалось прочитать буфер обмена: {exc}")
            except Exception:
                log.debug("ignored exception reporting clipboard access failure", exc_info=True)
            return False

        clipboard: QClipboard | None
        if isinstance(clipboard_obj, QClipboard):
            clipboard = clipboard_obj
        else:
            # Older Qt builds can return proxy objects; fall back to the application instance.
            instance = QGuiApplication.instance()
            fallback = None
            try:
                if instance is not None:
                    fallback = instance.clipboard()
            except Exception:
                fallback = None
            clipboard = fallback if isinstance(fallback, QClipboard) else None
            if clipboard is None:
                type_name = type(clipboard_obj).__name__ if clipboard_obj is not None else "None"
                self.logger.debug("Unexpected clipboard object %s; treating as no image", type_name)
                try:
                    self.statusChanged.emit(self._CLIPBOARD_NO_IMAGE)
                except Exception:
                    log.debug("ignored exception reporting an empty clipboard", exc_info=True)
                return False

        try:
            image = clipboard.image()
        except Exception as exc:
            image = None
            primary_error = exc
        else:
            primary_error = None

        mime = None
        if (image is None or image.isNull()) and hasattr(clipboard, "mimeData"):
            try:
                mime = clipboard.mimeData()
            except Exception:
                mime = None
            if mime is not None and mime.hasImage():
                try:
                    data = mime.imageData()
                except Exception as exc:
                    if primary_error is None:
                        primary_error = exc
                    data = None
                if isinstance(data, QImage):
                    image = data
                elif isinstance(data, QPixmap):
                    image = data.toImage()
                elif isinstance(data, Image.Image):
                    try:
                        image = pil_to_qimage(data)
                    except Exception as exc:
                        if primary_error is None:
                            primary_error = exc
                        image = None

        if image is None or image.isNull():
            copied_file = self._clipboard_image_file(mime)
            if copied_file is not None:
                try:
                    return self.open_image(copied_file)
                except Exception as exc:  # damaged file, not an image after all
                    self.logger.warning("Copied file not opened: %s (%s)", copied_file, exc)
                    self.statusChanged.emit("error: Скопированный файл не открывается как картинка.")
                    return False
            link = self._clipboard_image_link(mime)
            if link is not None:  # «Копировать адрес картинки» in a browser
                return self.open_image_url(link)
        if image is None:
            if isinstance(primary_error, AttributeError) or primary_error is None:
                try:
                    self.statusChanged.emit(self._CLIPBOARD_NO_IMAGE)
                except Exception:
                    log.debug("ignored exception reporting an empty clipboard", exc_info=True)
            else:
                try:
                    self.statusChanged.emit(f"error: Не удалось прочитать буфер обмена: {primary_error}")
                except Exception:
                    log.debug("ignored exception reporting a clipboard read failure", exc_info=True)
            return False

        if image.isNull():
            try:
                self.statusChanged.emit(self._CLIPBOARD_NO_IMAGE)
            except Exception:
                log.debug("ignored exception reporting an empty clipboard", exc_info=True)
            return False

        result = self.paste_image(image)
        if not result:
            self._refresh_hotkeys_async()
        return result

    # ---------- Area & stencil ----------
    # ---------- Area & stencil ----------
    def select_area(self):
        from ui.services.stencil_sync import USE_KALKA_OVERLAY
        if USE_KALKA_OVERLAY:
            # Kalka mode: F1 = modern rubber-band placement of the stencil window (the
            # window IS the draw area). Routed to the UI; the old OpenCV ROI never runs.
            try:
                self.kalkaSelectAreaRequested.emit()
            except Exception:
                log.debug("ignored exception emitting kalkaSelectAreaRequested", exc_info=True)
            return
        self.engine.select_area()
        self._emit_viewport_state()
        self._invalidate_visuals(self._INVALIDATE_AREA)





    def get_area_rect(self) -> QRect | None:
        state = self.get_viewport_state()
        return qrect_from_tuple(state.draw_region_desktop_px) if state.draw_region_desktop_px is not None else None

    def get_stretch_to_area(self) -> bool:
        return True

    def set_stretch_to_area(self, enabled: bool, *, rebuild: bool = True) -> None:
        del enabled
        self.apply_viewport_state(
            stretch_to_area=True,
            reset_manual=True,
            invalidate=self._INVALIDATE_PLACEMENT if rebuild else None,
            emit=True,
        )

    def get_manual_image_rect(self):
        return None

    def set_manual_image_rect(self, rect, *, rebuild: bool = True) -> None:
        del rect
        if rebuild:
            self._invalidate_visuals(self._INVALIDATE_PLACEMENT)

    def reset_manual_image_rect(self, *, rebuild: bool = True) -> None:
        if rebuild:
            self._invalidate_visuals(self._INVALIDATE_PLACEMENT)

    def ensure_manual_image_rect(self):
        return None

    def get_source_pixmap(self) -> QPixmap | None:
        if self._last_source_qimg is not None and not self._last_source_qimg.isNull():
            return QPixmap.fromImage(self._last_source_qimg)
        pil = getattr(self.engine, "source_pil_image", None)
        if isinstance(pil, Image.Image):
            try:
                return QPixmap.fromImage(pil_to_qimage(pil))
            except Exception:
                log.debug('ignored exception in return QPixmap.fromImage(pil_to_qimage(pil))', exc_info=True)
        if self._last_qimg is not None and not self._last_qimg.isNull():
            return QPixmap.fromImage(self._last_qimg)
        return None




    def export_image_state(self) -> dict:
        if isinstance(self._session_image_descriptor_cache, dict):
            return deepcopy(self._session_image_descriptor_cache)
        descriptor: dict[str, object] = {}
        kind = self._session_image_kind
        original_path = str(self._session_image_original_path or "").strip()
        image_label = str(getattr(self.engine, "IMAGE_PATH", "") or "").strip()
        clipboard_label = tr("clipboard_source_name")
        if not kind:
            if image_label == clipboard_label:
                kind = "clipboard_cache"
            elif image_label and os.path.exists(image_label):
                kind = "file"
                original_path = image_label
            elif self._get_source_pil_for_session() is not None:
                kind = "session_cache"
                if image_label and image_label != clipboard_label:
                    original_path = image_label
        if not kind:
            return {}
        descriptor["kind"] = kind
        if original_path:
            descriptor["original_path"] = original_path
        if kind == "file" and original_path:
            try:
                stat = os.stat(original_path)
                descriptor["file_size"] = int(stat.st_size)
                descriptor["mtime"] = float(stat.st_mtime)
            except Exception:
                log.debug('ignored exception in stat = os.stat(original_path)', exc_info=True)
        self._session_image_descriptor_cache = deepcopy(descriptor)
        return deepcopy(descriptor)

    def export_session_state(self) -> dict:
        return {
            "painter": self.snapshot_painter_config(),
            "image": self.export_image_state(),
        }

    def export_profile_state(self) -> dict:
        current = self.snapshot_painter_config()
        return {key: deepcopy(current[key]) for key in SESSION_PROFILE_KEYS if key in current}

    def default_profile_state(self) -> dict:
        """Settings of a place used for the first time: engine defaults, not the
        calibrations of the place the user came from."""
        if getattr(self, "_default_profile", None) is None:
            from engine.olegpainter.core import OlegPainter
            defaults = OlegPainter(status_callback=lambda message: None).get_config()
            self._default_profile = {key: deepcopy(defaults[key]) for key in SESSION_PROFILE_KEYS if key in defaults}
        return deepcopy(self._default_profile)

    def apply_profile_state(self, profile_state) -> None:
        if not isinstance(profile_state, dict):
            return
        merged = self.snapshot_painter_config()
        for key in SESSION_PROFILE_KEYS:
            if key in profile_state:
                merged[key] = deepcopy(profile_state[key])
        # A profile / place switch only changes DRAWING settings — it must NOT re-assert
        # the image source. Carrying IMAGE_PATH + source_is_clipboard_placeholder into
        # load_config destroys a clipboard image (the engine "reloads" from a placeholder
        # path that has no file -> source_pil_image=None -> NoneType crash on reprocess).
        # Stripping them lets config_service.load_config's preserve-image branch keep the
        # in-memory picture intact across the switch.
        merged.pop("IMAGE_PATH", None)
        merged.pop("source_is_clipboard_placeholder", None)
        self.load_config(merged)



    # ---------- Settings ----------
    def set_k_clusters(self, k: int):
        self.engine.set_k_clusters(int(k))
        self._invalidate_visuals(self._INVALIDATE_RENDER, strict=True)

    def set_draw_delay(self, sec: float):
        self.engine.set_draw_delay(float(sec))
        self._emit_session_state_changed("draw-delay", sections=("painter",))

    def set_pen_button_delay(self, sec: float):
        """Per-area pen down/up settle (sec). Lower = faster on many tiny regions."""
        try:
            self.engine.pen_button_delay = max(0.0, float(sec))
        except Exception:
            log.debug("ignored exception in set_pen_button_delay", exc_info=True)
        self._emit_session_state_changed("pen-button-delay", sections=("painter",))

    def set_pen_press_nudge(self, enabled: bool):
        try:
            self.engine.pen_press_nudge = bool(enabled)
        except Exception:
            log.debug("ignored exception in set_pen_press_nudge", exc_info=True)
        self._emit_session_state_changed("pen-press-nudge", sections=("painter",))

    def set_pen_split_strokes(self, enabled: bool):
        try:
            self.engine.pen_split_strokes = bool(enabled)
        except Exception:
            log.debug("ignored exception in set_pen_split_strokes", exc_info=True)
        self._emit_session_state_changed("pen-split-strokes", sections=("painter",))

    def set_hex_field_opened_by_actions(self, enabled: bool):
        self.engine.hex_field_opened_by_actions = bool(enabled)
        self._emit_session_state_changed("hex-field-opened", sections=("painter",))

    def set_pen_stroke_gap(self, sec: float):
        try:
            self.engine.pen_stroke_gap = min(1.0, max(0.0, float(sec)))
        except Exception:
            log.debug("ignored exception in set_pen_stroke_gap", exc_info=True)
        self._emit_session_state_changed("pen-stroke-gap", sections=("painter",))

    def set_pen_settle_delay(self, sec: float):
        """Settle after moving to a region start + after pen-down (sec)."""
        try:
            self.engine.pen_settle_delay = max(0.0, float(sec))
        except Exception:
            log.debug("ignored exception in set_pen_settle_delay", exc_info=True)
        self._emit_session_state_changed("pen-settle-delay", sections=("painter",))

    def set_area_fill_delay(self, sec: float):
        """Fill speed of LARGE areas: pause per pixel of travel along long strokes
        (0 = instant). Draw-time only; does not affect small areas."""
        try:
            self.engine.area_fill_delay = max(0.0, float(sec))
        except Exception:
            log.debug("ignored exception in set_area_fill_delay", exc_info=True)
        self._emit_session_state_changed("area-fill-delay", sections=("painter",))

    def set_pen_max_step(self, px: int):
        """Max pen step (px) for CONTINUOUS strokes: when > 0 the pen walks long moves
        in <=px hops instead of one instant jump — needed for targets that stamp at the
        sampled cursor position (e.g. Roblox spray) so there are no gaps. 0 = off."""
        try:
            self.engine.pen_max_step = max(0, int(px))
        except Exception:
            log.debug("ignored exception in set_pen_max_step", exc_info=True)
        self._emit_session_state_changed("pen-max-step", sections=("painter",))

    def set_focus_target_window_enabled(self, enabled: bool):
        """Focus the target window before drawing so its first click (color selection)
        isn't eaten by window activation (which drew the wrong/default color). On by
        default; turn off if it brings the wrong window forward on your setup."""
        try:
            self.engine.focus_target_window_enabled = bool(enabled)
        except Exception:
            log.debug("ignored exception in set_focus_target_window_enabled", exc_info=True)
        self._emit_session_state_changed("focus-target-window", sections=("painter",))

    def set_brush_size(self, px: int):
        self.engine.set_brush_size(int(px))
        self._invalidate_visuals(self._INVALIDATE_RENDER, strict=True)

    def set_prep_color_space(self, value: str):
        self._set_prep_setting("prep_color_space", value)

    def set_prep_quantization_mode(self, value: str):
        self._set_prep_setting("prep_quantization_mode", value)

    def set_prep_cleanup_mode(self, value: str):
        self._set_prep_setting("prep_cleanup_mode", value)

    def set_fast_min_region_area(self, area: int):
        try:
            if hasattr(self.engine, "fast_min_region_area"):
                self.engine.fast_min_region_area = max(0, int(area))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_fast_min_region_area failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: set_fast_min_region_area failed: {e}')", exc_info=True)
        else:
            self._invalidate_heavy_preview(strict=False)

    def recommend_color_count(self) -> dict:
        """Ask the engine to recommend a perceptually-distinct color count for the
        loaded image. Returns the engine's transparent dict (count/threshold/...)."""
        fn = getattr(self.engine, "recommend_color_count", None)
        if callable(fn):
            try:
                res = fn()
                return res if isinstance(res, dict) else {"count": int(res)}
            except Exception as e:
                log.debug(f"recommend_color_count failed: {e}", exc_info=True)
        return {"count": 0, "reason": "unavailable"}

    def set_color_merge_threshold(self, value: float):
        """Merge near-identical palette colors (OKLab dE) so the color count is a
        MAX, not forced. 0 = off. Changes the palette/cluster_map -> re-render."""
        try:
            self.engine.color_merge_threshold = max(0.0, float(value))
            self._invalidate_heavy_preview(strict=True)
            self._emit_session_state_changed("color-merge-threshold", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_color_merge_threshold failed: {e}")
            except Exception:
                log.debug("ignored exception in set_color_merge_threshold", exc_info=True)

    def set_prep_dither_enabled(self, enabled: bool):
        """Experimental: Floyd-Steinberg dithering of the palette assignment
        (perceptual color space only). Changes the cluster_map -> re-render."""
        try:
            if hasattr(self.engine, "prep_dither_enabled"):
                self.engine.prep_dither_enabled = bool(enabled)
            self._invalidate_heavy_preview(strict=True)
            self._emit_session_state_changed("prep-dither-enabled", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_prep_dither_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in set_prep_dither_enabled", exc_info=True)

    def set_run_length_merge_enabled(self, enabled: bool):
        """Experimental: collapse straight runs in the fill into single pen drags
        (same pixels, far fewer mouse moves -> faster). Draw-time only."""
        try:
            if hasattr(self.engine, "run_length_merge_enabled"):
                self.engine.run_length_merge_enabled = bool(enabled)
            self._emit_session_state_changed("run-length-merge-enabled", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_run_length_merge_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in set_run_length_merge_enabled", exc_info=True)

    def set_skip_matching_canvas(self, enabled: bool):
        """Mark cells whose canvas color already matches as drawn before a run. Draw-time only."""
        try:
            if hasattr(self.engine, "skip_matching_canvas"):
                self.engine.skip_matching_canvas = bool(enabled)
            self._emit_session_state_changed("skip-matching-canvas", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_skip_matching_canvas failed: {e}")
            except Exception:
                log.debug("ignored exception in set_skip_matching_canvas", exc_info=True)

    def set_cheap_bridges_enabled(self, enabled: bool):
        """Short in-colour detours instead of pen lifts when cheaper. Draw-time only."""
        try:
            if hasattr(self.engine, "cheap_bridges_enabled"):
                self.engine.cheap_bridges_enabled = bool(enabled)
            self._emit_session_state_changed("cheap-bridges-enabled", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_cheap_bridges_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in set_cheap_bridges_enabled", exc_info=True)

    def set_astar_bridge_enabled(self, enabled: bool):
        """Experimental: traverse fills with DFS + A* bridges (pen routes through
        on-color cells across backtracks instead of cutting straight lines).
        Draw-time only."""
        try:
            if hasattr(self.engine, "astar_bridge_enabled"):
                self.engine.astar_bridge_enabled = bool(enabled)
            self._emit_session_state_changed("astar-bridge-enabled", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_astar_bridge_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in set_astar_bridge_enabled", exc_info=True)

    def set_manual_match_space(self, space: str):
        """Manual-palette nearest-color space: 'oklab' (truer hues) or 'lab' (legacy).
        Pick-time only — does not touch clustering or calibration."""
        try:
            if hasattr(self.engine, "manual_match_space"):
                normalize = getattr(self.engine, "_normalize_manual_match_space", None)
                self.engine.manual_match_space = normalize(space) if callable(normalize) else str(space)
            self._emit_session_state_changed("manual-match-space", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_manual_match_space failed: {e}")
            except Exception:
                log.debug("ignored exception in set_manual_match_space", exc_info=True)

    def set_euler_greedy_pairing_enabled(self, enabled: bool):
        """Experimental: greedy odd-vertex pairing lets the single-line (Euler)
        route handle regions of ANY complexity instead of giving up past the DP
        limit. May retrace painted cells (slight overdraw). Draw-time only."""
        try:
            if hasattr(self.engine, "euler_greedy_pairing_enabled"):
                self.engine.euler_greedy_pairing_enabled = bool(enabled)
            self._emit_session_state_changed("euler-greedy-pairing", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_euler_greedy_pairing_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in set_euler_greedy_pairing_enabled", exc_info=True)

    def set_fill_traversal_mode(self, mode: str):
        """Experimental fill visit order: 'auto' (snake/DFS, the default) or
        'gilbert' (generalized-Hilbert curve order). Draw-time only."""
        try:
            if hasattr(self.engine, "fill_traversal_mode"):
                normalize = getattr(self.engine, "_normalize_fill_traversal_mode", None)
                self.engine.fill_traversal_mode = normalize(mode) if callable(normalize) else str(mode)
            self._emit_session_state_changed("fill-traversal-mode", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_fill_traversal_mode failed: {e}")
            except Exception:
                log.debug("ignored exception in set_fill_traversal_mode", exc_info=True)

    def set_area_order_2opt_enabled(self, enabled: bool):
        """2-opt pass over the nearest-region order: same regions, shorter pen
        travel between them. Affects only inter-region transitions."""
        try:
            if hasattr(self.engine, "area_order_2opt_enabled"):
                self.engine.area_order_2opt_enabled = bool(enabled)
            self._emit_session_state_changed("area-order-2opt", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_area_order_2opt_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in set_area_order_2opt_enabled", exc_info=True)

    def set_semantic_order_mode(self, mode: str):
        """Semantic color order: 'off' (legacy tone order) or 'bg_first' (paint
        the backdrop colors first, then objects, then details). Re-sorts the
        palette immediately so the change is visible before the next draw."""
        try:
            if hasattr(self.engine, "semantic_order_mode"):
                normalize = getattr(self.engine, "_normalize_semantic_order_mode", None)
                self.engine.semantic_order_mode = normalize(mode) if callable(normalize) else str(mode)
                try:
                    self.engine._apply_palette_sorting()
                except Exception:
                    log.debug("ignored exception re-sorting palette", exc_info=True)
            self._emit_session_state_changed("semantic-order-mode", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_semantic_order_mode failed: {e}")
            except Exception:
                log.debug("ignored exception in set_semantic_order_mode", exc_info=True)

    def set_semantic_threshold(self, value: float):
        """Backdrop/object saliency cutoff (0.05..0.95). Applied at READ time —
        re-sorting the palette is enough, the network is NOT re-run."""
        try:
            if hasattr(self.engine, "semantic_threshold"):
                try:
                    self.engine.semantic_threshold = max(0.05, min(0.95, float(value)))
                except (TypeError, ValueError):
                    self.engine.semantic_threshold = 0.35
                try:
                    self.engine._apply_palette_sorting()
                except Exception:
                    log.debug("ignored exception re-sorting palette", exc_info=True)
            self._emit_session_state_changed("semantic-threshold", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_semantic_threshold failed: {e}")
            except Exception:
                log.debug("ignored exception in set_semantic_threshold", exc_info=True)

    def set_semantic_model_id(self, model_id: str):
        """Which AI matting model classifies the planes. Drops the saliency
        cache (its key includes the model) and re-sorts the palette — the next
        read re-runs inference with the new model."""
        try:
            if hasattr(self.engine, "semantic_model_id"):
                self.engine.semantic_model_id = str(model_id or "").strip() or "bg.u2netp"
                self.engine._semantic_saliency_cache = None
                try:
                    self.engine._apply_palette_sorting()
                except Exception:
                    log.debug("ignored exception re-sorting palette", exc_info=True)
            self._emit_session_state_changed("semantic-model", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_semantic_model_id failed: {e}")
            except Exception:
                log.debug("ignored exception in set_semantic_model_id", exc_info=True)

    def set_semantic_region_split_enabled(self, enabled: bool):
        """Per-REGION planes: a mixed color is drawn in two passes (backdrop
        regions in the backdrop phase, object regions in the object phase)."""
        try:
            if hasattr(self.engine, "semantic_region_split_enabled"):
                self.engine.semantic_region_split_enabled = bool(enabled)
            self._emit_session_state_changed("semantic-region-split", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_semantic_region_split_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in set_semantic_region_split_enabled", exc_info=True)

    def set_semantic_background_drop(self, value: str):
        """Drop a semantic plane from drawing: 'off' / 'backdrop' (don't draw
        the background) / 'object'. Rebuilds the preview so the change is
        visible (it re-runs the prep, which re-applies the plane drop)."""
        try:
            if hasattr(self.engine, "semantic_background_drop"):
                normalize = getattr(self.engine, "_normalize_semantic_background_drop", None)
                self.engine.semantic_background_drop = (
                    normalize(value) if callable(normalize) else str(value or "off"))
                try:
                    self._rebuild_preview_if_possible()
                except Exception:
                    log.debug("ignored exception rebuilding preview", exc_info=True)
            self._emit_session_state_changed("semantic-background-drop", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_semantic_background_drop failed: {e}")
            except Exception:
                log.debug("ignored exception in set_semantic_background_drop", exc_info=True)

    def set_palette_match_metric(self, value: str):
        """Final fixed-palette match metric: 'ciede2000' (accurate) or
        'euclidean' (fast). Affects manual/screen-palette picking at draw time
        only — no preview rebuild needed."""
        try:
            metric = "ciede2000" if str(value or "").strip().lower() == "ciede2000" else "euclidean"
            if hasattr(self.engine, "palette_match_metric"):
                self.engine.palette_match_metric = metric
            self._emit_session_state_changed("palette-match-metric", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_palette_match_metric failed: {e}")
            except Exception:
                log.debug("ignored exception in set_palette_match_metric", exc_info=True)

    def set_prep_preserve_accents(self, enabled: bool):
        """Chroma-weight the palette k-means so rare saturated accents survive.
        Perceptual path only; re-runs the prep so the change is visible."""
        try:
            if hasattr(self.engine, "prep_preserve_accents"):
                self.engine.prep_preserve_accents = bool(enabled)
                try:
                    self._rebuild_preview_if_possible()
                except Exception:
                    log.debug("ignored exception rebuilding preview", exc_info=True)
            self._emit_session_state_changed("preserve-accents", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_prep_preserve_accents failed: {e}")
            except Exception:
                log.debug("ignored exception in set_prep_preserve_accents", exc_info=True)

    def set_prep_keep_details(self, enabled: bool):
        """Cleanup keeps small regions that contrast with their surroundings (DETAILS-001)."""
        self.engine.prep_keep_details = bool(enabled)
        self._rebuild_preview_if_possible()
        self._emit_session_state_changed("keep-details", sections=("painter",))

    def set_aggressive_despeckle_enabled(self, enabled: bool):
        """Relabel every sub-threshold region into the nearest large region
        (no tiny dots survive). Re-runs the prep so the effect is visible."""
        try:
            if hasattr(self.engine, "aggressive_despeckle_enabled"):
                self.engine.aggressive_despeckle_enabled = bool(enabled)
                try:
                    self._rebuild_preview_if_possible()
                except Exception:
                    log.debug("ignored exception rebuilding preview", exc_info=True)
            self._emit_session_state_changed("aggressive-despeckle", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_aggressive_despeckle_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in set_aggressive_despeckle_enabled", exc_info=True)

    def set_motion_profile_enabled(self, enabled: bool):
        """Plotter-style speed profile for paced fills (area_fill_delay > 0):
        cruise on straights, slow into corners. Pixels are bit-identical —
        only the sleep durations between sub-steps change."""
        try:
            if hasattr(self.engine, "motion_profile_enabled"):
                self.engine.motion_profile_enabled = bool(enabled)
            self._emit_session_state_changed("motion-profile", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_motion_profile_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in set_motion_profile_enabled", exc_info=True)

    def set_fill_route_polish_enabled(self, enabled: bool):
        """Re-chain the lift-separated runs of a built visit order (same cells,
        cheaper pen lifts; guarded never-worse). Draw-time only."""
        try:
            if hasattr(self.engine, "fill_route_polish_enabled"):
                self.engine.fill_route_polish_enabled = bool(enabled)
            self._emit_session_state_changed("fill-route-polish", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_fill_route_polish_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in set_fill_route_polish_enabled", exc_info=True)

    def set_snake_turn_minimize_enabled(self, enabled: bool):
        """Pick the snake axis by strip count instead of bbox aspect (fewer
        turns/dead-ends on striped shapes). Draw-time only."""
        try:
            if hasattr(self.engine, "snake_turn_minimize_enabled"):
                self.engine.snake_turn_minimize_enabled = bool(enabled)
            self._emit_session_state_changed("snake-turn-minimize", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_snake_turn_minimize_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in set_snake_turn_minimize_enabled", exc_info=True)

    def set_area_order_or_opt_enabled(self, enabled: bool):
        """Or-opt (segment relocation) after 2-opt over the region order: same
        regions, even shorter pen travel between them."""
        try:
            if hasattr(self.engine, "area_order_or_opt_enabled"):
                self.engine.area_order_or_opt_enabled = bool(enabled)
            self._emit_session_state_changed("area-order-or-opt", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_area_order_or_opt_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in set_area_order_or_opt_enabled", exc_info=True)

    def set_area_entry_exit_routing_enabled(self, enabled: bool):
        """Experimental: enter the next region at its cell nearest to the pen's
        REAL exit point (instead of the region's top-left cell / centroid
        anchor). Same picture, shorter hops; stroke start points change."""
        try:
            if hasattr(self.engine, "area_entry_exit_routing_enabled"):
                self.engine.area_entry_exit_routing_enabled = bool(enabled)
            self._emit_session_state_changed("area-entry-exit", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_area_entry_exit_routing_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in set_area_entry_exit_routing_enabled", exc_info=True)

    def set_telemetry_enabled(self, enabled: bool):
        """Session telemetry (JSONL metrics in %LOCALAPPDATA%): pure diagnostics,
        drawing behaviour is untouched either way."""
        try:
            if hasattr(self.engine, "telemetry_enabled"):
                self.engine.telemetry_enabled = bool(enabled)
            self._emit_session_state_changed("telemetry-enabled", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_telemetry_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in set_telemetry_enabled", exc_info=True)

    def set_post_draw_repair_enabled(self, enabled: bool):
        try:
            if hasattr(self.engine, "post_draw_repair_enabled"):
                self.engine.post_draw_repair_enabled = bool(enabled)
            self._emit_session_state_changed("post-draw-repair", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_post_draw_repair_enabled failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: set_post_draw_repair_enabled failed: {e}')", exc_info=True)
            return
        if not bool(enabled):
            self._engine_on_post_draw_repair(
                {
                    "pass_index": 0,
                    "total_passes": max(1, int(getattr(self.engine, "post_draw_repair_passes", 1) or 1)),
                    "detected_cells": 0,
                    "repaired_cells": 0,
                    "remaining_cells": 0,
                    "unverifiable_cells": 0,
                    "regions": [],
                    "overlay": None,
                    "draw_region": getattr(self.engine, "draw_region", None),
                    "message": "",
                }
            )

    def set_post_draw_repair_passes(self, passes: int):
        try:
            clamped = max(1, min(3, int(passes)))
            if hasattr(self.engine, "post_draw_repair_passes"):
                self.engine.post_draw_repair_passes = clamped
            self._emit_session_state_changed("post-draw-repair", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_post_draw_repair_passes failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: set_post_draw_repair_passes failed: {e}')", exc_info=True)

    def set_post_draw_repair_mode(self, value: str):
        try:
            normalized = "aggressive" if str(value or "").strip().lower() == "aggressive" else "conservative"
            if hasattr(self.engine, "post_draw_repair_mode"):
                self.engine.post_draw_repair_mode = normalized
            self._emit_session_state_changed("post-draw-repair", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_post_draw_repair_mode failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: set_post_draw_repair_mode failed: {e}')", exc_info=True)

    def set_post_draw_repair_sensitivity(self, value: int):
        try:
            clamped = max(50, min(200, int(value)))
            if hasattr(self.engine, "post_draw_repair_sensitivity"):
                self.engine.post_draw_repair_sensitivity = clamped
            self._emit_session_state_changed("post-draw-repair", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_post_draw_repair_sensitivity failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: set_post_draw_repair_sensitivity failed: {e}')", exc_info=True)

    def set_post_draw_repair_color_mismatch_sensitivity(self, value: int):
        try:
            clamped = max(50, min(200, int(value)))
            if hasattr(self.engine, "post_draw_repair_color_mismatch_sensitivity"):
                self.engine.post_draw_repair_color_mismatch_sensitivity = clamped
            self._emit_session_state_changed("post-draw-repair", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_post_draw_repair_color_mismatch_sensitivity failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: set_post_draw_repair_color_mismatch_sensitiv...", exc_info=True)

    def _update_dynamic_brush(self, **kwargs):
        self.update_brush_settings({"step_value" if key == "step" else key: value for key, value in kwargs.items()})

    def set_dynamic_brush_control_mode(self, mode: str):
        self.update_brush_settings(dict(control_mode=mode))

    def set_dynamic_brush_enabled(self, enabled: bool):
        self.update_brush_settings(dict(enabled=enabled))

    def set_dynamic_brush_drag_enabled(self, enabled: bool):
        self.update_brush_settings(dict(drag_enabled=enabled))

    def set_dynamic_brush_verify_at_draw(self, enabled: bool):
        self.update_brush_settings(dict(verify_at_draw=enabled))

    def set_dynamic_brush_min(self, value: float):
        self._update_dynamic_brush(min_value=value)

    def set_dynamic_brush_max(self, value: float):
        self._update_dynamic_brush(max_value=value)

    def set_dynamic_brush_default(self, value: float):
        self._update_dynamic_brush(default_value=value)

    def set_dynamic_brush_step(self, step: float):
        self._update_dynamic_brush(step=step)

    def reset_dynamic_brush_profile(self):
        self._guard_brush_edit()
        self._brush_learning_message = self._brush_learning_error = ""
        self.engine.reset_dynamic_brush_profile()
        self._emit_dynamic_brush_settings_changed()

    def start_dynamic_brush_scratch_zone_capture(self):
        self.request_brush_capture("scratch")

    def start_dynamic_brush_point_capture(self, value: float):
        self.request_brush_capture("point", value)

    def clear_dynamic_brush_points(self):
        self._guard_brush_edit()
        self._brush_learning_message = self._brush_learning_error = ""
        self.engine.dynamic_brush_points = []
        self._invalidate_brush_profile()
        self._emit_dynamic_brush_settings_changed()

    def learn_dynamic_brush_profile(self):
        return self.start_brush_learning()

    def cancel_active_captures(self):
        canceller = getattr(self.engine, "_cancel_active_captures", None)
        if callable(canceller):
            try:
                canceller()
            except Exception:
                log.debug('ignored exception in canceller()', exc_info=True)

    # ---------- Config ----------


    # ---------- Drawing ----------
    def _start_in_worker(self, fn, *, on_result: Callable[[object], None] | None = None,
                         on_error: Callable[[str], None] | None = None) -> bool:
        if self._is_shutting_down:
            return False
        if self._worker_thread is not None:
            try:
                if self._thread_is_running(self._worker_thread):
                    try:
                        self.statusChanged.emit("warn: Предыдущая команда рисования ещё выполняется — подождите секунду.")
                    except Exception:
                        log.debug("ignored exception emitting the busy worker warning", exc_info=True)
                    return False
            except Exception:
                self._worker_thread = None
                self._worker = None
        if self._worker_thread is not None:
            try:
                self._worker_thread.quit()
                self._worker_thread.wait(200)
            except Exception:
                log.debug('ignored exception in self._worker_thread.quit()', exc_info=True)
            self._worker_thread = None
            self._worker = None
        self._worker_thread = QThread()
        self._worker = _Worker(fn)
        self._worker.moveToThread(self._worker_thread)
        self._worker_thread.started.connect(self._worker.run)
        self._worker.finished.connect(self._worker_thread.quit)
        self._worker.finished.connect(self._worker.deleteLater)
        self._worker_thread.finished.connect(self._worker_thread.deleteLater)
        # Drop our Python references once the C++ objects are scheduled for
        # deletion. Otherwise a finished thread deletes itself via deleteLater
        # while self._worker_thread still points at it, and the next start
        # calls .quit() on the dead object -> "Internal C++ object already
        # deleted" RuntimeError.
        self._worker_thread.finished.connect(self._on_worker_thread_finished)
        if callable(on_result):
            self._worker.resultReady.connect(
                lambda result: on_result(result) if not self._is_shutting_down else None)
        if on_error is not None:
            self._worker.error.connect(on_error)
        else:
            self._worker.error.connect(lambda e: self.statusChanged.emit(f"error: drawing worker failed: {e}"))
        self._worker_thread.start()
        return True

    def _on_worker_thread_finished(self) -> None:
        """Clear stale worker references after the thread has finished.

        Runs alongside the queued ``deleteLater`` so the next draw start sees
        ``self._worker_thread is None`` and builds a fresh thread instead of
        poking a deleted C++ object.
        """
        if self.sender() is not self._worker_thread:
            return
        self._worker = None
        self._worker_thread = None













    # ---------- Tools/Calibrations ----------
    # --------- AI model helpers ---------









    def set_alpha_background_threshold(self, value: float) -> None:
        self.set_background_removal_state(alpha_threshold=value, invalidate=False)

    def set_background_color_tolerance(self, value: float) -> None:
        self.set_background_removal_state(color_tolerance=value, invalidate=False)

    def set_background_reference_rgb(self, rgb) -> None:
        self.set_background_removal_state(reference_rgb=rgb, invalidate=False)

    def set_background_removal_state(
        self,
        *,
        enabled: bool | object = _UNSET,
        mode: str | object = _UNSET,
        alpha_threshold: float | object = _UNSET,
        color_tolerance: float | object = _UNSET,
        reference_rgb: tuple[int, int, int] | list[int] | None | object = _UNSET,
        invalidate: bool = False,
    ) -> None:
        # Validate the entire patch before touching the engine. A failed command
        # must not leave a partially applied background configuration.
        def channel(value):
            if type(value) not in (int, float) or not 0 <= value <= 255:
                raise ValueError("Значение должно быть числом от 0 до 255.")
            return int(round(value))

        updates = {}
        if enabled is not _UNSET:
            if type(enabled) is not bool:
                raise ValueError("Включение удаления фона должно быть логическим значением.")
            updates.update(background_removal_enabled=enabled, remove_background=enabled)
        if mode is not _UNSET:
            if mode not in ("alpha", "corner", "picked"):
                raise ValueError("Неизвестный способ удаления фона.")
            updates["background_removal_mode"] = mode
        if alpha_threshold is not _UNSET:
            value = channel(alpha_threshold)
            updates.update(background_alpha_threshold=value, alpha_threshold=value)
        if color_tolerance is not _UNSET:
            value = channel(color_tolerance)
            updates.update(background_color_tolerance=value, bg_tolerance=value)
        if reference_rgb is not _UNSET:
            if reference_rgb is not None and (
                not isinstance(reference_rgb, (list, tuple)) or len(reference_rgb) != 3
            ):
                raise ValueError("Цвет фона должен содержать три канала RGB.")
            updates["background_reference_rgb"] = (
                tuple(channel(v) for v in reference_rgb) if reference_rgb is not None else None
            )
        engine = self.engine
        if invalidate and updates.get("background_removal_enabled", engine.background_removal_enabled):
            if (updates.get("background_removal_mode", engine.background_removal_mode) == "picked"
                    and updates.get("background_reference_rgb", engine.background_reference_rgb) is None):
                raise ValueError("Сначала выберите цвет фона на изображении или введите HEX.")
        if all(getattr(engine, key) == value for key, value in updates.items()):
            return
        for key, value in updates.items():
            setattr(engine, key, value)
        self._emit_session_state_changed("background-removal", sections=("painter",))
        if invalidate:
            self._invalidate_visuals(self._INVALIDATE_RENDER)



    def accept_sd_license(self, accepted: bool):
        try:
            setattr(self.engine, 'ai_outpaint_sd_license', bool(accepted))
        except Exception as exc:
            self.statusChanged.emit(f'error: set_sd_license failed: {exc}')










    def calibrate_color_circle(self):
        if self._reject_input_during_brush_learning():
            return
        self.screenCalibrationRequested.emit("ring")

    def calibrate_brightness_slider(self):
        if self._reject_input_during_brush_learning():
            return
        self.screenCalibrationRequested.emit("slider")

    def calibrate_screen_palette(self):
        if self._reject_input_during_brush_learning():
            return
        self.screenCalibrationRequested.emit("palette")

    def calibrate_wheel_square(self):
        if self._reject_input_during_brush_learning():
            return
        self.screenCalibrationRequested.emit("wheel")

    def start_hex_coord_capture(self):
        if self._reject_input_during_brush_learning():
            return
        try:
            self._open_colour_box_for_capture()
            self.engine.start_hex_coord_capture()
            self._emit_capture_state()
        except Exception as e: self.statusChanged.emit(f"error: start_hex_coord_capture failed: {e}")

    def _open_colour_box_for_capture(self):
        """Where the HEX box appears only after the recorded clicks (Gartic Phone),
        replay them first so the user can point at the box itself."""
        engine = self.engine
        if not (getattr(engine, "hex_field_opened_by_actions", False) and engine._should_play_actions("pre")):
            return
        if engine.drawing_enabled:
            return
        engine.stop_flag = False
        try:
            with engine._owned_automation_input("открытие окна цвета перед указанием HEX"):
                engine._play_actions_sequence("pre")
        except Exception:
            log.debug("could not open the colour box before HEX capture", exc_info=True)

    def start_dynamic_brush_coord_capture(self):
        self.request_brush_capture("text")

    def start_dynamic_brush_slider_capture(self):
        self.request_brush_capture("slider")

    def calibrate_dynamic_brush(self):
        return self.start_brush_learning()

    def get_dynamic_brush_calibration(self):
        try:
            calib = getattr(self.engine, "dynamic_brush_calibration", None)
            if isinstance(calib, dict):
                return dict(calib)
        except Exception:
            log.debug("ignored exception in calib = getattr(self.engine, 'dynamic_brush_calibration', None)", exc_info=True)
        return None

    def start_app_layer_coord_capture(self):
        if self._reject_input_during_brush_learning():
            return
        try: self.engine.start_app_layer_coord_capture()
        except Exception as e: self.statusChanged.emit(f"error: start_app_layer_coord_capture failed: {e}")

    # ---------- Engine callbacks ----------





    def _parse_progress_args(self, args):
        if not args:
            return None, None, None
        payload = args[0] if len(args) == 1 else args
        try:
            if isinstance(payload, (tuple, list)):
                if len(payload) == 2:
                    done = int(payload[0])
                    total = max(1, int(payload[1]))
                    pct = int(min(100, max(0, round(done * 100 / total))))
                    return pct, done, total
            if len(args) == 2:
                done = int(args[0])
                total = max(1, int(args[1]))
                pct = int(min(100, max(0, round(done * 100 / total))))
                return pct, done, total
            value = float(payload)
            if math.isnan(value) or math.isinf(value):
                return None, None, None
            if 0.0 <= value <= 1.0:
                pct = int(round(value * 100))
            else:
                pct = int(round(value))
            return max(0, min(100, pct)), None, None
        except Exception:
            return None, None, None

    def _update_progress_metrics(self, *, force=False, percent_override=None, done_override=None, total_override=None,
                                 sample=None, phase=None):
        now = time.monotonic()
        if not force and (now - self._progress_last_emit) < self._progress_emit_min_interval:
            return
        sample = sample or self.engine.drawing_progress_snapshot()
        completed = phase == DrawingPhase.COMPLETED
        terminal = phase is not None and phase.terminal
        percent = None if percent_override is None else max(0, min(100, int(percent_override)))
        done_pixels = None if done_override is None else max(0, int(done_override))
        total_pixels = None if total_override is None else max(1, int(total_override))
        if done_pixels is None or total_pixels is None:
            total_candidate = sample.total_pixels
            done_candidate = sample.done_pixels
            if done_pixels is None:
                done_pixels = done_candidate
            if total_pixels is None:
                total_pixels = total_candidate
        if total_pixels:
            try:
                percent_from_counts = int(min(100, max(0, round(done_pixels * 100 / total_pixels))))
            except Exception:
                percent_from_counts = None
            if percent_from_counts is not None:
                percent = percent_from_counts
            elif percent is None:
                percent = 0
        elif percent is None:
            percent = 0
        percent = max(0, min(100, int(percent)))
        if completed:
            percent = 100
        # Pixels can reach 100% while the engine is still running a trailing pass
        # (e.g. post-draw repair / finishing). `_is_drawing` stays True until the
        # engine reports the COMPLETED event (which fires AFTER that pass),
        # so until then we hold at 99% and flag the phase as "finishing" instead
        # of prematurely showing "done" / 0:00 ETA.
        finishing = (
            percent >= 100
            and bool(getattr(self, "_is_drawing", False))
            and not terminal
        )
        if finishing:
            percent = 99
        # Monotonic progress: within one drawing session the displayed percent
        # never goes backwards (jitter reads as "it broke"); resets when idle.
        if bool(getattr(self, "_is_drawing", False)) and sample.smart_eta:
            floor = int(getattr(self, "_percent_floor", 0) or 0)
            if percent < floor:
                percent = floor
            else:
                self._percent_floor = percent
        else:
            self._percent_floor = 0
        colors_total = sample.colors_total
        colors_done = colors_total if completed else sample.colors_done
        colors_remaining = max(0, colors_total - colors_done)
        eta_seconds = None
        eta_range = None
        if finishing:
            eta_seconds = None  # duration of the trailing pass is unknown
        elif percent >= 100:
            eta_seconds = 0.0
        else:
            have_counts = done_pixels is not None and total_pixels
            if have_counts and done_pixels > 0:
                remaining_pixels = max(0, total_pixels - done_pixels)
                if remaining_pixels > 0:
                    elapsed = sample.elapsed_seconds
                    if elapsed is not None and elapsed >= 0:
                        eta_seconds = elapsed * (remaining_pixels / done_pixels)
                        if sample.smart_eta:
                            # MEASURED two-term model: regress elapsed on
                            # (pixels_done, regions_done) and price the remaining
                            # work at the observed rates. Unlike the config-weight
                            # model below it stays honest when sleep granularity
                            # dwarfs draw_delay (many tiny regions => real cost is
                            # per-REGION, which the pixel ratio cannot see).
                            try:
                                done_px, total_px = sample.done_pixels, sample.total_pixels
                                regions_done, regions_total = sample.regions_done, sample.regions_total
                                samples = getattr(self, "_eta_samples", None)
                                if samples is None:
                                    samples = self._eta_samples = []
                                if samples and (elapsed < samples[-1][0] or done_px < samples[-1][1]):
                                    samples.clear()  # a new drawing session started
                                    if getattr(self, "_eta_pred_history", None):
                                        self._eta_pred_history.clear()
                                if not samples or (elapsed - samples[-1][0]) >= 0.2:
                                    samples.append((float(elapsed), float(done_px), float(regions_done)))
                                    if len(samples) > 600:
                                        del samples[:300]
                                smart = solve_two_term_eta(
                                    samples,
                                    max(0, total_px - done_px),
                                    max(0, regions_total - regions_done),
                                )
                                if smart is not None:
                                    eta_seconds = smart
                                    # Honest interval instead of a twitchy point: track
                                    # how much completion estimates made >=10 s ago
                                    # disagree with the current one and widen the shown
                                    # range by that observed instability (ACI-style).
                                    hist = getattr(self, "_eta_pred_history", None)
                                    if hist is None:
                                        hist = self._eta_pred_history = []
                                    total_est = float(elapsed) + float(smart)
                                    hist.append((float(elapsed), total_est))
                                    if len(hist) > 200:
                                        del hist[:100]
                                    rel = sorted(
                                        abs(t_est - total_est) / max(float(smart), 1.0)
                                        for t_old, t_est in hist if (elapsed - t_old) >= 10.0
                                    )
                                    if len(rel) >= 5:
                                        q = rel[min(len(rel) - 1, int(0.9 * len(rel)))]
                                        q = min(0.8, max(0.12, float(q)))
                                        eta_range = (max(0.0, smart * (1.0 - q)), smart * (1.0 + q))
                            except Exception:
                                log.debug("smart ETA failed; using pixel ratio", exc_info=True)
                        else:
                            try:
                                # Legacy: weight remaining work by the CONFIG-derived
                                # per-region pen overhead. Falls back to the pixel
                                # ratio above on failure.
                                rem_work, total_work = sample.remaining_work, sample.total_work
                                done_work = total_work - rem_work
                                if rem_work > 0 and done_work > 1e-6:
                                    eta_seconds = elapsed * (rem_work / done_work)
                            except Exception:
                                log.debug("ignored exception in work-weighted ETA", exc_info=True)
            elif percent == 0:
                eta_seconds = None
        if terminal and not completed or phase == DrawingPhase.STOPPING:
            eta_seconds, eta_range = None, None
        eta_text = self._format_eta(eta_seconds, colors_remaining, colors_total, eta_range=eta_range)
        colors_done = max(0, colors_total - colors_remaining)
        current_color = None if completed else sample.current_color
        elapsed_seconds = sample.elapsed_seconds
        try:
            self.drawingStatsChanged.emit({
                "run_id": sample.run_id,
                "percent": percent,
                "eta_seconds": eta_seconds,
                "eta_text": eta_text,
                "elapsed_seconds": elapsed_seconds,
                "colors_total": colors_total,
                "colors_done": colors_done,
                "colors_remaining": colors_remaining,
                "current_color": current_color,
                "phase": phase.value if terminal or phase == DrawingPhase.STOPPING else (
                    "finishing" if finishing else "drawing"),
            })
        except Exception:
            log.debug('ignored exception in self.drawingStatsChanged.emit(...)', exc_info=True)
        emitted = False
        if force or self._progress_snapshot.get("percent") != percent:
            try:
                self.progressChanged.emit(percent)
            except Exception:
                log.debug('ignored exception in self.progressChanged.emit(percent)', exc_info=True)
            self._progress_snapshot["percent"] = percent
            emitted = True
        if force or self._progress_snapshot.get("eta_text") != eta_text:
            try:
                self.etaChanged.emit(eta_text)
            except Exception:
                log.debug('ignored exception in self.etaChanged.emit(eta_text)', exc_info=True)
            self._progress_snapshot["eta_text"] = eta_text
            emitted = True
        if emitted or force:
            self._progress_last_emit = now

    @staticmethod
    def _format_duration(value: float) -> str:
        total_seconds = max(0, int(round(float(value))))
        hours, remainder = divmod(total_seconds, 3600)
        minutes, seconds = divmod(remainder, 60)
        if hours > 0:
            return f"{hours:d}:{minutes:02d}:{seconds:02d}"
        return f"{minutes:02d}:{seconds:02d}"

    def _format_eta(self, eta_seconds, remaining_colors, total_colors, eta_range=None):
        valid_point = (eta_seconds is not None and isinstance(eta_seconds, (int, float))
                       and math.isfinite(eta_seconds))
        valid_range = False
        try:
            valid_range = (eta_range is not None and math.isfinite(eta_range[0])
                           and math.isfinite(eta_range[1]) and eta_range[1] > eta_range[0] >= 0
                           and (eta_range[1] - eta_range[0]) >= 5.0)
        except Exception:
            valid_range = False
        if valid_range:
            # honest uncertainty: "~ 04:10–06:30" instead of a twitchy single number
            eta_part = f"~ {self._format_duration(eta_range[0])}–{self._format_duration(eta_range[1])}"
        elif not valid_point:
            eta_part = "~ --:--"
        else:
            eta_part = f"~ {self._format_duration(eta_seconds)}"
        if total_colors is None or total_colors <= 0:
            color_part = "ост -/-"
        else:
            remaining = remaining_colors if remaining_colors is not None else total_colors
            try:
                remaining = max(0, min(int(remaining), total_colors))
            except Exception:
                remaining = max(0, total_colors)
            color_part = f"ост {remaining}/{total_colors}"
        return f"{eta_part}  {color_part}"

    # ---------- Helpers ----------
    def _invoke_on_qt(self, fn):
        if not callable(fn) or self._is_shutting_down:
            return
        try:
            if QThread.isMainThread():
                fn()
            else:
                QTimer.singleShot(0, self, lambda: fn() if not self._is_shutting_down else None)
        except Exception as exc:
            try:
                self.logger.debug("hotkey invocation failed: %s", exc)
            except Exception:
                log.debug("ignored exception in self.logger.debug('hotkey invocation failed: %s', exc)", exc_info=True)





    def _engine_on_skipped_regions(self, payload=None):
        if getattr(self, "_is_shutting_down", False):
            return
        if not isinstance(payload, dict):
            return

        def _handle():
            if getattr(self, "_is_shutting_down", False):
                return
            self._last_skipped_regions_payload = payload
            overlay_img = payload.get("overlay")
            draw_region = payload.get("draw_region")
            if overlay_img is not None and draw_region is not None:
                composed = overlay_img
                if isinstance(overlay_img, Image.Image):
                    base_img = getattr(self.engine, "quantized_preview_image", None)
                    if getattr(self.engine, "stencil_enabled", False) and isinstance(base_img, Image.Image):
                        try:
                            composed = base_img.convert("RGBA")
                            composed.alpha_composite(overlay_img)
                        except Exception:
                            composed = overlay_img
                elif isinstance(overlay_img, QImage):
                    try:
                        composed = ImageQt.ImageQt(overlay_img)
                        if composed.mode != "RGBA":
                            composed = composed.convert("RGBA")
                    except Exception:
                        composed = None
                else:
                    composed = None
                if composed is not None:
                    self._engine_show_stencil(composed, draw_region, blocking=False, allow_if_disabled=True)
                    if not getattr(self.engine, "stencil_enabled", False):
                        self._schedule_skipped_overlay_hide()
            regions = payload.get("regions") or []
            threshold = payload.get("threshold")
            try:
                self.logger.info("Skipped %s region(s) below %s: %s", len(regions), threshold, [item.get('area') for item in regions[:5]])
            except Exception:
                log.debug("ignored exception in self.logger.info('Skipped %s region(s) below %s: %s', len(regions), threshold...", exc_info=True)

        self._invoke_on_qt(_handle)

    def _engine_on_post_draw_repair(self, payload=None):
        if getattr(self, "_is_shutting_down", False):
            return
        if not isinstance(payload, dict):
            return

        def _handle():
            if getattr(self, "_is_shutting_down", False):
                return
            self._last_post_draw_repair_payload = payload
            draw_region = payload.get("draw_region") or getattr(self.engine, "draw_region", None)
            overlay_img = payload.get("overlay")
            regions = payload.get("regions") or []
            message = str(payload.get("message") or "").strip()
            try:
                remaining_cells = max(0, int(payload.get("remaining_cells", 0) or 0))
            except Exception:
                remaining_cells = 0

            if remaining_cells > 0 and overlay_img is not None and draw_region is not None:
                composed = overlay_img
                if isinstance(overlay_img, Image.Image):
                    base_img = getattr(self.engine, "quantized_preview_image", None)
                    if getattr(self.engine, "stencil_enabled", False) and isinstance(base_img, Image.Image):
                        try:
                            composed = base_img.convert("RGBA")
                            composed.alpha_composite(overlay_img)
                        except Exception:
                            composed = overlay_img
                elif isinstance(overlay_img, QImage):
                    try:
                        composed = ImageQt.ImageQt(overlay_img)
                        if composed.mode != "RGBA":
                            composed = composed.convert("RGBA")
                    except Exception:
                        composed = None
                else:
                    composed = None
                if composed is not None:
                    self._engine_show_stencil(composed, draw_region, blocking=False, allow_if_disabled=True)
            else:
                revision = self._preview_active_revision or self._preview_display_revision or self._render_revision
                if self._sync_requested_stencil_state_from_engine():
                    base_qimg = self._current_overlay_qimage()
                    if base_qimg is not None:
                        self.schedule_stencil_sync(
                            "post-draw-repair-clear",
                            revision,
                            image=base_qimg,
                            draw_region=draw_region,
                        )
                    else:
                        self._engine_hide_stencil(blocking=False)
                else:
                    self._engine_hide_stencil(blocking=False)

            try:
                self.logger.info(
                    "Post-draw repair: remaining=%s regions=%s message=%s",
                    remaining_cells,
                    len(regions),
                    message,
                )
            except Exception:
                log.debug("ignored exception in self.logger.info('Post-draw repair: remaining=%s regions=%s message=%s', rema...", exc_info=True)

        self._invoke_on_qt(_handle)




    def _area_rect_to_physical(self, rect) -> tuple[float, float, float, float] | None:
        if rect is None:
            return None
        draw_region = getattr(self.engine, "draw_region", None)
        if not draw_region or len(draw_region) != 4:
            return None
        try:
            rx, ry, rw, rh = rect
        except Exception:
            return None
        x0, y0, _, _ = draw_region
        return (float(x0) + float(rx), float(y0) + float(ry), float(rw), float(rh))

    def _physical_rect_to_area(self, rect) -> tuple[float, float, float, float] | None:
        if rect is None:
            return None
        draw_region = getattr(self.engine, "draw_region", None)
        if not draw_region or len(draw_region) != 4:
            return None
        try:
            rx, ry, rw, rh = rect
        except Exception:
            return None
        x0, y0, _, _ = draw_region
        rel_x = float(rx) - float(x0)
        rel_y = float(ry) - float(y0)
        return (rel_x, rel_y, float(rw), float(rh))

    def _normalize_area_rect(self, rect) -> tuple[int, int, int, int] | None:
        return normalize_desktop_rect(rect)










    def get_preparation_snapshot(self) -> dict:
        missing = []
        missing_action_codes: list[str] = []
        preview_busy = self._preview_pending is not None or self._preview_thread is not None
        try:
            source_img = self._last_source_qimg
            image_loaded = bool(source_img is not None and not source_img.isNull())
        except Exception:
            image_loaded = False
        if not image_loaded:
            try:
                pil = getattr(self.engine, "source_pil_image", None)
                image_loaded = isinstance(pil, Image.Image)
            except Exception:
                image_loaded = False

        try:
            preview_ready = bool(not preview_busy and self._last_qimg is not None and not self._last_qimg.isNull())
        except Exception:
            preview_ready = False

        state = self.get_viewport_state()
        area_selected = state.draw_region_desktop_px is not None
        area_stale = bool(area_selected and state.stale)
        preview_blocked_by: list[str] = []

        if not image_loaded:
            missing.append(tr("prep_missing_load_image"))
            missing_action_codes.append("load_image")
            preview_blocked_by.append("load_image")
        if not area_selected:
            missing.append(tr("prep_missing_select_area"))
            missing_action_codes.append("select_area")
            preview_blocked_by.append("select_area")
        elif area_stale:
            missing.append(tr("prep_missing_reselect_area"))
            missing_action_codes.append("reselect_area")
            preview_blocked_by.append("reselect_area")

        color_ready, has_hex, has_hsv, has_manual, active_method = self._color_tools_ready()
        required_calibrations: list[str] = []
        if not color_ready:
            if active_method == "manual_palette":
                required_calibrations.append("manual_palette")
                missing.append(tr("prep_missing_manual_palette"))
                missing_action_codes.append("capture_manual_palette")
            elif active_method == "hsv_palette":
                required_calibrations.append("hsv_palette")
                missing.append(tr("prep_missing_hsv"))
                missing_action_codes.append("calibrate_hsv_palette")
            elif active_method == "screen_palette":
                required_calibrations.append("screen_palette")
                missing.append("• Обведите рамкой палитру программы (кнопка «Настроить цвет»).")
                missing_action_codes.append("calibrate_screen_palette")
            elif active_method == "wheel_square":
                required_calibrations.append("wheel_square")
                missing.append("• Обведите рамкой цветовое колесо программы (кнопка «Настроить цвет»).")
                missing_action_codes.append("calibrate_wheel_square")
            else:
                required_calibrations.append("hex_field")
                missing.append(tr("prep_missing_hex"))
                missing_action_codes.append("calibrate_hex_field")

        from application.color_mixing import valid_alpha_slider
        if (active_method == "manual_palette" and self.engine.manual_palette_mix_enabled
                and not valid_alpha_slider(self.engine.alpha_slider_params)):
            required_calibrations.append("alpha_slider")
            missing.append("• Настройте ползунок непрозрачности для смешивания цветов (страница «Палитра»).")
            missing_action_codes.append("calibrate_alpha_slider")

        if self._compute_capture_state()["active"] or self.desktop_interaction or self._hotkey_capture_active:
            missing.append(tr("prep_missing_finish_capture"))
            missing_action_codes.append("finish_capture")

        if preview_busy:
            missing.append("• Дождитесь подготовки изображения.")
            missing_action_codes.append("wait_preview")
        if self.brush_learning_active:
            missing.append("• Дождитесь завершения обучения кисти.")
            missing_action_codes.append("wait_brush_learning")

        return {
            "image_loaded": bool(image_loaded),
            "area_selected": bool(area_selected),
            "area_stale": bool(area_stale),
            "preview_ready": bool(preview_ready),
            "preview_busy": preview_busy,
            "active_method": str(active_method or ""),
            "required_calibrations": tuple(required_calibrations),
            "missing_requirements": tuple(missing),
            "missing_action_codes": tuple(missing_action_codes),
            "preview_blocked_by": tuple(preview_blocked_by),
            "next_action_code": str(missing_action_codes[0] if missing_action_codes else ""),
            "can_start": not missing,
            "color_tool_ready": bool(color_ready),
            "has_hex": bool(has_hex),
            "has_hsv": bool(has_hsv),
            "has_manual": bool(has_manual),
        }

    def _preflight_check(self):
        snapshot = self.get_preparation_snapshot()
        missing = list(snapshot.get("missing_requirements") or ())
        if missing:
            return False, tr("prep_preflight_header") + "\n" + "\n".join(missing)
        return True, ""

    def _dynamic_brush_should_prompt_learning(self) -> bool:
        settings = self.get_dynamic_brush_settings()
        enabled = bool(settings.get("enabled"))
        runtime_algo = str(getattr(self.engine, "drawing_algorithm", "") or "").strip().lower()
        profile_state = str(settings.get("profile_state") or "needs_learning").strip().lower()
        return bool(enabled and runtime_algo == "dfs_4dir" and profile_state != "ready")

    @staticmethod
    def _is_numeric_tuple(value, *, length: int) -> bool:
        if not isinstance(value, (tuple, list)) or len(value) != int(length):
            return False
        return all(isinstance(item, (int, float)) for item in value)

    @classmethod
    def _is_valid_hsv_circle_calibration(cls, value) -> bool:
        return cls._is_numeric_tuple(value, length=4)

    @staticmethod
    def _is_valid_hsv_slider_calibration(value) -> bool:
        if not isinstance(value, (tuple, list)) or len(value) != 4:
            return False
        orientation = str(value[0] or "").strip().lower()
        if orientation not in {"vertical", "horizontal"}:
            return False
        return all(isinstance(item, (int, float)) for item in value[1:])

    def _color_tools_ready(self):
        has_hex = False
        has_hsv = False
        has_manual = False
        try:
            v = getattr(self.engine, "hex_input_coord", None)
            if isinstance(v, (tuple, list)) and len(v) >= 2:
                has_hex = True
            elif hasattr(self.engine, "hex_field_coords"):
                v = getattr(self.engine, "hex_field_coords")
                if isinstance(v, (tuple, list)) and len(v) >= 2:
                    has_hex = True
        except Exception:
            log.debug("ignored exception in v = getattr(self.engine, 'hex_input_coord', None)", exc_info=True)
        try:
            cc = getattr(self.engine, "circle_params_calib", None)
            sl = getattr(self.engine, "slider_params_calib", None)
            if self._is_valid_hsv_circle_calibration(cc) and self._is_valid_hsv_slider_calibration(sl):
                has_hsv = True
        except Exception:
            log.debug("ignored exception in cc = getattr(self.engine, 'circle_params_calib', None)", exc_info=True)
        try:
            palette = getattr(self.engine, "manual_palette_coords", None)
            if palette and isinstance(palette, (list, tuple)) and len(palette) > 0:
                has_manual = True
        except Exception:
            log.debug("ignored exception in palette = getattr(self.engine, 'manual_palette_coords', None)", exc_info=True)
        try:
            cfg = self.engine.get_config()
            if isinstance(cfg, dict):
                for k, v in cfg.items():
                    kl = str(k).lower()
                    if "hex" in kl and ("coord" in kl or "pos" in kl or "rect" in kl):
                        if isinstance(v, (tuple, list)) and len(v) >= 2:
                            has_hex = True
                circle_cfg = cfg.get("circle_params_calib")
                slider_cfg = cfg.get("slider_params_calib")
                if self._is_valid_hsv_circle_calibration(circle_cfg) and self._is_valid_hsv_slider_calibration(slider_cfg):
                    has_hsv = True
                if not has_manual:
                    palette_cfg = cfg.get("manual_palette_coords")
                    if isinstance(palette_cfg, (list, tuple)) and len(palette_cfg) > 0:
                        has_manual = True
        except Exception:
            log.debug('ignored exception in cfg = self.engine.get_config()', exc_info=True)
        current_method = getattr(self.engine, "color_picking_method", "hex_field")
        if current_method == "manual_palette":
            ready = has_manual
        elif current_method == "hsv_palette":
            ready = has_hsv
        elif current_method == "screen_palette":
            palette = self.engine.screen_palette_calib
            ready = bool(isinstance(palette, dict) and len(palette.get("lab", ())) > 0
                         and len(palette.get("lab", ())) == len(palette.get("coords", ())))
        elif current_method == "wheel_square":
            from engine.olegpainter import wheel_picker
            ready = wheel_picker.valid(getattr(self.engine, "wheel_square_calib", None))
        else:
            ready = has_hex
        return ready, has_hex, has_hsv, has_manual, current_method


       # ---------- Layers (App) ----------
    def set_layers_enabled(self, enabled: bool):
        """Флаг «рисовать по слоям» (без запуска записи координат)."""
        try:
            for attr in ("draw_with_layers_enabled", "layers_enabled", "use_layers"):
                if hasattr(self.engine, attr):
                    setattr(self.engine, attr, bool(enabled))
                    break
            self.statusChanged.emit(f"layers: {'on' if enabled else 'off'}")
            self._emit_session_state_changed("layers-enabled", sections=("painter",))
        except Exception as e:
            self.statusChanged.emit(f"error: set_layers_enabled failed: {e}")

    def set_color_picking_method(self, method: str):
        try:
            if hasattr(self.engine, "color_picking_method"):
                self.engine.color_picking_method = method
            self._emit_manual_palette_changed()
            self._invalidate_visuals(self._INVALIDATE_RENDER)
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_color_picking_method failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: set_color_picking_method failed: {e}')", exc_info=True)

    def set_hex_add_hash(self, enabled: bool):
        try:
            if hasattr(self.engine, "hex_add_hash"):
                self.engine.hex_add_hash = bool(enabled)
            self._emit_session_state_changed("hex-add-hash", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_hex_add_hash failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: set_hex_add_hash failed: {e}')", exc_info=True)



    def calibrate_alpha_slider(self):
        if self._reject_input_during_brush_learning():
            return
        self._require_manual_palette_edit(getattr(self, "_manual_palette_revision", 0))
        if not getattr(self, "_brush_capture_available", False):
            raise RuntimeError("Экранные инструменты не подключены.")
        self.screenCalibrationRequested.emit("alpha")

    def set_drawing_algorithm(self, algorithm: str):
        try:
            normalized = str(algorithm or "").strip().lower()
            if normalized == "dfs_4dir_dynamic":
                normalized = "dfs_4dir"
                enable_dynamic = getattr(self.engine, "set_dynamic_brush_enabled", None)
                if callable(enable_dynamic):
                    try:
                        enable_dynamic(True)
                    except Exception:
                        log.debug('ignored exception in enable_dynamic(True)', exc_info=True)
            if hasattr(self.engine, "drawing_algorithm"):
                self.engine.drawing_algorithm = normalized
            self._invalidate_visuals(self._INVALIDATE_RENDER)
            self._emit_dynamic_brush_settings_changed()
            self._emit_session_state_changed("drawing-algorithm", sections=("painter",))
        except Exception as e:
            try:
                self.statusChanged.emit(f"error: set_drawing_algorithm failed: {e}")
            except Exception:
                log.debug("ignored exception in self.statusChanged.emit(f'error: set_drawing_algorithm failed: {e}')", exc_info=True)

    def set_outline_fill_tool_mode(self, mode: str):
        setter = getattr(self.engine, "set_outline_fill_tool_mode", None)
        if callable(setter):
            try:
                setter(mode)
                self._emit_session_state_changed("outline-fill-mode", sections=("painter",))
            except Exception as e:
                try:
                    self.statusChanged.emit(f"error: set_outline_fill_tool_mode failed: {e}")
                except Exception:
                    log.debug("ignored exception in self.statusChanged.emit(f'error: set_outline_fill_tool_mode failed: {e}')", exc_info=True)

    def set_outline_fill_brush_key(self, sequence: str):
        setter = getattr(self.engine, "set_outline_fill_brush_key", None)
        if callable(setter):
            try:
                setter(sequence)
                self._emit_session_state_changed("outline-fill-brush-key", sections=("painter",))
            except Exception as e:
                try:
                    self.statusChanged.emit(f"error: set_outline_fill_brush_key failed: {e}")
                except Exception:
                    log.debug("ignored exception in self.statusChanged.emit(f'error: set_outline_fill_brush_key failed: {e}')", exc_info=True)

    def set_outline_fill_fill_key(self, sequence: str):
        setter = getattr(self.engine, "set_outline_fill_fill_key", None)
        if callable(setter):
            try:
                setter(sequence)
                self._emit_session_state_changed("outline-fill-fill-key", sections=("painter",))
            except Exception as e:
                try:
                    self.statusChanged.emit(f"error: set_outline_fill_fill_key failed: {e}")
                except Exception:
                    log.debug("ignored exception in self.statusChanged.emit(f'error: set_outline_fill_fill_key failed: {e}')", exc_info=True)

    def set_outline_fill_coords(self, brush_coord=None, fill_coord=None):
        setter = getattr(self.engine, "set_outline_fill_coords", None)
        if callable(setter):
            try:
                setter(brush_coord, fill_coord)
                self._emit_session_state_changed("outline-fill-coords", sections=("painter",))
            except Exception as e:
                try:
                    self.statusChanged.emit(f"error: set_outline_fill_coords failed: {e}")
                except Exception:
                    log.debug("ignored exception in self.statusChanged.emit(f'error: set_outline_fill_coords failed: {e}')", exc_info=True)

    def start_outline_fill_coord_capture(self):
        if self._reject_input_during_brush_learning():
            return
        starter = getattr(self.engine, "start_outline_fill_coord_capture", None)
        if callable(starter):
            try:
                starter()
            except Exception as e:
                try:
                    self.statusChanged.emit(f"error: start_outline_fill_coord_capture failed: {e}")
                except Exception:
                    log.debug("ignored exception in self.statusChanged.emit(f'error: start_outline_fill_coord_capture failed: {e}')", exc_info=True)




    def start_pre_color_actions_capture(self):
        self.start_extra_actions_capture("pre")

    def start_post_color_actions_capture(self):
        self.start_extra_actions_capture("post")


    def toggle_pre_color_actions_capture(self):
        self.toggle_extra_actions_capture("pre")

    def toggle_post_color_actions_capture(self):
        self.toggle_extra_actions_capture("post")


    def set_pre_actions_enabled(self, enabled: bool):
        self.set_extra_actions_enabled(enabled, "pre")

    def set_post_actions_enabled(self, enabled: bool):
        self.set_extra_actions_enabled(enabled, "post")

    def toggle_app_layer_coord_capture(self):
        """Одна кнопка «Задать слои»: старт/стоп записи координат."""
        if self._reject_input_during_brush_learning():
            return
        if self._should_bounce_toggle("app_layer"):
            return
        try:
            capturing = bool(getattr(self.engine, "is_capturing_app_layer_coords", False))
        except Exception:
            capturing = False
        try:
            if capturing:
                if hasattr(self.engine, "finish_app_layer_coord_capture"):
                    self.engine.finish_app_layer_coord_capture()
            else:
                if hasattr(self.engine, "start_app_layer_coord_capture"):
                    self.engine.start_app_layer_coord_capture()
        except Exception as e:
            self.statusChanged.emit(f"error: toggle_app_layer_coord_capture failed: {e}")

    def _get_current_pil(self) -> Image.Image:
        """Достаёт актуальную картинку для ИИ (RGBA). Приоритет: то, что сейчас в предпросмотре."""
        # 1) Если есть QImage предпросмотра — конвертируем (самый надёжный вариант)
        if getattr(self, "_last_qimg", None) and not self._last_qimg.isNull():
            pil = self._to_pil(self._last_qimg)
            return pil.convert("RGBA") if pil.mode != "RGBA" else pil

        # 2) Если есть последний источник из UI — используем его.
        if getattr(self, "_last_source_qimg", None) and not self._last_source_qimg.isNull():
            pil = self._to_pil(self._last_source_qimg)
            return pil.convert("RGBA") if pil.mode != "RGBA" else pil

        # 3) Прямой доступ к полям движка (частый случай: source_pil_image).
        for attr in ("source_pil_image", "image_original_rgba"):
            pil = getattr(self.engine, attr, None)
            if isinstance(pil, Image.Image):
                return pil.convert("RGBA") if pil.mode != "RGBA" else pil

        # 4) Если движок умеет отдать исходник через геттеры (совместимость по версиям).
        for getter in ("get_current_pil_image", "get_source_image", "get_source_pil"):
            if hasattr(self.engine, getter):
                pil = getattr(self.engine, getter)()
                if isinstance(pil, Image.Image):
                    return pil.convert("RGBA") if pil.mode != "RGBA" else pil

        # 5) Последний fallback: путь к исходному файлу в движке.
        image_path = getattr(self.engine, "IMAGE_PATH", "")
        if isinstance(image_path, str):
            image_path = image_path.strip()
            if image_path and image_path != tr("clipboard_source_name") and os.path.exists(image_path):
                with Image.open(image_path) as im:
                    pil = im.convert("RGBA")
                return pil

        raise RuntimeError("Нет изображения для ИИ-обработки.")







    def get_current_image_info(self) -> Dict[str, int] | None:
        """Return basic metadata (width/height) for the current image."""
        try:
            pil = self._get_current_pil()
        except Exception:
            return None
        return {"width": pil.width, "height": pil.height}

    # --- Explicit control wrappers for clearer UI binding ---


    # --- Convenience helpers for presets/models ---


    
    # ---------- Hotkeys (definitions, storage, registration) ----------

    def _normalize_sequence(self, sequence) -> str:
        if not isinstance(sequence, str):
            raise ValueError("сочетание клавиш должно быть текстом")
        if not sequence.strip():
            return ""
        seq = QKeySequence(sequence)
        if seq.count() != 1:
            raise ValueError("укажите одно сочетание клавиш")
        combo = seq[0]
        key = combo.key()
        mods = combo.keyboardModifiers()
        if key in (Qt.Key_unknown, Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta, 0):
            raise ValueError("это сочетание нельзя назначить")
        allowed = Qt.ControlModifier | Qt.AltModifier | Qt.ShiftModifier | Qt.MetaModifier
        if mods.value & ~allowed.value:
            raise ValueError("недопустимая клавиша-модификатор")
        if key == Qt.Key_Enter:
            key = Qt.Key_Return
        elif key == Qt.Key_Backtab:
            key = Qt.Key_Tab
            mods |= Qt.ShiftModifier
        if int(key) <= 0x10FFFF:
            char = chr(key)
            char = self._RU_TO_EN_LAYOUT.get(char.lower(), char)
            # All presentations and the Win32 hook use the same physical chord.
            shifted = dict(zip('~!@#$%^&*()_+{}:"<>?|', '`1234567890-=[];\',./\\'))
            if char in shifted:
                char = shifted[char]
                mods |= Qt.ShiftModifier
            key = ord(char.upper()) if len(char.upper()) == 1 else key
        return QKeySequence(mods.value | int(key)).toString(QKeySequence.PortableText)

    def _format_sequence_native(self, sequence: str) -> str:
        if not sequence:
            return ""
        try:
            pretty = QKeySequence(sequence).toString(QKeySequence.NativeText)
            return pretty or sequence
        except Exception:
            return sequence








    def _global_action_map(self) -> Dict[str, Callable[[], None]]:
        routes: Dict[str, Callable[[], None]] = {}
        for code, names in self._GLOBAL_HOTKEY_ROUTES.items():
            candidate_names = (names,) if isinstance(names, str) else tuple(names)
            for name in candidate_names:
                cb = getattr(self, name, None)
                if callable(cb):
                    routes[code] = cb
                    break
            else:
                self.logger.debug("No callable bound for global hotkey '%s'", code)
        return routes

    def toggle_overlay(self):
        """Global-hotkey target: ask the UI to show/hide the HUD overlay."""
        try:
            self.toggleOverlayRequested.emit()
        except Exception:
            log.debug('ignored exception in self.toggleOverlayRequested.emit()', exc_info=True)

    def request_layout_edit(self):
        """Global-hotkey target (Alt+F2): ask the UI to toggle the unified layout
        edit mode (HUD blocks + draw area)."""
        try:
            self.layoutEditRequested.emit()
        except Exception:
            log.debug('ignored exception in self.layoutEditRequested.emit()', exc_info=True)

    def set_stencil_opacity(self, percent):
        """Set the stencil ghost opacity (10..100 %) live on the overlay."""
        try:
            pct = max(10, min(100, int(percent)))
        except Exception:
            return
        self._stencil_view_opacity = pct / 100.0
        overlay = getattr(self, "_overlay", None)
        if overlay is not None and hasattr(overlay, "set_view_opacity"):
            try:
                overlay.set_view_opacity(self._stencil_view_opacity)
            except Exception:
                log.debug('ignored exception in overlay.set_view_opacity', exc_info=True)





