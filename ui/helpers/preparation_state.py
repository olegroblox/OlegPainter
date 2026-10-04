from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PreparationState:
    current_place_id: str = ""
    current_place_label: str = ""
    current_algo_code: str = ""
    current_algo_label: str = ""
    current_method_id: str = ""
    current_method_label: str = ""
    image_loaded: bool = False
    area_selected: bool = False
    area_stale: bool = False
    preview_ready: bool = False
    preview_busy: bool = False
    required_calibrations: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    missing_action_codes: tuple[str, ...] = ()
    preview_blocked_by: tuple[str, ...] = ()
    next_action_code: str = ""
    can_start: bool = False

    @property
    def next_requirement(self) -> str:
        return self.missing_requirements[0] if self.missing_requirements else ""


def build_preparation_state(service_snapshot: dict | None, place_state: dict | None) -> PreparationState:
    snapshot = dict(service_snapshot or {})
    place = dict(place_state or {})
    return PreparationState(
        current_place_id=str(place.get("place_id") or ""),
        current_place_label=str(place.get("place_text") or place.get("place") or ""),
        current_algo_code=str(place.get("algo_code") or ""),
        current_algo_label=str(place.get("algo_text") or place.get("algo") or ""),
        current_method_id=str(
            snapshot.get("active_method")
            or place.get("method_id")
            or ""
        ),
        current_method_label=str(place.get("method_text") or ""),
        image_loaded=bool(snapshot.get("image_loaded")),
        area_selected=bool(snapshot.get("area_selected")),
        area_stale=bool(snapshot.get("area_stale")),
        preview_ready=bool(snapshot.get("preview_ready")),
        preview_busy=bool(snapshot.get("preview_busy")),
        required_calibrations=tuple(str(item) for item in snapshot.get("required_calibrations") or ()),
        missing_requirements=tuple(str(item) for item in snapshot.get("missing_requirements") or ()),
        missing_action_codes=tuple(str(item) for item in snapshot.get("missing_action_codes") or ()),
        preview_blocked_by=tuple(str(item) for item in snapshot.get("preview_blocked_by") or ()),
        next_action_code=str(snapshot.get("next_action_code") or ""),
        can_start=bool(snapshot.get("can_start")),
    )
