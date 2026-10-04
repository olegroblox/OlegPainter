from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Iterable, List


@dataclass(frozen=True)
class HotkeyDefinition:
    code: str
    label: str
    scope: str  # "global" or "app"
    default: str
    description: str | None = None
    label_key: str | None = None
    description_key: str | None = None


HOTKEY_DEFINITIONS: List[HotkeyDefinition] = [
    HotkeyDefinition(
        code="select_area",
        label="Select area",
        scope="global",
        default="F1",
        description="Start area selection",
        label_key="action_select_area",
    ),
    HotkeyDefinition(
        code="toggle_stencil",
        label="Toggle stencil",
        scope="global",
        default="F2",
        description="Show or hide stencil overlay",
        label_key="action_toggle_stencil",
    ),
    HotkeyDefinition(
        code="edit_stencil",
        label="Edit stencil",
        scope="global",
        default="Alt+F2",
        description="Switch stencil overlay into interactive editor",
        label_key="action_edit_stencil",
    ),
    HotkeyDefinition(
        code="start_pause",
        label="Start or pause",
        scope="global",
        default="F3",
        description="Start drawing or pause/resume",
        label_key="action_start_pause",
    ),
    HotkeyDefinition(
        code="stop",
        label="Stop drawing",
        scope="global",
        default="F4",
        description="Abort current drawing",
        label_key="action_stop",
    ),
    HotkeyDefinition(
        code="capture_hex_palette",
        label="Capture HEX palette",
        scope="global",
        default="F5",
        description="Capture HEX palette coordinates",
        label_key="action_capture_hex_palette",
    ),
    HotkeyDefinition(
        code="define_manual_palette",
        label="Define manual palette",
        scope="global",
        default="F9",
        description="Capture manual palette colors",
        label_key="action_define_manual_palette",
    ),
    # Recording keys are F6/F7 since 2026-10-01 (HOTKEYS-002): the former "[" and "]"
    # are the Russian Х/Ъ keys and toggled the recording while typing in any program.
    HotkeyDefinition(
        code="record_pre_color_actions",
        label="Record actions before color",
        scope="global",
        default="F6",
        description="Capture pre-color actions",
        label_key="action_record_pre_color_actions",
    ),
    HotkeyDefinition(
        code="record_post_color_actions",
        label="Record actions after color",
        scope="global",
        default="F7",
        description="Capture post-color actions",
        label_key="action_record_post_color_actions",
    ),
    HotkeyDefinition(
        code="define_app_layers",
        label="Define app layers",
        scope="global",
        default="F8",
        description="Capture target app layer coordinates",
        label_key="action_define_app_layers",
    ),
    HotkeyDefinition(
        code="toggle_overlay",
        label="Toggle info overlay",
        scope="global",
        default="Ctrl+Shift+P",
        description="Show or hide the live drawing info overlay (HUD)",
        label_key="action_toggle_overlay",
    ),
    HotkeyDefinition(
        code="edit_overlay",
        label="Edit HUD layout",
        scope="global",
        default="Ctrl+Shift+E",
        description="Toggle the HUD layout editor (arrange / show-hide blocks)",
        label_key="action_edit_overlay",
    ),
    HotkeyDefinition(
        code="paste_clipboard",
        label="Paste image from clipboard",
        scope="app",
        default="Ctrl+V",
        description="Import image from clipboard",
        label_key="action_paste_clipboard",
    ),
    HotkeyDefinition(
        code="open_file",
        label="Open image",
        scope="app",
        default="Ctrl+O",
        description="Open image dialog",
        label_key="action_open_image",
    ),
    HotkeyDefinition(
        code="show_help",
        label="Show help",
        scope="app",
        default="Ctrl+/",
        description="Open help page",
        label_key="action_show_help",
    ),
]


_LEGACY_DEFAULTS = {"record_pre_color_actions": "[", "record_post_color_actions": "]"}


def migrate_legacy_hotkeys(mapping):
    """Saved profiles still hold the old "[" / "]" defaults: move them to the new
    ones unless that key is already taken by another action. A user who assigns
    "[" by hand afterwards keeps it: only stored data passes through here."""
    if not isinstance(mapping, dict) or not isinstance(mapping.get("global"), dict):
        return mapping
    result = {scope: dict(values) if isinstance(values, dict) else values for scope, values in mapping.items()}
    taken = {str(seq).casefold() for values in result.values() if isinstance(values, dict)
             for seq in values.values() if seq}
    for code, old in _LEGACY_DEFAULTS.items():
        new = definition_for_code(code).default
        if result["global"].get(code) == old and new.casefold() not in taken:
            result["global"][code] = new
            taken.add(new.casefold())
    return result


def definition_for_code(code: str) -> HotkeyDefinition:
    for item in HOTKEY_DEFINITIONS:
        if item.code == code:
            return item
    raise KeyError(code)


def definitions_by_scope(scope: str) -> List[HotkeyDefinition]:
    scope_lower = scope.lower()
    return [item for item in HOTKEY_DEFINITIONS if item.scope == scope_lower]


def default_hotkey_profile() -> Dict[str, str]:
    return {item.code: item.default for item in HOTKEY_DEFINITIONS}


def scopes() -> Iterable[str]:
    return {item.scope for item in HOTKEY_DEFINITIONS}
