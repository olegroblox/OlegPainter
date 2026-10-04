"""Mixin extracted from ui/services/painter_service.py.

Config / session persistence: snapshot, restore, save, hotkeys mapping.
"""
from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal, Slot, QTimer  # noqa: F401
from PySide6.QtGui import QImage  # noqa: F401
from PIL import Image, ImageQt  # noqa: F401
from pathlib import Path  # noqa: F401
from copy import deepcopy  # noqa: F401
import time, os, traceback, json, math, inspect  # noqa: F401
import logging
from typing import Dict

from app_paths import get_app_paths  # noqa: F401

from engine.ai import (  # noqa: F401
    AiFeatureDisabled,
    AiModelDescriptor,
    AiModelRegistry,
    DisabledAiBackend,
    ModelTask,
    ai_deferred_message,
    describe_execution_provider,
)
from engine.olegpainter.viewport_state import (  # noqa: F401
    VIEWPORT_UNSET,
    ViewportState,
    normalize_area_rect,
    normalize_desktop_rect,
)
from ui.helpers.config_migration import (  # noqa: F401
    migrate_json_file_in_place,
    sanitize_painter_config,
)
from ui.helpers.hotkey_definitions import (  # noqa: F401
    HOTKEY_DEFINITIONS,
    HotkeyDefinition,
    default_hotkey_profile,
    definition_for_code,
    definitions_by_scope,
    scopes as hotkey_scopes,
)
from ui.i18n import tr  # noqa: F401

from ui.services._sentinel import _UNSET  # noqa: F401

log = logging.getLogger("olegpainter.painter_service")


