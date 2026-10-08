"""Place selection and per-place/algorithm profiles, without window dependencies."""
from __future__ import annotations

from copy import deepcopy
from PySide6.QtCore import QObject, Signal

from application.place_catalog import (
    PLACE_OPTIONS, PLACE_PRESETS, ALGORITHM_DISPLAY_KEYS, LEGACY_SCREEN_PLACE, recommended_profile,
    resolve_algorithm,
)
from ui.i18n import tr


# Legacy place documents contain these settings in addition to selection metadata.
PLACE_SETTING_KEYS = {
    "fast_min_region_area", "post_draw_repair_enabled", "post_draw_repair_passes",
    "post_draw_repair_mode", "post_draw_repair_sensitivity",
    "post_draw_repair_color_mismatch_sensitivity", "prep_color_space",
    "prep_quantization_mode", "prep_cleanup_mode", "draw_with_layers_enabled",
    "manual_mix_enabled", "manual_mix_alpha", "outline_fill_tool_mode",
    "outline_fill_brush_key", "outline_fill_fill_key", "outline_fill_brush_coord",
    "outline_fill_fill_coord",
}

METHOD_LABELS = {
    "hex_field": "settings_method_hex_label",
    "hsv_palette": "settings_method_hsv_label",
    "manual_palette": "settings_method_manual_label",
    "screen_palette": "settings_method_screen_label",
    "wheel_square": "settings_method_wheel_label",
}


