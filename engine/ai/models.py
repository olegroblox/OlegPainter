"""Catalog of optional AI models and their local installation (AI-001).

Nothing here is shipped with the app: the user picks models on the AI page and
the store downloads, verifies and removes them. Qt-free; progress and
cancellation go through plain callables so the same code runs in tests.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import threading
import urllib.request
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

CATALOG_PATH = Path(__file__).with_name("catalog.json")
TASKS = ("background", "segment", "depth", "lineart", "inpaint", "upscale")
_CHUNK = 1 << 20
_USER_AGENT = "OlegPainter-model-installer/1.0"


class InstallCancelled(RuntimeError):
    pass


class InstallError(RuntimeError):
    pass


@dataclass(frozen=True)
class ModelFile:
    path: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class ModelArchive:
    url: str
    size: int
    sha256: str
    extract_to: str
    check: tuple[str, ...]


@dataclass(frozen=True)
class ModelInfo:
    id: str
    task: str
    title: dict
    description: dict
    license_name: str
    license_url: str
    files: tuple[ModelFile, ...] = ()
    archive: Optional[ModelArchive] = None
    runtime: dict = field(default_factory=dict)
    recommended: bool = False

    @property
    def download_size(self) -> int:
        if self.archive is not None:
            return self.archive.size
        return sum(f.size for f in self.files)

    def text(self, field_name: str, language: str) -> str:
        values = getattr(self, field_name)
        return values.get(language) or values.get("en") or next(iter(values.values()), "")


def load_catalog(path: Path = CATALOG_PATH) -> dict[str, ModelInfo]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    models = {}
    for raw in data["models"]:
        if raw["task"] not in TASKS:
            raise ValueError(f"Unknown AI task {raw['task']!r} in {raw['id']}")
        archive = raw.get("archive")
        info = ModelInfo(
            id=raw["id"], task=raw["task"], title=raw["title"], description=raw["description"],
            license_name=raw["license"]["name"], license_url=raw["license"]["url"],
            files=tuple(ModelFile(f["path"], f["url"], int(f["size"]), f["sha256"].lower()) for f in raw.get("files", ())),
            archive=ModelArchive(archive["url"], int(archive["size"]), archive["sha256"].lower(),
                                 archive["extract_to"], tuple(archive["check"])) if archive else None,
            runtime=dict(raw.get("runtime") or {}), recommended=bool(raw.get("recommended")),
        )
        if bool(info.files) == bool(info.archive):
            raise ValueError(f"Model {info.id} must define either files or an archive")
        models[info.id] = info
    return models


def default_models_root() -> Path:
    from app_paths import get_app_paths
    return get_app_paths().models_root


def _sha256(path: Path, cancelled: Callable[[], bool] = lambda: False) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(_CHUNK), b""):
            if cancelled():
                raise InstallCancelled()
            digest.update(block)
    return digest.hexdigest()


class ModelStore:
    """Installed state of catalog models under one writable folder."""

    def __init__(self, root: Path | None = None, catalog: dict[str, ModelInfo] | None = None,
                 opener: Callable = urllib.request.urlopen):
        self.root = Path(root or default_models_root()).resolve()
        self.catalog = catalog if catalog is not None else load_catalog()
        self._open = opener
        self._lock = threading.Lock()
        self._busy: set[str] = set()

    # ----- state ----------------------------------------------------------
    def get(self, model_id: str) -> ModelInfo:
        try:
            return self.catalog[model_id]
        except KeyError:
            raise KeyError(f"Неизвестная модель: {model_id}") from None

    def path(self, model_id: str, relative: str | None = None) -> Path:
        info = self.get(model_id)
        if info.archive is not None:
            base = self._inside(info.archive.extract_to)
            return self._inside(info.archive.extract_to, relative) if relative else base
        if relative is None:
            return self._inside(info.files[0].path)
        # Other files of a multi-file model live next to its first file.
        return self._inside(str(Path(info.files[0].path).parent / relative))

    def is_installed(self, model_id: str) -> bool:
        """Cheap check (existence + size); the hash was verified at install."""
        info = self.get(model_id)
        if info.archive is not None:
            return all(self._inside(info.archive.extract_to, name).is_file() for name in info.archive.check)
        for item in info.files:
            target = self._inside(item.path)
            try:
                if target.stat().st_size != item.size:
                    return False
            except OSError:
                return False
        return True

    def installed_ids(self) -> list[str]:
        return [model_id for model_id in self.catalog if self.is_installed(model_id)]

    def is_busy(self, model_id: str) -> bool:
        with self._lock:
            return model_id in self._busy

    # ----- install / remove ----------------------------------------------
    def install(self, model_id: str, progress: Callable[[int, int], None] = lambda done, total: None,
                cancelled: Callable[[], bool] = lambda: False) -> None:
        """Download and verify every file; nothing replaces a target until its
        hash matches. Partial downloads resume; a cancelled install keeps them."""
        info = self.get(model_id)
        with self._lock:
            if model_id in self._busy:
                raise InstallError("Эта модель уже устанавливается.")
            self._busy.add(model_id)
        try:
            if info.archive is not None:
                self._install_archive(info, progress, cancelled)
            else:
                total, done = info.download_size, 0
                for item in info.files:
                    target = self._inside(item.path)
                    if target.is_file() and target.stat().st_size == item.size and _sha256(target, cancelled) == item.sha256:
                        done += item.size
                        progress(done, total)
                        continue
                    self._fetch(item.url, target, item.size, item.sha256,
                                lambda got, base=done: progress(base + got, total), cancelled)
                    done += item.size
        finally:
            with self._lock:
                self._busy.discard(model_id)

    def remove(self, model_id: str) -> None:
        info = self.get(model_id)
        if self.is_busy(model_id):
            raise InstallError("Дождитесь окончания установки или отмените её.")
        if info.archive is not None:
            shutil.rmtree(self._inside(info.archive.extract_to), ignore_errors=True)
            return
        for item in info.files:
            for candidate in (self._inside(item.path), self._inside(item.path + ".part")):
                try:
                    candidate.unlink()
                except FileNotFoundError:
                    pass

    # ----- internals --------------------------------------------------------
    def _inside(self, *parts: str) -> Path:
        path = self.root.joinpath(*parts).resolve()
        if path != self.root and self.root not in path.parents:
            raise InstallError(f"Путь модели выходит за папку моделей: {'/'.join(parts)}")
        return path

    def _fetch(self, url, target: Path, size: int, sha256: str, progress, cancelled) -> None:
        target.parent.mkdir(parents=True, exist_ok=True)
        part = target.with_name(target.name + ".part")
        have = part.stat().st_size if part.exists() else 0
        if have > size:
            part.unlink()
            have = 0
        if have < size:
            request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            if have:
                request.add_header("Range", f"bytes={have}-")
            with self._open(request, timeout=60) as response:
                if have and getattr(response, "status", 200) != 206:
                    have = 0  # the server ignored Range: start over
                with open(part, "ab" if have else "wb") as out:
                    while True:
                        if cancelled():
                            raise InstallCancelled()
                        block = response.read(_CHUNK)
                        if not block:
                            break
                        out.write(block)
                        have += len(block)
                        progress(min(have, size))
        if part.stat().st_size != size:
            raise InstallError(f"Файл скачан не полностью ({part.stat().st_size} из {size} байт). Повторите установку.")
        if _sha256(part, cancelled) != sha256:
            part.unlink()
            raise InstallError("Контрольная сумма не совпала: файл повреждён или подменён. Повторите установку.")
        os.replace(part, target)

    def _install_archive(self, info: ModelInfo, progress, cancelled) -> None:
        archive = info.archive
        download_dir = self._inside("_downloads")
        zip_path = download_dir / (info.id + ".zip")
        if not (zip_path.is_file() and zip_path.stat().st_size == archive.size
                and _sha256(zip_path, cancelled) == archive.sha256):
            self._fetch(archive.url, zip_path, archive.size, archive.sha256,
                        lambda got: progress(got, archive.size), cancelled)
        destination = self._inside(archive.extract_to)
        staging = destination.with_name(destination.name + ".extracting")
        shutil.rmtree(staging, ignore_errors=True)
        with zipfile.ZipFile(zip_path) as bundle:
            for member in bundle.infolist():
                target = (staging / member.filename).resolve()
                if target != staging.resolve() and staging.resolve() not in target.parents:
                    raise InstallError("Архив модели содержит небезопасные пути.")
            bundle.extractall(staging)
        missing = [name for name in archive.check if not (staging / name).is_file()]
        if missing:
            shutil.rmtree(staging, ignore_errors=True)
            raise InstallError("В архиве нет нужных файлов: " + ", ".join(missing))
        shutil.rmtree(destination, ignore_errors=True)
        os.replace(staging, destination)
        zip_path.unlink()
        progress(archive.size, archive.size)
