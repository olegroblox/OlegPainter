from __future__ import annotations

import hashlib
from infrastructure.documents import atomic_bytes, write_document, backup_path
import os
from concurrent.futures import Future
from copy import deepcopy
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from threading import Thread

from PySide6.QtCore import QObject, QTimer, Signal, QEventLoop, QThread
from application.session_image import SessionImage
from ui.helpers.config_payload import ALL_CATEGORY_IDS, collect_payload

from ui.helpers.config_migration import sanitize_config_document
from ui.helpers.hotkey_definitions import migrate_legacy_hotkeys
import logging

log = logging.getLogger("olegpainter.session_persistence")


@dataclass(frozen=True)
class SessionSnapshot:
    revision: int
    document: dict
    image: SessionImage | None


class SessionStore:
    def __init__(self, config_store, session_file_name: str = "__autosave.json") -> None:
        self._config_store = config_store
        self._session_file_name = str(session_file_name or "__autosave.json")

    @property
    def base_dir(self) -> Path:
        return Path(self._config_store.base_dir)

    @property
    def session_path(self) -> Path:
        return self.base_dir / self._session_file_name

    @property
    def assets_dir(self) -> Path:
        return self.base_dir / "__session_assets"

    def load_document(self) -> dict[str, Any]:
        if not self.session_path.exists() and not backup_path(self.session_path).exists():
            return {}
        slug = Path(self._session_file_name).stem
        data = self._config_store.load(slug)
        return data if isinstance(data, dict) else {}

    def prepare_restore_image_state(self, image_state: Any) -> dict[str, Any]:
        if not isinstance(image_state, dict):
            return {}
        resolved = deepcopy(image_state)
        cache_relpath = resolved.get("cache_relpath")
        if isinstance(cache_relpath, str) and cache_relpath.strip():
            candidate = Path(cache_relpath)
            cache_path = candidate if candidate.is_absolute() else (self.base_dir / candidate)
            resolved["cache_path"] = str(cache_path)
        return resolved

    def write_autosave(self, document: dict[str, Any], image: SessionImage | None):
        if not isinstance(document, dict):
            raise ValueError("Session document must be a dict.")
        doc = deepcopy(document)
        existing = self.load_document()
        existing_meta = existing.get("meta", {})
        meta = doc["meta"]
        for key in ("name", "slug", "created_at", "version"):
            if existing_meta.get(key) is not None:
                meta[key] = existing_meta[key]
        payload = doc.setdefault("payload", {})
        if not isinstance(payload, dict):
            payload = {}
            doc["payload"] = payload
        session = payload.setdefault("session", {})
        if not isinstance(session, dict):
            session = {}
            payload["session"] = session

        image_state = session.get("image")
        png_bytes = image.encode() if image is not None else None
        finalized_image_state = self._finalize_image_state(image_state, png_bytes)
        if finalized_image_state:
            session["image"] = finalized_image_state
        else:
            session.pop("image", None)

        clean_doc, _changed = sanitize_config_document(doc)
        self._write_json_atomic(self.session_path, clean_doc)
        return clean_doc, png_bytes

    def _finalize_image_state(self, image_state: Any, png_bytes: bytes | None) -> dict[str, Any]:
        if not isinstance(image_state, dict):
            return {}
        descriptor = deepcopy(image_state)
        kind = str(descriptor.get("kind") or "").strip().lower()
        if not kind:
            return {}

        if not png_bytes:
            raise ValueError("Изображение сессии не сохранено: нет данных PNG.")
        fingerprint = hashlib.sha1(png_bytes).hexdigest()
        descriptor["fingerprint"] = fingerprint
        # Derive the asset path from these exact bytes, never a stale descriptor.
        cache_path = self.assets_dir / f"source_{fingerprint}.png"
        if not cache_path.exists() or cache_path.read_bytes() != png_bytes:
            self._write_bytes_atomic(cache_path, png_bytes)
        descriptor["cache_relpath"] = os.path.relpath(cache_path, self.base_dir).replace("\\", "/")

        return descriptor

    @staticmethod
    def _write_json_atomic(path: Path, document: dict[str, Any]) -> None:
        write_document(path, document)

    @staticmethod
    def _write_bytes_atomic(path: Path, data: bytes) -> None:
        atomic_bytes(path, data)


