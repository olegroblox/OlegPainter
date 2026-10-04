from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

from app_paths import get_app_paths


class ModelTask(str, Enum):
    """Logical AI task handled inside the application."""

    BACKGROUND = "background"
    UPSCALE = "upscale"
    OUTPAINT = "outpaint"

    @classmethod
    def from_value(cls, value: str) -> "ModelTask":
        try:
            return cls(value)
        except Exception as exc:  # pragma: no cover - defensive
            raise ValueError(f"Unsupported model task: {value!r}") from exc


@dataclass(frozen=True)
class ModelFile:
    path: Path
    url: Optional[str] = None
    sha256: Optional[str] = None
    md5: Optional[str] = None
    size: Optional[int] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any], *, base_dir: Path) -> "ModelFile":
        path_val = data.get("path") or data.get("dest") or data.get("destination")
        if not path_val:
            raise ValueError("Manifest file entry must include 'path'")
        path = Path(path_val)
        if not path.is_absolute():
            path = (base_dir / path).resolve()
        return cls(
            path=path,
            url=data.get("url"),
            sha256=data.get("sha256"),
            md5=data.get("md5"),
            size=data.get("size"),
        )


@dataclass
class AiModelDescriptor:
    """In-memory representation of a manifest entry."""

    id: str
    task: ModelTask
    provider: str
    title: Dict[str, str]
    description: Dict[str, str]
    install_type: str
    runtime: Dict[str, Any]
    license_text: Optional[str] = None
    license_url: Optional[str] = None
    tags: Sequence[str] = field(default_factory=tuple)
    extras: Dict[str, Any] = field(default_factory=dict)
    _files: List[ModelFile] = field(default_factory=list)
    _archive_url: Optional[str] = None
    _archive_sha256: Optional[str] = None
    _archive_size: Optional[int] = None
    _archive_extract_to: Optional[Path] = None

    def __post_init__(self) -> None:
        self.install_type = (self.install_type or "bundle").lower()

    @property
    def files(self) -> Sequence[ModelFile]:
        return tuple(self._files)

    @property
    def archive_url(self) -> Optional[str]:
        return self._archive_url

    @property
    def archive_extract_to(self) -> Optional[Path]:
        return self._archive_extract_to

    @property
    def archive_sha256(self) -> Optional[str]:
        return self._archive_sha256

    @property
    def archive_size(self) -> Optional[int]:
        return self._archive_size

    def localized_title(self, lang: str) -> str:
        lang = (lang or "en").lower()
        if lang in self.title:
            return self.title[lang]
        return self.title.get("en") or next(iter(self.title.values()), self.id)

    def localized_description(self, lang: str) -> str:
        lang = (lang or "en").lower()
        if lang in self.description:
            return self.description[lang]
        return self.description.get("en") or ""

    def is_bundled(self) -> bool:
        return self.install_type == "bundle"

    def is_downloadable(self) -> bool:
        return self.install_type in {"download", "optional"}

    def is_installed(self) -> bool:
        if self.files:
            for mf in self.files:
                if not mf.path.exists():
                    return False
        if self.archive_extract_to:
            return self.archive_extract_to.exists()
        return True

    def primary_path(self) -> Optional[Path]:
        if self.files:
            return self.files[0].path
        return self.archive_extract_to

    def iter_missing_paths(self) -> Iterator[Path]:
        for mf in self.files:
            if not mf.path.exists():
                yield mf.path
        if self.archive_extract_to and not self.archive_extract_to.exists():
            yield self.archive_extract_to

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "task": self.task.value,
            "provider": self.provider,
            "install_type": self.install_type,
            "files": [mf.path.as_posix() for mf in self.files],
            "runtime": self.runtime,
        }


