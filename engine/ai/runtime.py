"""Where AI models run: DirectML on the strongest GPU, CPU otherwise (AI-001).

onnxruntime-directml works on NVIDIA, AMD and Intel GPUs from one wheel and
falls back to the CPU. DirectML device ids follow DXGI adapter order, which on
hybrid PCs may put an integrated GPU first, so the high-performance adapter is
looked up explicitly. Sessions are cached per (model file, device) and dropped
together when AI is switched off.
"""
from __future__ import annotations

import ctypes
import logging
import os
import threading
import uuid
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

log = logging.getLogger("olegpainter.ai.runtime")

DEVICES = ("auto", "gpu", "cpu")


@dataclass(frozen=True)
class GpuInfo:
    name: str
    device_id: int
    memory_mb: int


def _guid(text: str):
    class GUID(ctypes.Structure):
        _fields_ = [("a", ctypes.c_uint32), ("b", ctypes.c_uint16), ("c", ctypes.c_uint16), ("d", ctypes.c_ubyte * 8)]
    value = uuid.UUID(text)
    result = GUID()
    result.a, result.b, result.c = value.fields[0], value.fields[1], value.fields[2]
    result.d[:] = value.bytes[8:]
    return result


@lru_cache(maxsize=1)
def best_gpu() -> GpuInfo | None:
    """The high-performance DXGI adapter and its DirectML device id, or None
    (no Windows, no discrete/integrated GPU, only the software renderer)."""
    if os.name != "nt":
        return None
    try:
        class DESC1(ctypes.Structure):
            _fields_ = [("Description", ctypes.c_wchar * 128), ("VendorId", ctypes.c_uint), ("DeviceId", ctypes.c_uint),
                        ("SubSysId", ctypes.c_uint), ("Revision", ctypes.c_uint), ("DedicatedVideoMemory", ctypes.c_size_t),
                        ("DedicatedSystemMemory", ctypes.c_size_t), ("SharedSystemMemory", ctypes.c_size_t),
                        ("Luid", ctypes.c_int64), ("Flags", ctypes.c_uint)]
        dxgi = ctypes.WinDLL("dxgi")
        factory = ctypes.c_void_p()
        iid_factory6 = _guid("c1b6694f-ff09-44a9-b03c-77900a0a1d17")
        if dxgi.CreateDXGIFactory1(ctypes.byref(iid_factory6), ctypes.byref(factory)) != 0:
            return None
        table = ctypes.cast(ctypes.cast(factory, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
        enum_adapters = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint,
                                           ctypes.POINTER(ctypes.c_void_p))(table[12])
        enum_by_preference = ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.c_uint, ctypes.c_uint,
                                                ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p))(table[29])
        iid_adapter1 = _guid("29038f61-3839-4626-91fd-086879011a05")

        def describe(adapter):
            vtable = ctypes.cast(ctypes.cast(adapter, ctypes.POINTER(ctypes.c_void_p))[0], ctypes.POINTER(ctypes.c_void_p))
            desc = DESC1()
            ctypes.WINFUNCTYPE(ctypes.c_long, ctypes.c_void_p, ctypes.POINTER(DESC1))(vtable[10])(adapter, ctypes.byref(desc))
            return desc

        best = ctypes.c_void_p()
        # DXGI_GPU_PREFERENCE_HIGH_PERFORMANCE = 1
        if enum_by_preference(factory, 0, 1, ctypes.addressof(iid_adapter1), ctypes.byref(best)) != 0:
            return None
        wanted = describe(best)
        if wanted.Flags & 2:  # DXGI_ADAPTER_FLAG_SOFTWARE: no real GPU
            return None
        for index in range(16):
            adapter = ctypes.c_void_p()
            if enum_adapters(factory, index, ctypes.byref(adapter)) != 0:
                break
            if describe(adapter).Luid == wanted.Luid:
                return GpuInfo(wanted.Description, index, int(wanted.DedicatedVideoMemory // 2**20))
    except Exception:
        log.debug("GPU detection failed", exc_info=True)
    return None


def directml_available() -> bool:
    try:
        import onnxruntime as ort
        return "DmlExecutionProvider" in ort.get_available_providers()
    except Exception:
        return False


def resolve_device(preference: str) -> str:
    """'gpu' or 'cpu' for a user preference; 'gpu' only when it can work."""
    preference = preference if preference in DEVICES else "auto"
    if preference == "cpu":
        return "cpu"
    return "gpu" if directml_available() and best_gpu() is not None else "cpu"


class SessionPool:
    """Cached onnxruntime sessions; one lock per pool keeps loads serial."""

    def __init__(self):
        self._sessions = {}
        self._lock = threading.Lock()
        self.last_device = {}
        self._gpu_failed: set[str] = set()

    def get(self, path: Path, device: str):
        import onnxruntime as ort
        key = (str(path), device)
        with self._lock:
            session = self._sessions.get(key)
            if session is not None:
                return session
            options = ort.SessionOptions()
            if device == "gpu":
                gpu = best_gpu()
                # DirectML requires sequential execution without memory pattern.
                options.enable_mem_pattern = False
                options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
                providers = [("DmlExecutionProvider", {"device_id": gpu.device_id if gpu else 0}), "CPUExecutionProvider"]
                try:
                    session = ort.InferenceSession(str(path), options, providers=providers)
                except Exception:
                    log.warning("DirectML session failed for %s; using the CPU", path, exc_info=True)
                    session = None
            if session is None:
                session = ort.InferenceSession(str(path), ort.SessionOptions(), providers=["CPUExecutionProvider"])
            used = "gpu" if session.get_providers()[0] == "DmlExecutionProvider" else "cpu"
            if used == "cpu" or device == "gpu":
                self.last_device[str(path)] = used
            log.info("AI model %s loaded on %s", Path(path).name, used)
            self._sessions[key] = session
            return session

    def run(self, path: Path, device: str, outputs, feed: dict):
        """session.run with a CPU retry: some models load on DirectML but hit an
        unsupported operator only when running (LaMa's FFT). The model then
        stays on the CPU for the rest of the session."""
        key = str(path)
        if device == "gpu" and key in self._gpu_failed:
            device = "cpu"
        session = self.get(path, device)
        try:
            return session.run(outputs, feed)
        except Exception:
            # Ask the session itself: switching the device clears the pool (and
            # last_device) while a job may still hold its GPU session.
            if device != "gpu" or session.get_providers()[0] != "DmlExecutionProvider":
                raise
            log.warning("GPU run failed for %s; retrying on the CPU", Path(path).name, exc_info=True)
            self._gpu_failed.add(key)
            return self.get(path, "cpu").run(outputs, feed)

    def inputs(self, path: Path, device: str) -> list[str]:
        device = "cpu" if str(path) in self._gpu_failed else device
        return [item.name for item in self.get(path, device).get_inputs()]

    def outputs(self, path: Path, device: str) -> list[str]:
        device = "cpu" if str(path) in self._gpu_failed else device
        return [item.name for item in self.get(path, device).get_outputs()]

    def clear(self) -> None:
        with self._lock:
            self._sessions.clear()
            self.last_device.clear()
            self._gpu_failed.clear()


# Kept for the old imports in engine/ai/__init__.py and the classic UI.
def detect_directml_provider(*_args, **_kwargs) -> bool:
    return directml_available()


def resolve_execution_providers(prefer_gpu: bool = True, logger=None):
    if prefer_gpu and directml_available():
        gpu = best_gpu()
        return ([("DmlExecutionProvider", {"device_id": gpu.device_id if gpu else 0}), "CPUExecutionProvider"], "gpu")
    return (["CPUExecutionProvider"], "cpu")


def describe_execution_provider(key: str) -> str:
    key = (key or "").lower()
    if key == "gpu":
        gpu = best_gpu()
        return f"DirectML ({gpu.name})" if gpu else "DirectML (GPU)"
    if key == "cpu":
        return "CPU"
    return key or "CPU"
