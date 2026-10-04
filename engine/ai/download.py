from __future__ import annotations

import hashlib
import shutil
import tempfile
import threading
import time
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from PySide6.QtCore import QThread, Signal

from .registry import AiModelDescriptor, AiModelRegistry, ModelFile
import logging

log = logging.getLogger("olegpainter.engine.ai.download")

_UA = "OlegPainter/AI-Downloader"
# Files at/above this size are fetched with several parallel HTTP Range
# connections — GitHub/S3 release CDNs throttle PER CONNECTION, so a single
# stream crawls even on a fast link; N connections saturate it.
_PARALLEL_MIN_BYTES = 16 * 1024 * 1024
_PARALLEL_MAX_CONNECTIONS = 8
_PARALLEL_BYTES_PER_CONN = 8 * 1024 * 1024


class DownloadWorker(QThread):
    """Background downloader for AI models with progress, cancellation, and verification."""

    progressChanged = Signal(int)  # 0-100
    speedChanged = Signal(str)     # "12.3 MB/s · ~0:45" (rate + ETA), optional
    statusChanged = Signal(str)
    errorOccurred = Signal(str)
    completed = Signal(object)  # emits descriptor
    cancelled = Signal(object)  # emits descriptor

    def __init__(self, registry: AiModelRegistry, descriptor: AiModelDescriptor, *, chunk_size: int = 4 << 20) -> None:
        super().__init__()
        self._registry = registry
        self._descriptor = descriptor
        self._chunk_size = max(chunk_size, 64 * 1024)
        self._cancel_requested = False
        self._current_target: Optional[Path] = None
        self._total_bytes: Optional[int] = None
        self._downloaded_bytes: int = 0
        self._speed_t0: float = 0.0
        self._speed_last_emit: float = 0.0

    # ---------------------------
    # Public API
    # ---------------------------
    @property
    def descriptor(self) -> AiModelDescriptor:
        return self._descriptor

    def cancel(self) -> None:
        self._cancel_requested = True

    # ---------------------------
    # Internal helpers
    # ---------------------------
    def _emit_status(self, text: str) -> None:
        try:
            self.statusChanged.emit(text)
        except Exception:
            log.debug('ignored exception in self.statusChanged.emit(text)', exc_info=True)

    def _emit_progress(self) -> None:
        if not self._total_bytes:
            return
        percent = int((self._downloaded_bytes / self._total_bytes) * 100)
        percent = max(0, min(100, percent))
        try:
            self.progressChanged.emit(percent)
        except Exception:
            log.debug('ignored exception in self.progressChanged.emit(percent)', exc_info=True)

    def run(self) -> None:  # noqa: D401 - Qt entrypoint
        descriptor = self._descriptor
        if descriptor.is_installed():
            self.progressChanged.emit(100)
            self.completed.emit(descriptor)
            return

        try:
            files = tuple(self._collect_download_targets(descriptor))
            self._total_bytes = self._estimate_total_size(files)
            if self._total_bytes:
                self.progressChanged.emit(0)
            for index, model_file in enumerate(files, start=1):
                if self._cancel_requested:
                    raise DownloadCancelled("cancelled by user (before download)")
                self._emit_status(f"downloading {index}/{len(files)}: {model_file.path.name}")
                self._download_file(model_file)
                if self._cancel_requested:
                    raise DownloadCancelled("cancelled by user")
            if descriptor.archive_url:
                if self._cancel_requested:
                    raise DownloadCancelled("cancelled before archive download")
                self._download_archive(descriptor)
            elif descriptor.archive_extract_to:
                # Some manifests may list extract_to without archive_url (bundled archive)
                descriptor.archive_extract_to.mkdir(parents=True, exist_ok=True)
            self._registry.mark_bundle_installed(descriptor)
            if not descriptor.is_installed():
                raise RuntimeError("installation incomplete - required files missing")
            self.progressChanged.emit(100)
            self.completed.emit(descriptor)
        except DownloadCancelled:
            self._cleanup_partial()
            self.cancelled.emit(descriptor)
        except Exception as exc:
            self._cleanup_partial()
            self.errorOccurred.emit(str(exc))

    # ---------------------------
    # File helpers
    # ---------------------------
    def _collect_download_targets(self, descriptor: AiModelDescriptor):
        if descriptor.archive_url:
            archive_path = descriptor.archive_extract_to or self._registry.models_root
            archive_path.parent.mkdir(parents=True, exist_ok=True)
        for model_file in descriptor.files:
            if model_file.url:
                model_file.path.parent.mkdir(parents=True, exist_ok=True)
                yield model_file

    def _estimate_total_size(self, files: tuple[ModelFile, ...]) -> Optional[int]:
        total = 0
        for model_file in files:
            if model_file.size:
                total += int(model_file.size)
            else:
                return None
        descriptor = self._descriptor
        if descriptor.archive_size:
            total += int(descriptor.archive_size)
        return total if total > 0 else None

    def _download_file(self, model_file: ModelFile) -> None:
        if not model_file.url:
            return
        tmp_dir = Path(tempfile.mkdtemp(prefix="ai_dl_"))
        tmp_path = tmp_dir / model_file.path.name
        self._current_target = tmp_path
        expected_hash = model_file.sha256
        expected_md5 = getattr(model_file, "md5", None)
        try:
            done = False
            # Fast path: several parallel Range connections (beats per-connection
            # CDN throttling on a fast link). Any failure falls back to one stream.
            try:
                done = self._parallel_download(
                    model_file.url, tmp_path,
                    expected_hash=expected_hash, expected_md5=expected_md5,
                    expected_size=model_file.size,
                )
            except DownloadCancelled:
                raise
            except Exception as exc:
                log.info("parallel download failed (%s); using single stream", exc)
                done = False
            if not done:
                try:
                    tmp_path.unlink(missing_ok=True)  # type: ignore[attr-defined]
                except Exception:
                    log.debug("ignored exception clearing tmp before single stream", exc_info=True)
                self._stream_download(
                    model_file.url, tmp_path,
                    expected_hash=expected_hash,
                    expected_md5=expected_md5,
                    expected_size=model_file.size,
                )
            model_file.path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(tmp_path), str(model_file.path))
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                log.debug('ignored exception in shutil.rmtree(tmp_dir, ignore_errors=True)', exc_info=True)
        self._current_target = None

    def _download_archive(self, descriptor: AiModelDescriptor) -> None:
        url = descriptor.archive_url
        if not url:
            return
        extract_to = descriptor.archive_extract_to or self._registry.models_root
        extract_to.mkdir(parents=True, exist_ok=True)
        tmp_dir = Path(tempfile.mkdtemp(prefix="ai_dl_archive_"))
        tmp_path = tmp_dir / "archive.bin"
        self._current_target = tmp_path
        try:
            self._emit_status("downloading archive")
            self._stream_download(url, tmp_path, expected_hash=descriptor.archive_sha256, expected_size=descriptor.archive_size)
            self._emit_status("extracting archive")
            if zipfile.is_zipfile(tmp_path):
                with zipfile.ZipFile(tmp_path, "r") as zip_ref:
                    zip_ref.extractall(extract_to)
            else:
                # Generic archive – leave as-is
                dest_path = extract_to / tmp_path.name
                shutil.move(str(tmp_path), str(dest_path))
        finally:
            try:
                shutil.rmtree(tmp_dir, ignore_errors=True)
            except Exception:
                log.debug('ignored exception in shutil.rmtree(tmp_dir, ignore_errors=True)', exc_info=True)
        self._current_target = None

    def _probe_range(self, url: str) -> tuple[Optional[int], bool]:
        """Tiny ranged GET to learn the total size and whether the server honours
        Range. 206 + Content-Range => parallel is possible."""
        req = urllib.request.Request(url, headers={"User-Agent": _UA, "Range": "bytes=0-0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                if r.getcode() == 206:
                    cr = r.headers.get("Content-Range") or ""
                    if "/" in cr:
                        try:
                            return int(cr.rsplit("/", 1)[1]), True
                        except (TypeError, ValueError):
                            pass
                cl = r.headers.get("Content-Length")
                return (int(cl) if cl else None), False
        except Exception:
            log.debug("range probe failed", exc_info=True)
            return None, False

    def _emit_speed(self, done: int, total: int, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and (now - self._speed_last_emit) < 0.12:
            return
        self._speed_last_emit = now
        elapsed = max(1e-3, now - self._speed_t0)
        rate = done / elapsed
        mbps = rate / (1024 * 1024)
        eta = (total - done) / rate if rate > 1.0 else 0.0
        try:
            self.speedChanged.emit(f"{mbps:.1f} MB/s · ~{int(eta // 60)}:{int(eta % 60):02d}")
        except Exception:
            log.debug("ignored exception emitting speed", exc_info=True)

    def _parallel_download(self, url: str, dest: Path, *, expected_hash: Optional[str],
                           expected_md5: Optional[str], expected_size: Optional[int]) -> bool:
        """Download in N parallel HTTP Range connections into one preallocated
        file. Returns False (caller falls back to a single stream) when the
        server doesn't support Range, the size is unknown, or the file is small.
        Verifies size + checksum on the assembled file."""
        total = int(expected_size) if expected_size else None
        probe_total, supports_range = self._probe_range(url)
        if total is None:
            total = probe_total
        if not supports_range or not total or total < _PARALLEL_MIN_BYTES:
            return False
        n_conn = max(2, min(_PARALLEL_MAX_CONNECTIONS,
                            (total + _PARALLEL_BYTES_PER_CONN - 1) // _PARALLEL_BYTES_PER_CONN))
        seg = total // n_conn
        ranges = []
        for i in range(n_conn):
            start = i * seg
            end = total - 1 if i == n_conn - 1 else (start + seg - 1)
            ranges.append((start, end))

        with dest.open("wb") as f:
            f.truncate(total)

        lock = threading.Lock()
        self._downloaded_bytes = 0
        self._speed_t0 = time.monotonic()
        self._speed_last_emit = 0.0
        errors: list[Exception] = []

        def _segment(rng: tuple[int, int]) -> None:
            start, end = rng
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": _UA, "Range": f"bytes={start}-{end}"})
                with urllib.request.urlopen(req, timeout=60) as r, dest.open("r+b") as f:
                    f.seek(start)
                    while True:
                        if self._cancel_requested:
                            return
                        chunk = r.read(self._chunk_size)
                        if not chunk:
                            break
                        f.write(chunk)
                        with lock:
                            self._downloaded_bytes += len(chunk)
                            self.progressChanged.emit(
                                max(0, min(100, int(self._downloaded_bytes / total * 100))))
                            self._emit_speed(self._downloaded_bytes, total)
            except Exception as exc:  # noqa: BLE001 - reported via errors list
                errors.append(exc)

        self._emit_status(f"downloading (×{n_conn}) {dest.name}")
        with ThreadPoolExecutor(max_workers=n_conn) as pool:
            list(pool.map(_segment, ranges))

        if self._cancel_requested:
            raise DownloadCancelled("cancelled during parallel download")
        if errors:
            raise errors[0]
        if dest.stat().st_size != total:
            raise RuntimeError(f"parallel size mismatch for {dest.name}")
        self._verify_file_hashes(dest, expected_hash, expected_md5)
        self._emit_speed(total, total, force=True)
        self.progressChanged.emit(100)
        return True

    @staticmethod
    def _verify_file_hashes(dest: Path, expected_hash: Optional[str], expected_md5: Optional[str]) -> None:
        if not expected_hash and not expected_md5:
            return
        sha = hashlib.sha256() if expected_hash else None
        md5 = hashlib.md5() if expected_md5 else None
        with dest.open("rb") as fp:
            for blk in iter(lambda: fp.read(1 << 20), b""):
                if sha:
                    sha.update(blk)
                if md5:
                    md5.update(blk)
        if expected_hash and sha.hexdigest().lower() != expected_hash.lower():
            raise RuntimeError(f"checksum mismatch for {dest.name}")
        if expected_md5 and md5.hexdigest().lower() != expected_md5.lower():
            raise RuntimeError(f"md5 checksum mismatch for {dest.name}")

    def _stream_download(self, url: str, dest: Path, *, expected_hash: Optional[str],
                         expected_size: Optional[int], expected_md5: Optional[str] = None) -> None:
        req = urllib.request.Request(url, headers={"User-Agent": "OlegPainter/AI-Downloader"})
        with urllib.request.urlopen(req) as response, dest.open("wb") as fp:
            hash_ctx = hashlib.sha256() if expected_hash else None
            md5_ctx = hashlib.md5() if expected_md5 else None
            # Progress source: the cumulative manifest total if known, else THIS
            # response's Content-Length. Without this fallback a manifest entry
            # with no `size` showed no progress at all — the button froze at 0%
            # for the whole multi-minute download (live report).
            file_total = 0
            if not self._total_bytes:
                try:
                    file_total = int(response.headers.get("Content-Length") or 0)
                except (TypeError, ValueError):
                    file_total = 0
            file_done = 0
            while True:
                if self._cancel_requested:
                    raise DownloadCancelled("cancelled during download")
                chunk = response.read(self._chunk_size)
                if not chunk:
                    break
                fp.write(chunk)
                if hash_ctx:
                    hash_ctx.update(chunk)
                if md5_ctx:
                    md5_ctx.update(chunk)
                if self._total_bytes:
                    self._downloaded_bytes += len(chunk)
                    self._emit_progress()
                elif file_total:
                    file_done += len(chunk)
                    percent = max(0, min(100, int(file_done / file_total * 100)))
                    try:
                        self.progressChanged.emit(percent)
                    except Exception:
                        log.debug("ignored exception emitting per-file progress", exc_info=True)
        if expected_size and dest.stat().st_size != expected_size:
            raise RuntimeError(f"downloaded size mismatch for {dest.name}")
        if expected_hash:
            actual_hash = hash_ctx.hexdigest() if hash_ctx else _sha256_file(dest)
            if actual_hash.lower() != expected_hash.lower():
                raise RuntimeError(f"checksum mismatch for {dest.name}")
        if expected_md5:
            actual_md5 = md5_ctx.hexdigest() if md5_ctx else None
            if actual_md5 is None:
                ctx = hashlib.md5()
                with dest.open("rb") as fp2:
                    for blk in iter(lambda: fp2.read(1 << 20), b""):
                        ctx.update(blk)
                actual_md5 = ctx.hexdigest()
            if actual_md5.lower() != expected_md5.lower():
                raise RuntimeError(f"md5 checksum mismatch for {dest.name}")

    def _cleanup_partial(self) -> None:
        target = self._current_target
        if not target:
            return
        try:
            if target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
            else:
                target.unlink(missing_ok=True)  # type: ignore[attr-defined]
        except Exception:
            log.debug('ignored exception in if target.is_dir(): shutil.rmtree(target, ignore_errors=True) else: target.un...', exc_info=True)


class DownloadCancelled(RuntimeError):
    """Internal control-flow exception for cancellation."""


def _sha256_file(path: Path) -> str:
    ctx = hashlib.sha256()
    with path.open("rb") as fp:
        for chunk in iter(lambda: fp.read(1 << 20), b""):
            ctx.update(chunk)
    return ctx.hexdigest()