class ConfigServiceMixin:
    """Config / session persistence: snapshot, restore, save, hotkeys mapping."""

    @staticmethod
    def _prune_removed_viewport_fields(config: dict | None) -> dict:
        if not isinstance(config, dict):
            return {}
        pruned = deepcopy(config)
        pruned.pop("stretch_to_area", None)
        pruned.pop("manual_image_rect", None)
        return pruned

    def snapshot_painter_config(self) -> dict:
        try:
            cfg = self.engine.get_config()
        except Exception:
            cfg = {}
        cfg = self._prune_removed_viewport_fields(cfg if isinstance(cfg, dict) else {})
        cfg["prep_color_space"] = self._prep_color_space
        cfg["prep_quantization_mode"] = self._prep_quantization_mode
        cfg["prep_cleanup_mode"] = self._prep_cleanup_mode
        return cfg

    def restore_image_state(self, image_state, *, cache_base_dir: str | os.PathLike[str] | None = None) -> bool:
        if not isinstance(image_state, dict):
            return False
        kind = str(image_state.get("kind") or "").strip().lower()
        original_path = str(image_state.get("original_path") or "").strip()
        cache_path_raw = str(image_state.get("cache_path") or "").strip()
        cache_relpath = str(image_state.get("cache_relpath") or "").strip()
        cache_root = Path(cache_base_dir) if cache_base_dir else get_app_paths().configs_root
        candidate_cache = None
        if cache_path_raw:
            candidate_cache = Path(cache_path_raw)
        elif cache_relpath:
            rel_path = Path(cache_relpath)
            candidate_cache = rel_path if rel_path.is_absolute() else (cache_root / rel_path)
        load_path = ""
        if kind == "file" and original_path and os.path.exists(original_path):
            load_path = original_path
        elif candidate_cache is not None and candidate_cache.exists():
            load_path = str(candidate_cache)
        if not load_path:
            return False
        with Image.open(load_path) as im:
            pil = im.convert("RGBA")
        origin = original_path or load_path
        if kind == "clipboard_cache":
            origin = tr("clipboard_source_name")
        self._engine_set_image(pil, origin=origin)
        self._set_session_image_source(kind or "session_cache", original_path or None)
        self._emit_session_state_changed("restore-image", sections=("image", "painter"))
        return True

    def restore_session_state(self, session_state) -> None:
        if not isinstance(session_state, dict):
            return
        image_state = session_state.get("image")
        painter_state = session_state.get("painter")
        image_restored = False
        if isinstance(image_state, dict):
            try:
                image_restored = self.restore_image_state(image_state)
            except Exception:
                image_restored = False
        if isinstance(painter_state, dict):
            payload = deepcopy(painter_state)
            if image_restored:
                payload.pop("IMAGE_PATH", None)
                payload.pop("source_is_clipboard_placeholder", None)
            else:
                # Image could NOT be restored (original file moved/deleted AND no cached copy).
                # Don't carry a DEAD IMAGE_PATH into the engine — load_config would set a path
                # that points nowhere and zero out the pixels. Dropping both keys makes
                # load_config preserve the (empty) current image instead of a broken reference.
                # A clipboard placeholder has no real path, so it's left untouched.
                raw_path = str(payload.get("IMAGE_PATH", "") or "").strip()
                is_clip = (
                    bool(payload.get("source_is_clipboard_placeholder"))
                    or raw_path == tr("clipboard_source_name")
                )
                if raw_path and not is_clip and not os.path.exists(raw_path):
                    self.logger.warning(
                        "Session restore: image source '%s' is gone; dropping dead IMAGE_PATH.",
                        raw_path,
                    )
                    payload.pop("IMAGE_PATH", None)
                    payload.pop("source_is_clipboard_placeholder", None)
            self.load_config(payload)

    def get_config(self) -> dict:
        try:
            cfg = self.snapshot_painter_config()
            self.configLoaded.emit(cfg)
            self._emit_viewport_state()
            self._emit_app_layers_changed()
            self._emit_extra_actions_changed()
            self._emit_outline_fill_settings_changed()
            self._emit_dynamic_brush_settings_changed()
            return cfg
        except Exception as e:
            self.statusChanged.emit(f"error: get_config failed: {e}")
            return {}

    def load_config(self, source):
        """Load configuration from a dict or a JSON file."""
        try:
            payload = None
            status_path = None
            if isinstance(source, (str, os.PathLike)):
                status_path = str(source)
                payload, _changed, _backup_path = migrate_json_file_in_place(Path(source))
            elif source is None:
                payload = {}
            elif isinstance(source, dict):
                payload, _changed = sanitize_painter_config(dict(source))
            else:
                raise TypeError(f"Unsupported config payload: {type(source)}")
            if isinstance(payload, dict) and "meta" in payload and "payload" in payload:
                nested = payload.get("payload") or {}
                if isinstance(nested, dict) and "painter" in nested:
                    candidate = nested.get("painter")
                    payload = candidate if isinstance(candidate, dict) else nested
                else:
                    payload = nested
            if not isinstance(payload, dict):
                raise ValueError("Config payload must be a dict.")
            payload, _changed = sanitize_painter_config(payload)
            payload = self._prune_removed_viewport_fields(payload)
            # One-time migration to perceptual colour (mirrors engine.load_config):
            # old presets persisted the former "legacy_rgb" default (gamma sRGB,
            # muddy). Bump those to OKLab ONCE so the service cache AND the engine
            # agree; the marker (persisted via engine.get_config) lets a LATER
            # deliberate legacy choice stick.
            _space_value = payload["prep_color_space"] if "prep_color_space" in payload else _UNSET
            if not bool(payload.get("color_quality_migrated_v1", False)):
                if _space_value is _UNSET or str(_space_value or "").strip().lower() == "legacy_rgb":
                    _space_value = "oklab"
            self._set_prep_setting(
                "prep_color_space",
                _space_value,
                emit=False,
                invalidate=False,
            )
            self._set_prep_setting(
                "prep_quantization_mode",
                payload["prep_quantization_mode"] if "prep_quantization_mode" in payload else _UNSET,
                emit=False,
                invalidate=False,
            )
            self._set_prep_setting(
                "prep_cleanup_mode",
                payload["prep_cleanup_mode"] if "prep_cleanup_mode" in payload else _UNSET,
                emit=False,
                invalidate=False,
            )
            # Preserve the in-memory image across a reload when the payload omits the
            # image keys OR explicitly declares a clipboard placeholder. A clipboard image
            # exists only in RAM (no file on disk), so any payload that carries the
            # placeholder must NOT be allowed to null it out — re-inject the live pixels.
            payload_is_clipboard = bool(payload.get("source_is_clipboard_placeholder")) or \
                (str(payload.get("IMAGE_PATH", "") or "").strip() == tr("clipboard_source_name"))
            preserve_existing_image = (
                ("IMAGE_PATH" not in payload and "source_is_clipboard_placeholder" not in payload)
                or payload_is_clipboard
            )
            preserved_image = self._get_source_pil_for_session() if preserve_existing_image else None
            preserved_label = str(getattr(self.engine, "IMAGE_PATH", "") or "").strip()
            preserved_kind = self._session_image_kind
            preserved_original_path = self._session_image_original_path
            self.engine.load_config(payload)
            if preserve_existing_image and preserved_image is not None:
                restored_origin = preserved_label or (
                    tr("clipboard_source_name") if preserved_kind == "clipboard_cache" else "session_restore"
                )
                self._engine_set_image(preserved_image, origin=restored_origin)
                self._set_session_image_source(preserved_kind, preserved_original_path)
            self._sync_requested_stencil_state_from_engine()
            cfg = self.snapshot_painter_config()
            self.configLoaded.emit(cfg)
            state = self._emit_viewport_state()
            self._emit_app_layers_changed()
            self._emit_manual_palette_changed()
            self._emit_extra_actions_changed()
            self._emit_outline_fill_settings_changed()
            self._emit_dynamic_brush_settings_changed()
            self._invalidate_visuals(self._INVALIDATE_AREA, state=state)
            self._emit_session_state_changed("load-config", sections=("painter",))
            if self._stencil_enabled_requested:
                self.schedule_stencil_sync("load-config", self._preview_display_revision, image=self._last_qimg)
            else:
                self.schedule_stencil_sync("load-config-disabled", self._preview_display_revision, force_hide=True)
            if status_path:
                self.statusChanged.emit(f"info: конфигурация загружена: {os.path.basename(status_path)}")
            else:
                self.statusChanged.emit("info: конфигурация применена.")
        except FileNotFoundError:
            self.statusChanged.emit("error: config file not found.")
        except json.JSONDecodeError as exc:
            self.statusChanged.emit(f"error: invalid config json: {exc}")
        except Exception as e:
            self.statusChanged.emit(f"error: load_config failed: {e}")

    def save_config(self, path: str):
        try:
            cfg = self.snapshot_painter_config()
            from infrastructure.documents import write_document
            from pathlib import Path
            write_document(Path(path), cfg)
            self.statusChanged.emit(f"info: Сохранён пресет: {os.path.basename(path)}")
        except Exception as e:
            self.statusChanged.emit(f"error: save_config failed: {e}")

    def load_config_by_place(self, place: str):
        """Load config preset by human-friendly name (place). Searches common folders."""
        candidates = []
        try:
            paths = get_app_paths()
            base_dirs = [
                str(paths.app_root),
                str(paths.configs_root),
                str(paths.assets_root),
                os.path.dirname(os.path.abspath(__file__)),
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            ]
            for d in base_dirs:
                if not d:
                    continue
                cand = os.path.join(d, f"{place}.json")
                if os.path.isfile(cand):
                    candidates.append(cand)
            for sub in ("presets", "configs", "assets"):
                d = os.path.join(str(paths.app_root), sub)
                cand = os.path.join(d, f"{place}.json")
                if os.path.isfile(cand):
                    candidates.append(cand)
        except Exception:
            log.debug('ignored exception in paths = get_app_paths()', exc_info=True)
        for p in candidates:
            try:
                return self.load_config(p)
            except Exception:
                continue
        # No on-disk JSON preset for this place — that is NORMAL: the per-place mode is
        # applied in-memory by SettingsPage._apply_place_preset right after this call.
        # Keep it quiet (debug, not a user-facing status) so switching places no longer
        # spams a false "Preset not found" error.
        log.debug("No on-disk preset file for place '%s'; in-memory preset is used.", place)
        return None