class SessionManager(QObject):
    DEBOUNCE_MS = 1500

    saveFailed = Signal(str)

    def __init__(self, service, profiles, config_store, *, shell=None, parent=None,
                 categories=None, session_file_name="__autosave.json"):
        super().__init__(parent)
        self._shell = shell
        self._config_store = config_store
        self._store = SessionStore(config_store, session_file_name=session_file_name)
        self._categories = set(ALL_CATEGORY_IDS if categories is None else categories)
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.timeout.connect(self._autosave)
        self._service = service
        self._profiles = profiles
        self._tracking_suspended = False
        self._dirty_sections: set[str] = set()
        self._revision = 0
        self._active = None
        self._pending = None
        self._closed = False
        self._write_timer = QTimer(self)
        self._write_timer.setInterval(10)
        self._write_timer.timeout.connect(self._poll_write)
        self._connect_service(service)
        self._connect_profiles(profiles)

    @property
    def store(self) -> SessionStore:
        return self._store

    def set_shell(self, shell):
        self._shell = shell

    def _autosave(self):
        try:
            self.request_save()
        except Exception as exc:
            log.exception("Autosave failed; dirty state retained")
            self.saveFailed.emit(str(exc))

    def close(self):
        if self._closed:
            return
        self.suspend()
        self._closed = True
        if self._pending is not None:
            self._pending[1].cancel()
            self._pending = None
        if self._active is not None:
            # Cleanup waits for owned snapshots only; the writer never uses UI.
            try:
                self._wait(self._active[1])
            except Exception:
                # Explicit close without saving may follow a failed autosave.
                # The failure was already reported; still release our bindings.
                log.warning("Closing session after a reported write failure")
        self._disconnect_service(self._service)
        self._disconnect_profiles(self._profiles)

    def restore(self) -> bool:
        if self._service is None:
            return False
        document = self._store.load_document()
        if not document:
            return False
        payload = document.get("payload", {}) if isinstance(document, dict) else {}
        if not isinstance(payload, dict):
            return False
        session = payload.get("session", {})
        if not isinstance(session, dict):
            session = {}
        image_state = self._store.prepare_restore_image_state(session.get("image"))
        ui_state = session.get("ui")
        saved_profiles = session.get("profiles")
        place_state = payload.get("place")
        painter_state = payload.get("painter")
        hotkeys_state = payload.get("hotkeys")

        self._tracking_suspended = True
        try:
            profiles = self._profiles
            if profiles is not None and callable(getattr(profiles, "restore_session_profiles", None)):
                profiles.restore_session_profiles(saved_profiles)
            # Restore the full painter first, then the explicit saved selection.
            # Profile restoration never loads a default preset over session data.
            if callable(getattr(self._service, "restore_session_state", None)):
                self._service.restore_session_state({"image": image_state, "painter": painter_state})
            else:
                if isinstance(painter_state, dict):
                    self._service.load_config(painter_state)
            if profiles is not None and isinstance(place_state, dict):
                profiles.apply_place_state(place_state)
            if isinstance(hotkeys_state, dict):
                try:
                    self._service.apply_hotkeys(migrate_legacy_hotkeys(hotkeys_state))
                except Exception:
                    log.debug('ignored exception in self._service.apply_hotkeys(hotkeys_state)', exc_info=True)
            if callable(getattr(self._shell, "restore_shell_state", None)):
                self._shell.restore_shell_state(ui_state)
        finally:
            self._tracking_suspended = False
            self._dirty_sections.clear()
            self._timer.stop()
        return True

    def mark_dirty(self, section: str = "session") -> None:
        if self._tracking_suspended:
            return
        self._dirty_sections.add(str(section or "session"))
        self._revision += 1
        self._timer.start(self.DEBOUNCE_MS)

    def suspend(self) -> None:
        """Stop tracking and cancel any pending debounced autosave. Call on shutdown so a
        queued save can't fire after the service/engine has been torn down."""
        self._tracking_suspended = True
        try:
            self._timer.stop()
        except Exception:
            log.debug("ignored exception stopping session timer", exc_info=True)

    def flush_now(self, *, force: bool = False) -> bool:
        """Compatibility/cleanup adapter; interactive paths request_save()."""
        return self._wait(self.request_save(force=force))

    @staticmethod
    def _wait(future):
        if not future.done():
            loop = QEventLoop()
            future.add_done_callback(lambda _: loop.quit())
            loop.exec(QEventLoop.ExcludeUserInputEvents)
        return future.result()

    def request_save(self, *, force: bool = False) -> Future:
        if not QThread.isMainThread():
            raise RuntimeError("Session snapshots must be captured on the Qt main thread")
        future = Future()
        if self._closed:
            future.set_exception(RuntimeError("Session is closed"))
            return future
        if self._service is None or self._profiles is None:
            future.set_result(False)
            return future
        if not force and not self._dirty_sections:
            future.set_result(False)
            return future
        self._timer.stop()
        document = self._build_document()
        image = (self._service.snapshot_session_image()
                 if document["payload"]["session"].get("image") else None)
        snapshot = SessionSnapshot(self._revision, deepcopy(document), image)
        if self._active is not None:
            # One waiting snapshot. Existing callers share its promise; replacing
            # it with newer state still satisfies their request to persist it.
            if self._pending is not None and not self._pending[1].cancelled():
                future = self._pending[1]
            self._pending = snapshot, future
        else:
            self._start_write(snapshot, future)
        return future

    def _start_write(self, snapshot, future):
        if not future.set_running_or_notify_cancel():
            self._write_timer.stop()
            return
        result = Future()
        store = self._store
        def write():
            try:
                result.set_result(store.write_autosave(snapshot.document, snapshot.image))
            except Exception as error:
                result.set_exception(error)
        thread = Thread(target=write, name="OlegPainter session writer")
        self._active = snapshot, future, result, thread
        try:
            thread.start()
        except Exception as error:
            result.set_exception(error)
        self._write_timer.start()

    def _poll_write(self):
        if self._active is None or self._active[3].is_alive():
            return
        snapshot, future, result, _thread = self._active
        self._active = None
        error = None
        try:
            _document, png = result.result()
            if self._revision == snapshot.revision:
                self._dirty_sections.clear()
            if snapshot.image is not None:
                self._service.accept_session_image_png(snapshot.image.token, png)
        except Exception as exc:
            error = exc
        if self._pending is not None:
            pending, self._pending = self._pending, None
            self._start_write(*pending)
        else:
            self._write_timer.stop()
        # Publish on Qt only, after transitioning ownership of all work. A close
        # callback may destroy the session or request another save immediately.
        if error is not None:
            log.error("Session write failed; dirty state retained: %s", error)
            self.saveFailed.emit(str(error))
            future.set_exception(error)
        else:
            future.set_result(True)

    def _build_document(self) -> dict[str, Any]:
        now = datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds")
        categories = sorted(self._categories)
        payload = collect_payload(self._service, self._profiles, self._categories, strict=True)
        session = payload.setdefault("session", {})
        if not isinstance(session, dict):
            session = {}
            payload["session"] = session
        if callable(getattr(self._service, "export_image_state", None)):
            session["image"] = self._service.export_image_state()
        if callable(getattr(self._profiles, "export_session_profiles", None)):
            session["profiles"] = self._profiles.export_session_profiles()
        if callable(getattr(self._shell, "export_shell_state", None)):
            session["ui"] = self._shell.export_shell_state()
        meta = {
            "name": "Last Session",
            "slug": "__autosave",
            "categories": categories,
            "created_at": now,
            "updated_at": now,
            "version": None,
        }
        return {"meta": meta, "payload": payload}

    def _connect_service(self, service) -> None:
        signal = getattr(service, "sessionStateChanged", None)
        if signal is not None:
            try:
                signal.connect(self._on_service_session_changed)
            except Exception:
                log.debug('ignored exception in signal.connect(self._on_service_session_changed)', exc_info=True)
        hotkeys_signal = getattr(service, "hotkeysChanged", None)
        if hotkeys_signal is not None:
            try:
                hotkeys_signal.connect(self._on_hotkeys_changed)
            except Exception:
                log.debug('ignored exception in hotkeys_signal.connect(self._on_hotkeys_changed)', exc_info=True)

    def _disconnect_service(self, service) -> None:
        signal = getattr(service, "sessionStateChanged", None)
        if signal is not None:
            try:
                signal.disconnect(self._on_service_session_changed)
            except Exception:
                log.debug('ignored exception in signal.disconnect(self._on_service_session_changed)', exc_info=True)
        hotkeys_signal = getattr(service, "hotkeysChanged", None)
        if hotkeys_signal is not None:
            try:
                hotkeys_signal.disconnect(self._on_hotkeys_changed)
            except Exception:
                log.debug('ignored exception in hotkeys_signal.disconnect(self._on_hotkeys_changed)', exc_info=True)

    def _connect_profiles(self, profiles) -> None:
        place_signal = getattr(profiles, "placeStateChanged", None)
        if place_signal is not None:
            try:
                place_signal.connect(self._on_place_changed)
            except Exception:
                log.debug('ignored exception in place_signal.connect(self._on_place_changed)', exc_info=True)
        profiles_signal = getattr(profiles, "sessionProfilesChanged", None)
        if profiles_signal is not None:
            try:
                profiles_signal.connect(self._on_profiles_changed)
            except Exception:
                log.debug('ignored exception in profiles_signal.connect(self._on_profiles_changed)', exc_info=True)

    def _disconnect_profiles(self, profiles) -> None:
        place_signal = getattr(profiles, "placeStateChanged", None)
        if place_signal is not None:
            try:
                place_signal.disconnect(self._on_place_changed)
            except Exception:
                log.debug('ignored exception in place_signal.disconnect(self._on_place_changed)', exc_info=True)
        profiles_signal = getattr(profiles, "sessionProfilesChanged", None)
        if profiles_signal is not None:
            try:
                profiles_signal.disconnect(self._on_profiles_changed)
            except Exception:
                log.debug('ignored exception in profiles_signal.disconnect(self._on_profiles_changed)', exc_info=True)

    def _on_service_session_changed(self, payload: Any) -> None:
        if isinstance(payload, dict):
            sections = payload.get("sections")
            if isinstance(sections, (list, tuple, set)) and sections:
                for section in sections:
                    self.mark_dirty(str(section))
                return
        self.mark_dirty("painter")

    def _on_hotkeys_changed(self, *_args) -> None:
        self.mark_dirty("hotkeys")

    def _on_place_changed(self, *_args) -> None:
        self.mark_dirty("place")

    def _on_profiles_changed(self, *_args) -> None:
        self.mark_dirty("profiles")
