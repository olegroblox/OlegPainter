"""Drawing execution events; independent of Qt and human-facing status text."""
from dataclasses import dataclass
from enum import StrEnum


class DrawingPhase(StrEnum):
    IDLE = "idle"
    RUNNING = "started"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    FAILED = "failed"

    @property
    def terminal(self):
        return self in (self.STOPPED, self.COMPLETED, self.FAILED)


@dataclass(frozen=True, slots=True)
class DrawingProgress:
    """Values captured at the producer, never live engine references."""
    run_id: int
    captured_at: float
    done_pixels: int
    total_pixels: int
    elapsed_seconds: float
    colors_total: int
    colors_done: int
    current_color: str | None
    regions_done: int
    regions_total: int
    smart_eta: bool
    remaining_work: float = 0.0
    total_work: float = 0.0


@dataclass(frozen=True, slots=True)
class DrawingEvent:
    run_id: int
    phase: DrawingPhase
    error: str = ""
    progress: DrawingProgress | None = None
