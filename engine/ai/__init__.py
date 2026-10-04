"""AI contract for the app.

The legacy implementations stay in the package for a future rewrite, but the
active runtime currently exposes a disabled backend only.
"""

from .registry import AiModelRegistry, AiModelDescriptor, ModelTask
from .disabled import (
    AI_DISABLED_MESSAGE,
    AI_DISABLED_REASON,
    AiFeatureDisabled,
    DisabledAiBackend,
    ai_deferred_message,
    describe_execution_provider,
    detect_directml_provider,
    resolve_execution_providers,
)

__all__ = [
    "AiModelRegistry",
    "AiModelDescriptor",
    "ModelTask",
    "AiFeatureDisabled",
    "DisabledAiBackend",
    "AI_DISABLED_MESSAGE",
    "AI_DISABLED_REASON",
    "ai_deferred_message",
    "detect_directml_provider",
    "resolve_execution_providers",
    "describe_execution_provider",
]
