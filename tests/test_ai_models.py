"""Model catalog and installer (AI-001): nothing is trusted before its hash."""
from __future__ import annotations

import hashlib
import io
import os
import sys
import zipfile

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from engine.ai.models import (  # noqa: E402
    CATALOG_PATH, InstallCancelled, InstallError, ModelArchive, ModelFile, ModelInfo, ModelStore, load_catalog)


class FakeResponse(io.BytesIO):
    def __init__(self, data: bytes, status: int = 200):
        super().__init__(data)
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


def _opener(payloads: dict, calls: list):
    def open_(request, timeout=None):
        calls.append((request.full_url, request.get_header("Range")))
        data = payloads[request.full_url]
        header = request.get_header("Range")
        if header:
            start = int(header.split("=")[1].rstrip("-"))
            return FakeResponse(data[start:], status=206)
        return FakeResponse(data)
    return open_


def _file_model(data: bytes, *, sha: str | None = None) -> dict:
    info = ModelInfo(id="t.model", task="background", title={"ru": "Т"}, description={"ru": "Т"},
                     license_name="MIT", license_url="",
                     files=(ModelFile("t/model.onnx", "https://x/model.onnx", len(data),
                                      sha or hashlib.sha256(data).hexdigest()),))
    return {info.id: info}


def test_real_catalog_is_valid_and_commercially_licensed():
    catalog = load_catalog(CATALOG_PATH)
    assert {m.task for m in catalog.values()} == {"background", "segment", "depth", "lineart", "inpaint", "upscale"}
    for info in catalog.values():
        assert info.download_size > 0
        assert "NC" not in info.license_name and "non-commercial" not in info.license_name.lower()
        for item in info.files:
            assert len(item.sha256) == 64 and item.url.startswith("https://")
        if info.archive:
            assert len(info.archive.sha256) == 64 and info.archive.check


def test_install_verifies_hash_and_reports_installed(tmp_path):
    data = b"model-bytes" * 1000
    calls = []
    store = ModelStore(tmp_path, _file_model(data), opener=_opener({"https://x/model.onnx": data}, calls))
    assert not store.is_installed("t.model")
    seen = []
    store.install("t.model", lambda done, total: seen.append((done, total)))
    assert store.is_installed("t.model")
    assert (tmp_path / "t" / "model.onnx").read_bytes() == data
    assert seen[-1] == (len(data), len(data))
    store.remove("t.model")
    assert not store.is_installed("t.model")


def test_wrong_hash_never_replaces_target(tmp_path):
    data = b"tampered" * 100
    store = ModelStore(tmp_path, _file_model(data, sha="0" * 64), opener=_opener({"https://x/model.onnx": data}, []))
    with pytest.raises(InstallError):
        store.install("t.model")
    assert not (tmp_path / "t" / "model.onnx").exists()
    assert not (tmp_path / "t" / "model.onnx.part").exists()


def test_cancel_keeps_partial_and_next_install_resumes(tmp_path):
    data = os.urandom(3 * (1 << 20) + 17)
    calls = []
    store = ModelStore(tmp_path, _file_model(data), opener=_opener({"https://x/model.onnx": data}, calls))
    chunks = []
    with pytest.raises(InstallCancelled):
        store.install("t.model", lambda done, total: chunks.append(done), cancelled=lambda: len(chunks) >= 1)
    part = tmp_path / "t" / "model.onnx.part"
    assert part.exists() and 0 < part.stat().st_size < len(data)
    store.install("t.model")
    assert calls[-1][1] == f"bytes={part.stat().st_size if part.exists() else 0}-" or calls[-1][1].startswith("bytes=")
    assert (tmp_path / "t" / "model.onnx").read_bytes() == data


def _archive_model(blob: bytes, check=("tool.exe",)) -> dict:
    info = ModelInfo(id="t.zip", task="upscale", title={"ru": "Т"}, description={"ru": "Т"},
                     license_name="BSD", license_url="",
                     archive=ModelArchive("https://x/a.zip", len(blob), hashlib.sha256(blob).hexdigest(), "up/tool", check))
    return {info.id: info}


def _zip(entries: dict) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as bundle:
        for name, data in entries.items():
            bundle.writestr(name, data)
    return buffer.getvalue()


def test_archive_installs_into_its_folder(tmp_path):
    blob = _zip({"tool.exe": b"exe", "models/a.param": b"p"})
    store = ModelStore(tmp_path, _archive_model(blob), opener=_opener({"https://x/a.zip": blob}, []))
    store.install("t.zip")
    assert store.is_installed("t.zip")
    assert (tmp_path / "up" / "tool" / "models" / "a.param").read_bytes() == b"p"
    assert not (tmp_path / "_downloads" / "t.zip.zip").exists()


def test_archive_with_escaping_paths_is_rejected(tmp_path):
    blob = _zip({"tool.exe": b"exe", "../../evil.txt": b"x"})
    store = ModelStore(tmp_path, _archive_model(blob), opener=_opener({"https://x/a.zip": blob}, []))
    with pytest.raises(InstallError):
        store.install("t.zip")
    assert not (tmp_path.parent / "evil.txt").exists()
    assert not store.is_installed("t.zip")
