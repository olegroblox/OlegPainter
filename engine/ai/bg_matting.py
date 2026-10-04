from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Optional, Sequence, Tuple

import numpy as np

try:  # pragma: no cover - cv2 heavy to import in tests
    import cv2  # type: ignore
except Exception:  # pragma: no cover
    cv2 = None  # type: ignore

from .registry import AiModelDescriptor, AiModelRegistry
from .runtime import resolve_execution_providers, describe_execution_provider


@dataclass
class BgMattingConfig:
    model_id: str
    threshold: float = 0.5
    softness: int = 0  # in pixels
    prefer_gpu: bool = True


class OnnxBgMatting:
    """Background matting helper for U2Net/ISNet/MODNet style networks."""

    def __init__(self, registry: AiModelRegistry, logger: logging.Logger | None = None) -> None:
        self.registry = registry
        self.logger = logger or logging.getLogger("OnnxBgMatting")
        self._descriptor: Optional[AiModelDescriptor] = None
        self._session = None
        self._input_name: Optional[str] = None
        self._output_name: Optional[str] = None
        self._input_size: Tuple[int, int] | None = None
        self._normalization: dict[str, Any] = {"mean": None, "std": None}
        self._provider_summary: str | None = None

    # ---------------------------
    # Configuration
    # ---------------------------
    def ensure_model(self, config: BgMattingConfig) -> AiModelDescriptor:
        descriptor = self.registry.get(config.model_id)
        if descriptor is None:
            raise RuntimeError(f"Unknown AI background model '{config.model_id}'")
        if not descriptor.primary_path() or not descriptor.primary_path().exists():
            raise FileNotFoundError(f"Model '{descriptor.id}' not installed")
        if self._descriptor and self._descriptor.id == descriptor.id and self._session is not None:
            return descriptor
        self._load_session(descriptor, prefer_gpu=config.prefer_gpu)
        return descriptor

    def _load_session(self, descriptor: AiModelDescriptor, *, prefer_gpu: bool) -> None:
        import onnxruntime as ort  # type: ignore

        model_path = descriptor.primary_path()
        if not model_path:
            raise FileNotFoundError(f"Model '{descriptor.id}' missing primary path.")
        providers, picked = resolve_execution_providers(prefer_gpu=prefer_gpu, logger=self.logger)
        self.logger.info("Loading background model %s (%s)", descriptor.id, describe_execution_provider(picked))
        session = ort.InferenceSession(str(model_path), providers=providers)
        input_meta = session.get_inputs()[0]
        output_meta = session.get_outputs()[0]
        self._input_name = input_meta.name
        self._output_name = output_meta.name
        runtime_cfg = descriptor.runtime or {}
        # Input size, best source first: the ONNX's own FIXED H/W -> the
        # manifest's runtime.input_size (for exports with a DYNAMIC input, e.g.
        # BiRefNet, where the meta gives no size) -> 320. Reading runtime only on
        # dynamic shapes keeps fixed-size models (u2net 320, etc.) untouched.
        self._input_size = (self._extract_input_size(input_meta.shape)
                            or self._runtime_input_size(runtime_cfg)
                            or (320, 320))
        normalization = runtime_cfg.get("normalization") or {}
        mean = normalization.get("mean")
        std = normalization.get("std")
        if mean is not None:
            self._normalization["mean"] = np.array(mean, dtype=np.float32)
        else:
            self._normalization["mean"] = None
        if std is not None:
            self._normalization["std"] = np.array(std, dtype=np.float32)
        else:
            self._normalization["std"] = None
        self._session = session
        self._descriptor = descriptor
        self._provider_summary = picked

    @staticmethod
    def _extract_input_size(shape: Sequence[Any]) -> Optional[Tuple[int, int]]:
        """Fixed (w, h) from the ONNX input meta, or None when the H/W dims are
        dynamic/symbolic (the caller then falls back to the manifest size)."""
        if len(shape) < 4:
            return None
        h, w = shape[-2], shape[-1]
        if isinstance(h, int) and h > 0 and isinstance(w, int) and w > 0:
            return int(w), int(h)
        return None

    @staticmethod
    def _runtime_input_size(runtime_cfg: dict) -> Optional[Tuple[int, int]]:
        size = runtime_cfg.get("input_size")
        if isinstance(size, (list, tuple)) and len(size) == 2:
            try:
                h, w = int(size[0]), int(size[1])
            except (TypeError, ValueError):
                return None
            if h > 0 and w > 0:
                return (w, h)
        return None

    # ---------------------------
    # Inference
    # ---------------------------
    def compute_mask(self, image, config: BgMattingConfig) -> np.ndarray:
        descriptor = self.ensure_model(config)
        if self._session is None or self._input_name is None or self._output_name is None:
            raise RuntimeError("Background model not initialised")

        input_tensor, original_size = self._prepare_input(image)
        outputs = self._session.run([self._output_name], {self._input_name: input_tensor})
        mask = outputs[0]
        if mask.ndim == 4:
            mask = mask[0, 0]
        elif mask.ndim == 3:
            mask = mask[0]
        mask = np.asarray(mask, dtype=np.float32)
        mask = self._resize_mask(mask, original_size)
        mask = np.clip(mask, 0.0, 1.0)
        if config.softness and cv2 is not None:
            ksize = int(max(3, config.softness * 2 + 1))
            if ksize % 2 == 0:
                ksize += 1
            mask = cv2.GaussianBlur(mask, (ksize, ksize), 0)
        return mask

    def _prepare_input(self, image) -> Tuple[np.ndarray, Tuple[int, int]]:
        from PIL import Image  # lazy import

        if not isinstance(image, Image.Image):
            raise TypeError("Expected PIL.Image for background matting input")
        rgb = image.convert("RGB")
        orig_size = rgb.size
        w, h = self._input_size or (320, 320)
        if rgb.size != (w, h):
            rgb = rgb.resize((w, h))
        arr = np.asarray(rgb, dtype=np.float32) / 255.0
        arr = arr.transpose(2, 0, 1)  # HWC -> CHW
        if self._normalization["mean"] is not None and self._normalization["std"] is not None:
            mean = self._normalization["mean"][:, None, None]
            std = self._normalization["std"][:, None, None]
            arr = (arr - mean) / std
        input_tensor = arr[None, :, :, :]
        return input_tensor, orig_size

    def _resize_mask(self, mask: np.ndarray, size: Tuple[int, int]) -> np.ndarray:
        if mask.shape[::-1] == size:
            return mask
        if cv2 is not None:
            return cv2.resize(mask, size, interpolation=cv2.INTER_LINEAR)
        from PIL import Image

        pil_mask = Image.fromarray(np.uint8(mask * 255), mode="L")
        pil_mask = pil_mask.resize(size, Image.BILINEAR)
        return np.asarray(pil_mask, dtype=np.float32) / 255.0

    @property
    def provider_summary(self) -> Optional[str]:
        return self._provider_summary

