"""AiCenter (AI-001): opt-in, persisted preferences, one job at a time."""
from __future__ import annotations

import os
import sys
import threading
import time

import numpy as np
import pytest
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from application.ai import AiCenter, AiUnavailable, EngineAiBackend  # noqa: E402
from engine.ai.models import ModelFile, ModelInfo, ModelStore  # noqa: E402


def _store(tmp_path, installed=("bg.fake",)):
    catalog = {
        "bg.fake": ModelInfo(id="bg.fake", task="background", title={"ru": "Фон"}, description={"ru": ""},
                             license_name="MIT", license_url="",
                             files=(ModelFile("bg/fake.onnx", "https://x/f", 4, "0" * 64),)),
        "depth.fake": ModelInfo(id="depth.fake", task="depth", title={"ru": "Глубина"}, description={"ru": ""},
                                license_name="MIT", license_url="",
                                files=(ModelFile("depth/fake.onnx", "https://x/d", 4, "0" * 64),)),
    }
    for model_id in installed:
        path = tmp_path / "models" / catalog[model_id].files[0].path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"1234")
    return ModelStore(tmp_path / "models", catalog)


def _wait(predicate, seconds=5.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_ai_is_off_by_default_and_blocks_jobs(tmp_path):
    center = AiCenter(tmp_path / "ai.json", _store(tmp_path))
    assert not center.prefs.enabled
    assert not EngineAiBackend(center).is_enabled()
    with pytest.raises(AiUnavailable):
        center.start("flatten", Image.new("RGB", (8, 8)), 1, {}, lambda *a: None)
    assert center.foreground(Image.new("RGB", (8, 8))) is None


def test_preferences_persist(tmp_path):
    center = AiCenter(tmp_path / "ai.json", _store(tmp_path))
    center.set_enabled(True)
    center.set_device("cpu")
    center.set_background_model("bg.fake")
    center.set_depth_order(True)
    again = AiCenter(tmp_path / "ai.json", _store(tmp_path))
    assert (again.prefs.enabled, again.prefs.device, again.prefs.background_model, again.prefs.depth_order) == \
        (True, "cpu", "bg.fake", True)


def test_missing_model_is_reported_not_crashed(tmp_path):
    center = AiCenter(tmp_path / "ai.json", _store(tmp_path, installed=()))
    center.set_enabled(True)
    with pytest.raises(AiUnavailable):
        center.start("background", Image.new("RGB", (8, 8)), 1, {}, lambda *a: None)
    snapshot = center.snapshot()
    assert snapshot["tools"]["background"] is False
    assert all(not m["installed"] for m in snapshot["models"])


def test_one_job_at_a_time_and_cancel_drops_result(tmp_path, monkeypatch):
    center = AiCenter(tmp_path / "ai.json", _store(tmp_path))
    center.set_enabled(True)
    release = threading.Event()

    def slow_compute(kind, image, key, params, cancelled):
        release.wait(5)
        return image

    monkeypatch.setattr(center, "_compute", slow_compute)
    results = []
    center.start("flatten", Image.new("RGB", (8, 8)), 1, {}, lambda *a: results.append(a))
    with pytest.raises(AiUnavailable):
        center.start("flatten", Image.new("RGB", (8, 8)), 1, {}, lambda *a: None)
    center.cancel_job()
    release.set()
    time.sleep(0.2)
    assert results == []
    assert not center.busy()


def test_job_result_carries_image_key(tmp_path):
    center = AiCenter(tmp_path / "ai.json", _store(tmp_path))
    center.set_enabled(True)
    results = []
    center.start("flatten", Image.new("RGBA", (16, 16), (200, 10, 10, 255)), "rev-7", {}, lambda *a: results.append(a))
    assert _wait(lambda: results)
    kind, image, key = results[0]
    assert (kind, key) == ("flatten", "rev-7") and image.size == (16, 16)


def test_disabling_frees_sessions_and_selection(tmp_path):
    center = AiCenter(tmp_path / "ai.json", _store(tmp_path))
    center.set_enabled(True)
    center.pool._sessions[("x", "cpu")] = object()
    center.selection_mask = np.ones((2, 2), bool)
    center.set_enabled(False)
    assert not center.pool._sessions and center.selection_mask is None


def test_gpu_failure_falls_back_to_cpu_even_after_a_device_switch(tmp_path, monkeypatch):
    # Live 2026-09-30: DirectML failed with a cp1251 message decoded as UTF-8, and a
    # device switch during the job had cleared the pool, so no CPU retry happened.
    import types
    from engine.ai import runtime

    class Session:
        def __init__(self, path, options, providers):
            self.provider = providers[0][0] if isinstance(providers[0], tuple) else providers[0]

        def get_providers(self):
            return [self.provider]

        def run(self, outputs, feed):
            if self.provider == "DmlExecutionProvider":
                raise UnicodeDecodeError("utf-8", b"\xcd", 0, 1, "invalid continuation byte")
            return ["cpu"]

    fake = types.SimpleNamespace(InferenceSession=Session, SessionOptions=lambda: types.SimpleNamespace(),
                                 ExecutionMode=types.SimpleNamespace(ORT_SEQUENTIAL=1))
    monkeypatch.setitem(sys.modules, "onnxruntime", fake)
    monkeypatch.setattr(runtime, "best_gpu", lambda: runtime.GpuInfo("Fake GPU", 0, 8000))
    pool = runtime.SessionPool()
    path = tmp_path / "model.onnx"
    session = pool.get(path, "gpu")
    pool.clear()  # the user picked another device while the job held its GPU session
    monkeypatch.setattr(pool, "get", lambda p, device, _get=pool.get: session if device == "gpu" else _get(p, device))
    assert pool.run(path, "gpu", ["out"], {}) == ["cpu"]
    assert str(path) in pool._gpu_failed


def test_install_message_uses_the_viewer_language_and_gpu_error_is_readable(tmp_path):
    from dataclasses import replace
    from application.ai import _readable
    store = _store(tmp_path)
    store.catalog["bg.fake"] = replace(store.catalog["bg.fake"], title={"ru": "Фон", "en": "Background"})
    center = AiCenter(tmp_path / "ai.json", store)
    center._set_message("Модель установлена: {title}.", model="bg.fake")
    assert center.snapshot("ru")["message"] == "Модель установлена: Фон."
    assert center.snapshot("en")["message"] == "Модель установлена: Background."
    assert "видеокарта не справилась" in _readable(UnicodeDecodeError("utf-8", b"\xcd", 0, 1, "bad"))