class AiModelRegistry:
    """Central registry for AI model metadata and installation state."""

    def __init__(
        self,
        models_root: Path | str | None = None,
        manifest_path: Path | str | None = None,
        logger: Optional[logging.Logger] = None,
    ) -> None:
        default_models_root = get_app_paths().models_root
        self.models_root = Path(models_root or default_models_root).resolve()
        self.manifest_path = (
            Path(manifest_path).resolve()
            if manifest_path is not None
            else (self.models_root / "manifest.json").resolve()
        )
        self.logger = logger or logging.getLogger("AiModelRegistry")
        self.logger.debug("AI registry manifest path: %s", self.manifest_path)
        self.models_root.mkdir(parents=True, exist_ok=True)
        self._models: Dict[str, AiModelDescriptor] = {}
        self._manifest_mtime: Optional[float] = None
        self._load_manifest()

    # ---------------------------
    # Manifest handling
    # ---------------------------
    def _load_manifest(self) -> None:
        if not self.manifest_path.exists():
            self.logger.warning("AI manifest not found at %s", self.manifest_path)
            self._models.clear()
            return

        try:
            data = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        except Exception as exc:
            self.logger.error("Failed to parse AI manifest: %s", exc)
            self._models.clear()
            return

        if not isinstance(data, dict) or "models" not in data:
            self.logger.error("Manifest format invalid: expected object with 'models'")
            self._models.clear()
            return

        models_raw = data.get("models", [])
        entries: Dict[str, AiModelDescriptor] = {}
        for entry in models_raw:
            try:
                descriptor = self._parse_entry(entry)
            except Exception as exc:
                self.logger.error("Skipping manifest entry due to error: %s", exc)
                continue
            entries[descriptor.id] = descriptor
        self._models = entries
        try:
            self._manifest_mtime = self.manifest_path.stat().st_mtime
        except Exception:
            self._manifest_mtime = None
        self.logger.debug("Loaded %d AI manifest entries", len(self._models))

    def reload_if_needed(self) -> None:
        try:
            current_mtime = self.manifest_path.stat().st_mtime
        except Exception:
            current_mtime = None
        if current_mtime and self._manifest_mtime and current_mtime <= self._manifest_mtime:
            return
        if current_mtime and self._manifest_mtime and abs(current_mtime - self._manifest_mtime) < 1e-3:
            return
        self._load_manifest()

    def _parse_entry(self, entry: Dict[str, Any]) -> AiModelDescriptor:
        if not isinstance(entry, dict):
            raise ValueError("Manifest entry must be an object")
        model_id = entry.get("id")
        if not model_id:
            raise ValueError("Manifest entry missing id")
        task_raw = entry.get("task")
        if not task_raw:
            raise ValueError(f"Manifest entry '{model_id}' missing task")
        task = ModelTask.from_value(str(task_raw))
        provider = entry.get("provider") or "unknown"
        title = entry.get("title") or {}
        description = entry.get("description") or {}
        install = entry.get("install") or {}
        runtime = entry.get("runtime") or {}
        tags = tuple(entry.get("tags") or [])
        extras = dict(entry.get("extras") or {})
        license_info = entry.get("license") or {}
        install_type = install.get("type") or entry.get("install_type") or "bundle"

        files_data = install.get("files") or []
        archive_url = install.get("archive_url")
        extract_to = install.get("extract_to")
        archive_sha256 = install.get("archive_sha256") or install.get("sha256")
        archive_size = install.get("archive_size") or install.get("size")

        files: List[ModelFile] = []
        for file_entry in files_data:
            files.append(ModelFile.from_dict(file_entry, base_dir=self.models_root))

        extract_path: Optional[Path] = None
        if extract_to:
            extract_path = self._resolve_relative_path(extract_to)

        descriptor = AiModelDescriptor(
            id=str(model_id),
            task=task,
            provider=str(provider),
            title={str(k).lower(): str(v) for k, v in title.items()},
            description={str(k).lower(): str(v) for k, v in description.items()},
            install_type=str(install_type).lower(),
            runtime=runtime,
            tags=tags,
            extras=extras,
            license_text=license_info.get("text"),
            license_url=license_info.get("url"),
            _files=files,
            _archive_url=archive_url,
            _archive_sha256=archive_sha256,
            _archive_size=archive_size,
            _archive_extract_to=extract_path,
        )
        return descriptor

    def _resolve_relative_path(self, value: str) -> Path:
        path = Path(value)
        if not path.is_absolute():
            path = (self.models_root / path).resolve()
        return path

    # ---------------------------
    # Public lookup helpers
    # ---------------------------
    def all_models(self) -> Sequence[AiModelDescriptor]:
        self.reload_if_needed()
        return tuple(self._models.values())

    def models_for_task(self, task: ModelTask | str) -> Sequence[AiModelDescriptor]:
        if isinstance(task, str):
            task = ModelTask.from_value(task)
        return tuple(m for m in self.all_models() if m.task == task)

    def get(self, model_id: str) -> Optional[AiModelDescriptor]:
        self.reload_if_needed()
        return self._models.get(model_id)

    def list_installed(self, task: ModelTask | str | None = None) -> Sequence[AiModelDescriptor]:
        candidates = self.models_for_task(task) if task else self.all_models()
        return tuple(model for model in candidates if model.is_installed())

    def list_available(self, task: ModelTask | str | None = None) -> Sequence[AiModelDescriptor]:
        candidates = self.models_for_task(task) if task else self.all_models()
        return tuple(candidates)

    def get_path(self, model: AiModelDescriptor | str) -> Optional[Path]:
        descriptor = model if isinstance(model, AiModelDescriptor) else self.get(model)
        if descriptor is None:
            return None
        return descriptor.primary_path()

    # ---------------------------
    # Installation helpers
    # ---------------------------
    def mark_bundle_installed(self, descriptor: AiModelDescriptor) -> None:
        """Ensure directories for bundled assets exist."""
        for mf in descriptor.files:
            mf.path.parent.mkdir(parents=True, exist_ok=True)
        if descriptor.archive_extract_to:
            descriptor.archive_extract_to.mkdir(parents=True, exist_ok=True)

    def iter_download_descriptors(self) -> Iterator[AiModelDescriptor]:
        for desc in self.all_models():
            if desc.is_downloadable():
                yield desc

    # The actual download orchestration lives in ui DownloadWorker to keep Qt affinity.
    # Registry keeps helper for direct file lists.
    def files_to_download(self, descriptor: AiModelDescriptor) -> Sequence[ModelFile]:
        return tuple(mf for mf in descriptor.files if mf.url)
