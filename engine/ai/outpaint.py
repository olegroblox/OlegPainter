from __future__ import annotations

import logging

log = logging.getLogger("olegpainter.engine.ai.outpaint")
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Sequence, Tuple

import numpy as np
from PIL import Image

from .registry import AiModelDescriptor, AiModelRegistry
from .runtime import resolve_execution_providers, describe_execution_provider


@dataclass
class OutpaintConfig:
    model_id: str
    mode: str = "lama"
    prompt: str = ""
    negative_prompt: str = ""
    steps: int = 20
    cfg_scale: float = 7.5
    seed: Optional[int] = None
    prefer_gpu: bool = True


@dataclass
class OutpaintResult:
    image: Image.Image
    mask_used: Image.Image
    metadata: Dict[str, Any] = field(default_factory=dict)


class OutpaintController:
    """Outpainting orchestrator with LaMa ONNX support and SD placeholders."""

    def __init__(self, registry: AiModelRegistry, logger: logging.Logger | None = None) -> None:
        self.registry = registry
        self.logger = logger or logging.getLogger("OutpaintController")
        self._sessions: Dict[str, Any] = {}

    def outpaint(self, canvas: Image.Image, mask: Image.Image, config: OutpaintConfig) -> OutpaintResult:
        descriptor = self.registry.get(config.model_id)
        if descriptor is None:
            raise RuntimeError(f"Unknown outpaint model '{config.model_id}'")
        runtime = descriptor.runtime or {}
        runtime_type = (runtime.get("type") or config.mode or "lama").lower()
        if runtime_type in {"", "onnx"}:
            runtime_type = (runtime.get("mode") or config.mode or "lama").lower()
        if runtime_type in {"lama", "onnx-lama"}:
            return self._run_lama(canvas, mask, config, descriptor)
        if runtime_type in {"sd", "sd-inpaint", "onnx-sd-inpaint"}:
            raise RuntimeError("Stable Diffusion ONNX inpainting not yet implemented.")
        raise RuntimeError(f"Unsupported outpaint runtime type '{runtime_type}'")

    # ---------------------------
    # LaMa ONNX
    # ---------------------------
    def _run_lama(
        self,
        canvas: Image.Image,
        mask: Image.Image,
        config: OutpaintConfig,
        descriptor: AiModelDescriptor,
    ) -> OutpaintResult:
        session = self._ensure_session(descriptor, prefer_gpu=config.prefer_gpu)
        input_keys = [meta.name for meta in session.get_inputs()]
        if len(input_keys) < 2:
            raise RuntimeError("LaMa ONNX model must expose at least 2 inputs (image + mask)")

        # Preprocess
        img_tensor, pad_info = self._prepare_canvas(canvas, descriptor.runtime)
        mask_tensor = self._prepare_mask(mask, pad_info)

        feed = {}
        for name in input_keys:
            lname = name.lower()
            if "mask" in lname:
                feed[name] = mask_tensor
            else:
                feed[name] = img_tensor

        try:
            outputs = session.run(None, feed)
        except Exception as exc:
            self.logger.warning(
                "LaMa ONNX inference failed on provider %s: %s. Retrying on CPU.",
                describe_execution_provider(getattr(session, "_picked_provider", "unknown")),
                self._safe_error_text(exc),
            )
            self._sessions.pop(descriptor.id, None)
            cpu_session = self._ensure_session(descriptor, prefer_gpu=False, force_cpu=True)
            session = cpu_session
            outputs = cpu_session.run(None, feed)
        if not outputs:
            raise RuntimeError("LaMa inference returned no outputs")
        result = outputs[0]
        if result.ndim == 4:
            result = result[0]
        if result.shape[0] in (1, 3):
            result = np.transpose(result, (1, 2, 0))
        result = self._postprocess_canvas(result, pad_info, descriptor.runtime)

        result_img = Image.fromarray(result)
        provider_key = getattr(session, "_picked_provider", "cpu")
        metadata = {
            "model": descriptor.id,
            "provider": describe_execution_provider(provider_key),
            "provider_key": provider_key,
        }
        return OutpaintResult(image=result_img, mask_used=mask, metadata=metadata)

    def _ensure_session(
        self,
        descriptor: AiModelDescriptor,
        *,
        prefer_gpu: bool,
        force_cpu: bool = False,
    ):
        import onnxruntime as ort  # type: ignore

        cache_key = f"{descriptor.id}::cpu" if force_cpu else descriptor.id
        cached = self._sessions.get(cache_key)
        if cached:
            return cached
        model_path = descriptor.primary_path()
        if not model_path:
            raise FileNotFoundError(f"Outpaint model '{descriptor.id}' missing file path")
        if force_cpu:
            provider_lists = (["CPUExecutionProvider"],)
        else:
            providers, _ = resolve_execution_providers(prefer_gpu=prefer_gpu, logger=self.logger)
            provider_lists = (providers, ["CPUExecutionProvider"])

        session = None
        picked_key = "cpu"
        last_error: Exception | None = None
        for attempt_providers in provider_lists:
            if not attempt_providers:
                continue
            try:
                self.logger.info(
                    "Initializing LaMa ONNX session (%s) with providers: %s",
                    "CPU fallback" if attempt_providers == ["CPUExecutionProvider"] else "default",
                    ", ".join(attempt_providers),
                )
                session = ort.InferenceSession(str(model_path), providers=attempt_providers)
                first_provider = attempt_providers[0] if attempt_providers else "CPUExecutionProvider"
                picked_key = "gpu" if first_provider.lower().startswith("dmlexecutionprovider") else "cpu"
                setattr(session, "_picked_provider", picked_key)
                break
            except Exception as exc:
                last_error = exc
                self.logger.warning("Failed to initialize ONNX session with providers=%s: %s", attempt_providers, exc)
                session = None
        if session is None:
            raise last_error or RuntimeError("Failed to initialize ONNX session")
        self._sessions[cache_key] = session
        return session

    def _prepare_canvas(self, image: Image.Image, runtime_cfg: Dict[str, Any]):
        orig_w, orig_h = image.width, image.height
        resized = image
        resized_w, resized_h = orig_w, orig_h
        target = runtime_cfg.get("input_size")
        if isinstance(target, (list, tuple)) and len(target) >= 2:
            try:
                target_w = int(max(1, target[0]))
                target_h = int(max(1, target[1]))
                if target_w != orig_w or target_h != orig_h:
                    resized = image.resize((target_w, target_h), Image.LANCZOS)
                    resized_w, resized_h = target_w, target_h
            except Exception:
                log.debug('ignored exception in target_w = int(max(1, target[0]))', exc_info=True)

        np_img = np.array(resized.convert("RGB"), dtype=np.float32) / 255.0
        np_img, pad_tuple = self._pad_to_divisor(np_img, runtime_cfg)
        pad_h, pad_w, prepad_h, prepad_w = pad_tuple
        pad_info = {
            "pad_h": pad_h,
            "pad_w": pad_w,
            "prepad_h": prepad_h,
            "prepad_w": prepad_w,
            "resized_h": resized_h,
            "resized_w": resized_w,
            "orig_h": orig_h,
            "orig_w": orig_w,
        }
        np_img = self._normalize(np_img, runtime_cfg)
        np_img = np.transpose(np_img, (2, 0, 1))[None, :, :, :]
        return np_img.astype(np.float32), pad_info

    def _prepare_mask(self, mask: Image.Image, pad_info):
        resized_w = pad_info.get("resized_w")
        resized_h = pad_info.get("resized_h")
        mask_img = mask
        if resized_w and resized_h:
            try:
                mask_img = mask.resize((int(resized_w), int(resized_h)), Image.BILINEAR)
            except Exception:
                log.debug('ignored exception in mask_img = mask.resize((int(resized_w), int(resized_h)), Image.BILINEAR)', exc_info=True)
        np_mask = np.array(mask_img.convert("L"), dtype=np.float32) / 255.0
        pad_h = pad_info.get("pad_h", 0)
        pad_w = pad_info.get("pad_w", 0)
        if pad_h or pad_w:
            np_mask = np.pad(np_mask, ((0, pad_h), (0, pad_w)), mode="constant", constant_values=1.0)
        np_mask = np_mask[None, None, :, :]
        return np_mask.astype(np.float32)

    def _pad_to_divisor(self, array: np.ndarray, runtime_cfg: Dict[str, Any]):
        divisor = int(runtime_cfg.get("divisor") or 8)
        h, w = array.shape[:2]
        pad_h = (divisor - h % divisor) % divisor
        pad_w = (divisor - w % divisor) % divisor
        if pad_h or pad_w:
            array = np.pad(array, ((0, pad_h), (0, pad_w), (0, 0)), mode="reflect")
        return array, (pad_h, pad_w, h, w)

    def _normalize(self, array: np.ndarray, runtime_cfg: Dict[str, Any]):
        norm_cfg = runtime_cfg.get("normalization") or {}
        mean = norm_cfg.get("mean")
        std = norm_cfg.get("std")
        if mean is None or std is None:
            return (array * 2.0) - 1.0  # default to [-1,1]
        mean = np.array(mean, dtype=np.float32)
        std = np.array(std, dtype=np.float32)
        return (array - mean) / std

    def _postprocess_canvas(self, array: np.ndarray, pad_info, runtime_cfg: Dict[str, Any]):
        pad_h = pad_info.get("pad_h", 0)
        pad_w = pad_info.get("pad_w", 0)
        prepad_h = pad_info.get("prepad_h")
        prepad_w = pad_info.get("prepad_w")
        resized_h = pad_info.get("resized_h")
        resized_w = pad_info.get("resized_w")
        orig_h = pad_info.get("orig_h")
        orig_w = pad_info.get("orig_w")
        array = self._denormalize(array, runtime_cfg)
        array = np.clip(array, 0.0, 255.0).astype(np.uint8)
        if pad_h or pad_w:
            array = array[:prepad_h, :prepad_w, :]
        if (
            resized_h
            and resized_w
            and orig_h
            and orig_w
            and (int(resized_h) != int(orig_h) or int(resized_w) != int(orig_w))
        ):
            array = np.array(Image.fromarray(array).resize((int(orig_w), int(orig_h)), Image.BICUBIC), dtype=np.uint8)
        return array

    def _denormalize(self, array: np.ndarray, runtime_cfg: Dict[str, Any]):
        norm_cfg = runtime_cfg.get("normalization") or {}
        mean = norm_cfg.get("mean")
        std = norm_cfg.get("std")
        if mean is None or std is None:
            return (array + 1.0) * 127.5
        mean = np.array(mean, dtype=np.float32)
        std = np.array(std, dtype=np.float32)
        return (array * std) + mean

    @staticmethod
    def _safe_error_text(exc: Exception) -> str:
        try:
            return str(exc)
        except Exception:
            return repr(exc)
