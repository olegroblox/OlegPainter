"""Immutable application state consumed by every presentation."""
from dataclasses import dataclass

from engine.olegpainter.drawing_events import DrawingPhase
from ui.helpers.preparation_state import PreparationState


@dataclass(frozen=True, slots=True)
class CaptureState:
    active: bool = False
    kind: str = ""
    count: int = 0
    maximum: int = 0
    slot: str = ""

    @classmethod
    def from_payload(cls, payload):
        return cls(bool(payload.get("active")), str(payload.get("kind") or ""),
                   int(payload.get("count") or 0), int(payload.get("max") or 0),
                   str(payload.get("slot") or ""))

    def to_payload(self):
        return {"active": self.active, "kind": self.kind, "count": self.count,
                "max": self.maximum, "slot": self.slot}


@dataclass(frozen=True, slots=True)
class ApplicationState:
    preparation: PreparationState
    drawing: DrawingPhase
    run_id: int
    capture: CaptureState
    desktop_mode: str = ""
    hotkey_capture: bool = False
    closing: bool = False
    brush_learning: bool = False

    @property
    def drawing_busy(self):
        return self.drawing in (DrawingPhase.RUNNING, DrawingPhase.PAUSED, DrawingPhase.STOPPING)

    @property
    def can_start(self):
        return (not self.closing and not self.brush_learning and self.preparation.can_start and not self.drawing_busy
                and not self.capture.active and not self.desktop_mode and not self.hotkey_capture)

    @property
    def can_edit_source(self):
        return not self.closing and not self.brush_learning and not self.drawing_busy and not self.capture.active and not self.desktop_mode and not self.hotkey_capture
