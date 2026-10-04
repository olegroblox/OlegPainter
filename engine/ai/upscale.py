from __future__ import annotations

import logging
import subprocess
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from PIL import Image

from .registry import AiModelDescriptor, AiModelRegistry


@dataclass
class UpscaleRequest:
    model_id: str
    scale: Optional[int] = None
    tile_size: Optional[int] = None
    denoise: Optional[int] = None
    face_enhance: bool = False
    prefer_gpu: bool = True  # reserved for future onnx implementations


@dataclass
class UpscaleResult:
    image: Image.Image
    metadata: Dict[str, Any] = field(default_factory=dict)


class UpscaleProvider:
    """Bridge for NCNN/ONNX upscalers defined in manifest metadata."""

    def __init__(self, registry: AiModelRegistry, logger: logging.Logger | None = None) -> None:
        self.registry = registry
        self.logger = logger or logging.getLogger("UpscaleProvider")

    def upscale(self, source: Image.Image, request: UpscaleRequest) -> UpscaleResult:
        descriptor = self.registry.get(request.model_id)
        if descriptor is None:
            raise RuntimeError(f"Unknown upscale model '{request.model_id}'")
        runtime = descriptor.runtime or {}
        runtime_type = (runtime.get("type") or "ncnn").lower()
        if runtime_type == "ncnn":
            return self._run_ncnn(source, request, descriptor)
        raise RuntimeError(f"Unsupported upscale runtime type: {runtime_type}")

    # ---------------------------
    # NCNN executables
    # ---------------------------
    def _run_ncnn(self, source: Image.Image, request: UpscaleRequest, descriptor: AiModelDescriptor) -> UpscaleResult:
        runtime = descriptor.runtime or {}
        family = (runtime.get("family") or "realesrgan").lower()
        binary_path = runtime.get("binary")
        if not binary_path:
            raise RuntimeError(f"Manifest for '{descriptor.id}' missing runtime.binary")
        binary = self._resolve_binary_path(binary_path, descriptor_id=descriptor.id)
        model_name = runtime.get("model") or descriptor.extras.get("model")
        models_dir = runtime.get("models_dir")
        if models_dir:
            models_dir = self._resolve_models_dir(
                models_dir,
                family=family,
                binary=binary,
                descriptor_id=descriptor.id,
            )
        scale = request.scale or runtime.get("scale") or 4
        tile = request.tile_size or runtime.get("tile") or 0
        denoise = request.denoise if request.denoise is not None else runtime.get("denoise")
        if request.face_enhance:
            self.logger.warning("Face enhancement requested but not supported in NCNN backend yet.")

        with tempfile.TemporaryDirectory(prefix="upscale_") as tmpdir:
            tmp_dir = Path(tmpdir)
            input_path = tmp_dir / "input.png"
            output_path = tmp_dir / "output.png"
            source.save(input_path, format="PNG")

            args = self._build_ncnn_args(
                family=family,
                binary=binary,
                input_path=input_path,
                output_path=output_path,
                model_name=model_name,
                models_dir=models_dir,
                scale=scale,
                tile=tile,
                denoise=denoise,
                face_enhance=request.face_enhance,
            )
            self.logger.info("Running %s for upscale (%s)", family, descriptor.id)
            process = subprocess.run(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                text=True,
                encoding="utf-8",
            )
            if process.returncode != 0:
                raise RuntimeError(
                    f"Upscale process failed (exit {process.returncode}): {process.stdout.strip()}"
                )
            if not output_path.exists():
                raise RuntimeError("Upscale process did not produce output.")
            result_image = Image.open(output_path).convert("RGB")
            metadata = {
                "command": " ".join(str(part) for part in args),
                "stdout": process.stdout,
                "scale": scale,
                "tile": tile,
                "model": model_name,
                "family": family,
            }
            return UpscaleResult(image=result_image, metadata=metadata)

    # ---------------------------
    # Helpers for runtime layout quirks
    # ---------------------------
    def _resolve_binary_path(self, value: str | Path, *, descriptor_id: str) -> Path:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = (self.registry.models_root / candidate).resolve()
        if candidate.exists():
            return candidate
        fallback = self._search_runtime_candidate(
            candidate.parent,
            candidate.name,
            expect_file=True,
            allow_prefix=False,
        )
        if fallback:
            if self.logger:
                self.logger.debug(
                    "Resolved upscale binary via fallback for %s: %s",
                    descriptor_id,
                    fallback,
                )
            return fallback
        raise FileNotFoundError(f"Upscale binary not found: {candidate}")

    def _resolve_models_dir(
        self,
        value: str | Path,
        *,
        family: str,
        binary: Path,
        descriptor_id: str,
    ) -> Optional[Path]:
        candidate = Path(value)
        if not candidate.is_absolute():
            candidate = (self.registry.models_root / candidate).resolve()
        if candidate.exists():
            return candidate

        search_roots = []
        if candidate.parent and candidate.parent != candidate:
            search_roots.append(candidate.parent)
        search_roots.append(binary.parent)

        priority: Tuple[str, ...] = ()
        if family in {"realcugan", "realcugan-ncnn-vulkan"}:
            priority = ("models-se", "models-pro", "models-nose")

        for root in search_roots:
            resolved = self._pick_priority_dir(root, priority)
            if resolved:
                if self.logger:
                    self.logger.debug(
                        "Resolved models directory via priority for %s: %s",
                        descriptor_id,
                        resolved,
                    )
                return resolved

        for root in search_roots:
            resolved = self._search_runtime_candidate(
                root,
                candidate.name,
                expect_file=False,
                allow_prefix=True,
            )
            if resolved:
                if self.logger:
                    self.logger.debug(
                        "Resolved models directory via fallback for %s: %s",
                        descriptor_id,
                        resolved,
                    )
                return resolved

        if self.logger:
            self.logger.warning(
                "Models directory not found for %s (expected %s)",
                descriptor_id,
                candidate,
            )
        return None

    def _pick_priority_dir(self, root: Path, names: Tuple[str, ...]) -> Optional[Path]:
        if not names or not root.exists() or not root.is_dir():
            return None
        for name in names:
            candidate = root / name
            if candidate.is_dir():
                return candidate
        return None

    def _search_runtime_candidate(
        self,
        root: Path,
        target_name: str,
        *,
        expect_file: bool,
        allow_prefix: bool,
        max_depth: int = 2,
    ) -> Optional[Path]:
        if not root.exists() or not root.is_dir():
            return None
        target_lower = target_name.lower()
        stack = [(root, 0)]
        visited = set()

        while stack:
            current, depth = stack.pop()
            if current in visited:
                continue
            visited.add(current)
            if depth > max_depth:
                continue
            try:
                entries = list(current.iterdir())
            except Exception:
                continue
            for entry in entries:
                name_lower = entry.name.lower()
                matches = name_lower == target_lower or (allow_prefix and name_lower.startswith(target_lower))
                if matches:
                    if expect_file and entry.is_file():
                        return entry
                    if not expect_file and entry.is_dir():
                        return entry
                if entry.is_dir() and depth < max_depth:
                    stack.append((entry, depth + 1))
        return None

    def _build_ncnn_args(
        self,
        *,
        family: str,
        binary: Path,
        input_path: Path,
        output_path: Path,
        model_name: Optional[str],
        models_dir: Optional[Path],
        scale: int,
        tile: int,
        denoise: Optional[int],
        face_enhance: bool,
    ) -> Tuple[str, ...]:
        args = [str(binary), "-i", str(input_path), "-o", str(output_path)]
        if family in {"realesrgan", "realesrgan-ncnn-vulkan"}:
            if model_name:
                args.extend(["-n", model_name])
            if models_dir:
                args.extend(["-m", str(models_dir)])
            if scale:
                args.extend(["-s", str(scale)])
            if tile:
                args.extend(["-t", str(tile)])
        elif family in {"waifu2x", "waifu2x-ncnn-vulkan"}:
            if scale:
                args.extend(["-s", str(scale)])
            if denoise is not None:
                args.extend(["-n", str(denoise)])
            if tile:
                args.extend(["-t", str(tile)])
            if models_dir:
                args.extend(["-m", str(models_dir)])
        elif family in {"realsr", "realsr-ncnn-vulkan"}:
            if model_name:
                args.extend(["-n", model_name])
            if models_dir:
                args.extend(["-m", str(models_dir)])
            if scale:
                args.extend(["-s", str(scale)])
            if tile:
                args.extend(["-t", str(tile)])
        elif family in {"realcugan", "realcugan-ncnn-vulkan"}:
            if model_name:
                args.extend(["-n", model_name])
            if models_dir:
                args.extend(["-m", str(models_dir)])
            if scale:
                args.extend(["-s", str(scale)])
            if denoise is not None:
                args.extend(["-d", str(denoise)])
            if tile:
                args.extend(["-t", str(tile)])
        else:
            raise RuntimeError(f"Unsupported NCNN family '{family}'")
        return tuple(args)
