from __future__ import annotations

import json
import os
import re
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

from infrastructure.documents import read_document, write_document, delete_document
from app_paths import get_app_paths
from ui.helpers.config_migration import CONFIG_SCHEMA_VERSION, sanitize_config_document
import logging

log = logging.getLogger("olegpainter.ui.helpers.config_store")

CONFIG_DIR_NAME = "configs"
CONFIG_VERSION = CONFIG_SCHEMA_VERSION


@dataclass
class ConfigDescriptor:
    name: str
    slug: str
    path: Path
    categories: List[str]
    created_at: datetime
    updated_at: datetime

    @property
    def created_at_iso(self) -> str:
        return self.created_at.isoformat(timespec="seconds")

    @property
    def updated_at_iso(self) -> str:
        return self.updated_at.isoformat(timespec="seconds")


class ConfigStore:
    """Disk-backed configuration registry."""

    def __init__(self, base_dir: Optional[Path] = None) -> None:
        # OLEGPAINTER_CONFIG_DIR = non-destructive "fresh first-run" mode: point the app at
        # an empty throwaway config dir so the real configs/ (and calibration) stay intact.
        env_dir = os.environ.get("OLEGPAINTER_CONFIG_DIR", "").strip()
        default_dir = Path(env_dir) if env_dir else get_app_paths().configs_root
        self._base_dir = Path(base_dir) if base_dir else default_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)

    @property
    def base_dir(self) -> Path:
        return self._base_dir

    def _file_for_slug(self, slug: str) -> Path:
        if (not isinstance(slug, str) or not slug or slug in (".", "..")
                or re.search(r'[<>:"/\\|?*\x00-\x1f]', slug) or slug.endswith((" ", "."))
                or (os.name == "nt" and os.path.isreserved(slug + ".json"))):
            raise ValueError("Invalid config slug")
        path = self._base_dir / f"{slug}.json"
        if path.resolve().parent != self._base_dir.resolve():
            raise ValueError("Config path escapes storage directory")
        return path

    def slugify(self, name: str, *, existing: Optional[Iterable[str]] = None) -> str:
        cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', name or '').strip()
        cleaned = re.sub(r"\s+", "_", cleaned)
        cleaned = re.sub(r"__+", "_", cleaned)
        cleaned = cleaned.strip("._")
        if not cleaned:
            cleaned = "config"
        if os.name == "nt" and os.path.isreserved(cleaned + ".json"):
            cleaned = "_" + cleaned
        slug = cleaned
        counter = 2
        existing_set = set(existing or [])
        while slug in existing_set or self._file_for_slug(slug).exists():
            slug = f"{cleaned}_{counter}"
            counter += 1
        return slug

    def list(self) -> List[ConfigDescriptor]:
        descriptors: List[ConfigDescriptor] = []
        paths = set(self._base_dir.glob("*.json"))
        paths.update(p.with_suffix("") for p in self._base_dir.glob("*.json.last-good"))
        for path in sorted(paths):
            self._file_for_slug(path.stem)
            descriptor = self._read_descriptor(path)
            if descriptor:
                descriptors.append(descriptor)
        descriptors.sort(key=lambda d: d.updated_at, reverse=True)
        return descriptors

    def _read_descriptor(self, path: Path) -> Optional[ConfigDescriptor]:
        try:
            doc = read_document(path)
            meta = doc.get("meta", {}) if isinstance(doc, dict) else {}
            payload = doc.get("payload") if isinstance(doc, dict) else {}
            name = str(meta.get("name") or path.stem)
            slug = path.stem
            raw_categories = meta.get("categories") if isinstance(meta, dict) else None
            if isinstance(raw_categories, list):
                categories = list(raw_categories)
            elif isinstance(payload, dict):
                categories = list(payload.keys())
            else:
                categories = []
            created_at = self._parse_datetime(meta.get("created_at"))
            updated_at = self._parse_datetime(meta.get("updated_at"))
            if created_at == datetime.min:
                created_at = datetime.fromtimestamp(path.stat().st_ctime)
            if updated_at == datetime.min:
                updated_at = datetime.fromtimestamp(path.stat().st_mtime)
            return ConfigDescriptor(
                name=name,
                slug=slug,
                path=path,
                categories=categories,
                created_at=created_at,
                updated_at=updated_at,
            )
        except Exception:
            log.warning("Cannot read config %s", path, exc_info=True)
            return None

    def load(self, slug: str) -> Dict[str, Any]:
        path = self._file_for_slug(slug)
        from ui.helpers.config_migration import migrate_json_file_in_place

        data, _changed, _backup_path = migrate_json_file_in_place(path)
        if not isinstance(data, dict):
            raise ValueError(f"Config '{slug}' is not a valid JSON object.")
        return data

    def save_new(self, name: str, categories: Iterable[str], payload: Dict[str, Any]) -> ConfigDescriptor:
        existing = [d.slug for d in self.list()]
        slug = self.slugify(name, existing=existing)
        now = self._now()
        doc = {
            "meta": {
                "name": name,
                "slug": slug,
                "categories": list(categories),
                "created_at": now.isoformat(timespec="seconds"),
                "updated_at": now.isoformat(timespec="seconds"),
                "version": CONFIG_VERSION,
            },
            "payload": deepcopy(payload),
        }
        self._write_doc(slug, doc)
        descriptor = self._read_descriptor(self._file_for_slug(slug))
        if descriptor is None:
            raise RuntimeError(f"Config '{slug}' metadata could not be read after creation.")
        return descriptor

    def update(self, slug: str, name: str, categories: Iterable[str], payload: Dict[str, Any]) -> ConfigDescriptor:
        path = self._file_for_slug(slug)
        now = self._now()
        try:
            existing = read_document(path)
        except FileNotFoundError:
            existing = {}
        meta = existing.get("meta", {}) if isinstance(existing, dict) else {}
        created_at = meta.get("created_at")
        doc = {
            "meta": {
                "name": name,
                "slug": slug,
                "categories": list(categories),
                "created_at": created_at or now.isoformat(timespec="seconds"),
                "updated_at": now.isoformat(timespec="seconds"),
                "version": CONFIG_VERSION,
            },
            "payload": deepcopy(payload),
        }
        self._write_doc(slug, doc)
        descriptor = self._read_descriptor(path)
        if descriptor is None:
            raise RuntimeError(f"Config '{slug}' metadata could not be read after update.")
        return descriptor

    def delete(self, slug: str) -> None:
        path = self._file_for_slug(slug)
        delete_document(path)

    def import_external(self, source_path: Path, name_override: Optional[str] = None) -> ConfigDescriptor:
        data = json.loads(Path(source_path).read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data, _changed = sanitize_config_document(data)
        is_doc = isinstance(data, dict) and ("meta" in data or "payload" in data)
        meta = data.get("meta") if is_doc else {}
        name = name_override or (meta.get("name") if isinstance(meta, dict) else None) or Path(source_path).stem
        payload = data.get("payload") if is_doc else data
        if not isinstance(payload, dict):
            payload = {"painter": payload}
        categories_raw = meta.get("categories") if isinstance(meta, dict) else None
        if isinstance(categories_raw, (list, tuple, set)):
            categories = list(categories_raw)
        else:
            categories = list(payload.keys())
        descriptor = self.save_new(name, categories, payload)
        return descriptor

    def _write_doc(self, slug: str, doc: Dict[str, Any]) -> None:
        path = self._file_for_slug(slug)
        path.parent.mkdir(parents=True, exist_ok=True)
        clean_doc, _changed = sanitize_config_document(doc)
        write_document(path, clean_doc)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(timezone.utc).replace(tzinfo=None)

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        def _naive(dt: datetime) -> datetime:
            # Normalize to naive UTC so aware and naive values stay comparable.
            # An imported config with a tz offset (e.g. "...+00:00") otherwise
            # makes descriptors.sort() raise TypeError (aware vs naive).
            if dt.tzinfo is not None:
                return dt.astimezone(timezone.utc).replace(tzinfo=None)
            return dt
        if isinstance(value, datetime):
            return _naive(value)
        if isinstance(value, str):
            try:
                return _naive(datetime.fromisoformat(value))
            except ValueError:
                log.debug('ignored exception in return datetime.fromisoformat(value)', exc_info=True)
        return datetime.min