class ProfileController(QObject):
    placeStateChanged = Signal(object)
    sessionProfilesChanged = Signal(object)

    def __init__(self, service):
        super().__init__(service)
        self.service = service
        # A neutral first place: a newcomer chooses a game in the quick start (PLACES-002).
        self._place_id = "universal"
        current = resolve_algorithm(service.snapshot_painter_config().get("drawing_algorithm", ""))
        preset = PLACE_PRESETS[self._place_id]
        self._algorithm = current if current in preset["algorithms"] else preset["default_algo"]
        self._profiles: dict[str, dict] = {}
        # Places the user has set up (chosen, or restored from a session). The start
        # place is not one of them: a newcomer's first choice still gets the
        # recommended settings, even when it is «Другая программа» itself.
        self._set_up: set[str] = set()
        self._transition = False
        service.configLoaded.connect(self._on_config_loaded)

    @property
    def place_id(self):
        return self._place_id

    @property
    def algorithm(self):
        return self._algorithm

    @staticmethod
    def profile_key(place_id, algorithm):
        return f"{place_id}::{resolve_algorithm(algorithm)}"

    def capture(self):
        if self._transition:
            return
        key = self.profile_key(self._place_id, self._algorithm)
        snapshot = self.service.export_profile_state()
        if snapshot != self._profiles.get(key):
            self._profiles[key] = deepcopy(snapshot)
            self.sessionProfilesChanged.emit(deepcopy(self._profiles))

    def export_session_profiles(self):
        # Include the active profile even if the user has never switched away.
        profiles = deepcopy(self._profiles)
        profiles[self.profile_key(self._place_id, self._algorithm)] = self.service.export_profile_state()
        return profiles

    def restore_session_profiles(self, profiles):
        restored = {}
        legacy = {}
        for key, value in (profiles.items() if isinstance(profiles, dict) else ()):
            if not isinstance(value, dict):
                continue
            normalized = str(key)
            if "::" in normalized:
                place, algorithm = normalized.split("::", 1)
                if place == LEGACY_SCREEN_PLACE:
                    # «Палитра рамкой» is a colour method of «Другая программа» now.
                    legacy[self.profile_key("universal", algorithm)] = deepcopy(value)
                    continue
                normalized = self.profile_key(place, algorithm)
            restored[normalized] = deepcopy(value)
        for key, value in legacy.items():
            restored.setdefault(key, value)
        self._profiles = restored
        self._set_up.update(key.split("::", 1)[0] for key in restored if "::" in key)
        self.sessionProfilesChanged.emit(deepcopy(restored))

    def is_set_up(self, place_id: str) -> bool:
        return place_id in self._set_up

    def available_place_options(self):
        return [{**deepcopy(item), "label": tr(item["key"])} for item in PLACE_OPTIONS]

    def export_place_state(self):
        index = next(i for i, item in enumerate(PLACE_OPTIONS) if item["id"] == self._place_id)
        option = PLACE_OPTIONS[index]
        preset = PLACE_PRESETS[self._place_id]
        config = self.service.snapshot_painter_config()
        method = config.get("color_picking_method") or preset["method_id"]
        result = {key: deepcopy(config[key]) for key in PLACE_SETTING_KEYS if key in config}
        result.update({
            "place_id": self._place_id, "place_file": option["file"], "place_key": option["key"],
            "place": tr(option["key"]), "place_text": tr(option["key"]), "place_index": index,
            "algo_code": self._algorithm, "algo_text": tr(ALGORITHM_DISPLAY_KEYS[self._algorithm]),
            "algo_index": preset["algorithms"].index(self._algorithm),
            "method_id": method, "method_text": tr(METHOD_LABELS.get(method, method)),
        })
        return result

    current_place_state = export_place_state

    def select_place_by_id(self, place_id: str) -> bool:
        if place_id not in PLACE_PRESETS:
            return False
        self.select(place_id)
        return True

    def select(self, place_id: str, algorithm: str | None = None):
        if place_id not in PLACE_PRESETS:
            raise ValueError("Unknown place: " + place_id)
        preset = PLACE_PRESETS[place_id]
        selected = resolve_algorithm(algorithm) if algorithm is not None else self._algorithm
        changed_place = place_id != self._place_id
        # The first choice of a place sets it up; reselecting the start place with
        # its route (the controller at launch) does not.
        setup = place_id not in self._set_up and (changed_place or algorithm is None)
        if algorithm is None and setup:
            selected = preset["default_algo"]       # a place starts with the route measured for it
        if selected not in preset["algorithms"]:
            if algorithm is not None:
                raise ValueError("Unsupported algorithm: " + selected)
            selected = preset["default_algo"]
        if (place_id, selected) == (self._place_id, self._algorithm) and not setup:
            self._apply_selection()
            return
        self.capture()
        self._place_id, self._algorithm = place_id, selected
        self._transition = True
        try:
            if changed_place:
                option = next(item for item in PLACE_OPTIONS if item["id"] == place_id)
                self.service.load_config_by_place(option["file"])
            profile = deepcopy(self._profiles.get(self.profile_key(place_id, selected), {}))
            if (setup or (not profile and changed_place)) and hasattr(self.service, "default_profile_state"):
                # first visit: start clean instead of inheriting the previous place,
                # from the settings recommended for every place (PRESET-BASE-001)
                profile = self.service.default_profile_state()
                profile.update(recommended_profile(place_id))
            profile.update(drawing_algorithm=selected, color_picking_method=preset["method_id"])
            if len(profile) > 2:
                self.service.apply_profile_state(profile)
            self._apply_selection()
        finally:
            self._transition = False
        self._set_up.add(place_id)
        self.placeStateChanged.emit(self.export_place_state())

    def _apply_selection(self, method=None):
        method = method or PLACE_PRESETS[self._place_id]["method_id"]
        # A timed place keeps the automatic colour count of new pictures in its round.
        self.service.auto_colors_limit = int(PLACE_PRESETS[self._place_id].get("auto_colors_limit", 0))
        config = self.service.snapshot_painter_config()
        for key, value in PLACE_PRESETS[self._place_id].get("settings", {}).items():
            if config.get(key) != value:
                getattr(self.service, "set_" + key)(value)
        brush = PLACE_PRESETS[self._place_id].get("brush")
        if brush:
            current = self.service.engine.get_dynamic_brush_settings()
            patch = {key: value for key, value in brush.items() if current.get(key) != value}
            if patch:
                self.service.update_brush_settings(patch)
        if config.get("color_picking_method") != method:
            self.service.set_color_picking_method(method)
        if resolve_algorithm(config.get("drawing_algorithm")) != self._algorithm:
            self.service.set_drawing_algorithm(self._algorithm)

    def apply_place_state(self, state):
        if not isinstance(state, dict):
            raise ValueError("место рисования записано в непонятном виде")
        place_id = self._resolve_legacy_place(state)
        legacy_screen = place_id == LEGACY_SCREEN_PLACE
        if legacy_screen:
            place_id = "universal"
        algorithms = PLACE_PRESETS[place_id]["algorithms"]
        selected = resolve_algorithm(state.get("algo_code") or state.get("algo_text"))
        if not selected:
            index = state.get("algo_index")
            selected = algorithms[index] if type(index) is int and 0 <= index < len(algorithms) else self._algorithm
        if selected not in algorithms:
            raise ValueError("маршрута «" + selected + "» нет у этого места")
        method = state.get("method_id") or ("screen_palette" if legacy_screen else PLACE_PRESETS[place_id]["method_id"])
        if method not in METHOD_LABELS:
            raise ValueError("неизвестный способ выбора цвета «" + str(method) + "»")
        self._transition = True
        try:
            # Restoring selection must not load a preset or overwrite restored painter/image data.
            self._place_id, self._algorithm = place_id, selected
            profile = {key: deepcopy(state[key]) for key in PLACE_SETTING_KEYS if key in state}
            profile.update(drawing_algorithm=selected, color_picking_method=method)
            self.service.apply_profile_state(profile)
            self._apply_selection(method)
        finally:
            self._transition = False
        self._set_up.add(place_id)
        self.placeStateChanged.emit(self.export_place_state())

    def _resolve_legacy_place(self, state):
        if state.get("place_id"):
            if state["place_id"] not in PLACE_PRESETS:
                raise ValueError("Unknown saved place: " + str(state["place_id"]))
            return state["place_id"]
        for field, option_field in (("place_key", "key"), ("place_file", "file")):
            if state.get(field):
                for item in PLACE_OPTIONS:
                    if item[option_field] == state[field]:
                        return item["id"]
        label = state.get("place") or state.get("place_text")
        if label:
            for item in PLACE_OPTIONS:
                if label in (item["file"], tr(item["key"])):
                    return item["id"]
        index = state.get("place_index")
        if type(index) is int and 0 <= index < len(PLACE_OPTIONS):
            return PLACE_OPTIONS[index]["id"]
        if any(state.get(key) for key in ("place_key", "place_file", "place", "place_text")):
            raise ValueError("Unknown saved place")
        return self._place_id

    def _on_config_loaded(self, config):
        if self._transition or not isinstance(config, dict):
            return
        algorithm = resolve_algorithm(config.get("drawing_algorithm"))
        if algorithm in PLACE_PRESETS[self._place_id]["algorithms"]:
            self._algorithm = algorithm
        self.placeStateChanged.emit(self.export_place_state())
