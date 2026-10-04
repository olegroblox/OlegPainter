from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .registry import AiModelDescriptor, AiModelRegistry, ModelTask


AI_DISABLED_REASON = "deferred"
AI_DISABLED_MESSAGE = "AI features are disabled in the current dev build."


class AiFeatureDisabled(RuntimeError):
    pass


@dataclass(frozen=True)
class DisabledAiStatus:
    enabled: bool = False
    reason: str = AI_DISABLED_REASON
    message: str = AI_DISABLED_MESSAGE

    def to_dict(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "reason": self.reason,
            "message": self.message,
        }


class DisabledAiBackend:
    def __init__(self) -> None:
        self._status = DisabledAiStatus()
        self.registry: AiModelRegistry | None = None

    @property
    def status(self) -> dict[str, object]:
        return self._status.to_dict()

    def is_enabled(self) -> bool:
        return False

    def list_models(self, _task: str | ModelTask | None = None) -> Sequence[AiModelDescriptor]:
        return ()

    def provider_info(self) -> tuple[str, str]:
        return "disabled", describe_execution_provider("disabled")


def detect_directml_provider(*_args, **_kwargs) -> bool:
    return False


def resolve_execution_providers(*_args, **_kwargs) -> tuple[list[str], str]:
    return (["CPUExecutionProvider"], "disabled")


def describe_execution_provider(key: str) -> str:
    normalized = (key or "").lower()
    if normalized == "disabled":
        return "AI disabled"
    if not normalized or normalized == "missing":
        return "No onnxruntime providers available"
    if normalized == "gpu":
        return "DirectML (GPU)"
    if normalized == "cpu":
        return "CPU (fallback)"
    return normalized


def ai_deferred_message() -> str:
    return AI_DISABLED_MESSAGE
