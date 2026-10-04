from __future__ import annotations

from infrastructure.documents import read_document, write_document, atomic_bytes
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


CONFIG_SCHEMA_VERSION = 3

_AI_TOP_LEVEL_KEYS = {
    "ai",
    "use_ai_background",
    "use_ai_upscale",
    "use_ai_outpaint",
}
_AI_PREFIXES = ("ai_", "bg_ai_")


def sanitize_painter_config(config: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if not isinstance(config, dict):
        return {}, False

    cleaned = deepcopy(config)
    changed = False
    for key in list(cleaned.keys()):
        if key in _AI_TOP_LEVEL_KEYS or key.startswith(_AI_PREFIXES):
            cleaned.pop(key, None)
            changed = True

    legacy_enabled = cleaned.get("background_removal_enabled")
    if legacy_enabled is None and "remove_background" in cleaned:
        cleaned["background_removal_enabled"] = bool(cleaned.get("remove_background"))
        changed = True
    if "background_color_tolerance" not in cleaned and "bg_tolerance" in cleaned:
        cleaned["background_color_tolerance"] = cleaned.get("bg_tolerance")
        changed = True
    if "background_alpha_threshold" not in cleaned and "alpha_threshold" in cleaned:
        cleaned["background_alpha_threshold"] = cleaned.get("alpha_threshold")
        changed = True
    if "background_removal_mode" not in cleaned:
        if bool(config.get("use_ai_background", False)):
            cleaned["background_removal_mode"] = "corner"
            cleaned["background_removal_enabled"] = True
            changed = True
        elif bool(cleaned.get("background_removal_enabled", False)):
            cleaned["background_removal_mode"] = "corner"
            changed = True

    if cleaned.get("remove_background") is None:
        cleaned.pop("remove_background", None)

    algorithm = str(cleaned.get("drawing_algorithm") or "").strip().lower()
    if algorithm == "dfs_4dir_dynamic":
        cleaned["drawing_algorithm"] = "dfs_4dir"
        cleaned["dynamic_brush_enabled"] = True
        changed = True

    return cleaned, changed


def sanitize_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if not isinstance(payload, dict):
        return {}, False

    cleaned = deepcopy(payload)
    changed = False
    painter_section = cleaned.get("painter")
    if isinstance(painter_section, dict):
        sanitized_painter, painter_changed = sanitize_painter_config(painter_section)
        cleaned["painter"] = sanitized_painter
        changed = changed or painter_changed
    elif not any(key in cleaned for key in ("hotkeys", "place")):
        cleaned, changed = sanitize_painter_config(cleaned)

    session_section = cleaned.get("session")
    if isinstance(session_section, dict):
        sanitized_session = deepcopy(session_section)
        profiles = sanitized_session.get("profiles")
        if isinstance(profiles, dict):
            sanitized_profiles: dict[str, Any] = {}
            profiles_changed = False
            for profile_key, profile_value in profiles.items():
                if isinstance(profile_value, dict):
                    sanitized_profile, profile_changed = sanitize_painter_config(profile_value)
                    sanitized_profiles[str(profile_key)] = sanitized_profile
                    profiles_changed = profiles_changed or profile_changed
                else:
                    sanitized_profiles[str(profile_key)] = profile_value
            sanitized_session["profiles"] = sanitized_profiles
            if profiles_changed or sanitized_profiles != profiles:
                changed = True
        if sanitized_session != session_section:
            cleaned["session"] = sanitized_session
            changed = True

    return cleaned, changed


def sanitize_config_document(document: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    if not isinstance(document, dict):
        return {}, False

    cleaned = deepcopy(document)
    changed = False

    if "meta" in cleaned or "payload" in cleaned:
        payload = cleaned.get("payload")
        if isinstance(payload, dict):
            payload, payload_changed = sanitize_payload(payload)
            cleaned["payload"] = payload
            changed = changed or payload_changed
        meta = cleaned.get("meta")
        if isinstance(meta, dict):
            current_version = meta.get("version")
            # Only upgrade older/unknown schemas. A config written by a NEWER
            # build (higher version) must not be silently downgraded to ours.
            needs_upgrade = current_version is None or (
                isinstance(current_version, int) and current_version < CONFIG_SCHEMA_VERSION
            )
            if needs_upgrade:
                meta = deepcopy(meta)
                meta["version"] = CONFIG_SCHEMA_VERSION
                cleaned["meta"] = meta
                changed = True
        return cleaned, changed

    return sanitize_painter_config(cleaned)


def migrate_json_file_in_place(path: Path) -> tuple[Any, bool, Path | None]:
    source_path = Path(path)
    data = read_document(source_path)
    raw_bytes = source_path.read_bytes()
    if not isinstance(data, dict):
        return data, False, None

    cleaned, changed = sanitize_config_document(data)
    if not changed:
        return cleaned, False, None

    backup_path = _backup_path_for(source_path)
    if not backup_path.exists():
        atomic_bytes(backup_path, raw_bytes)
    write_document(source_path, cleaned)
    return cleaned, True, backup_path


def _backup_path_for(path: Path) -> Path:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return path.with_name(f"{path.stem}.pre_ai_cleanup_{timestamp}{path.suffix}.bak")
