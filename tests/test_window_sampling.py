import sys
from threading import Event, Timer

import numpy as np
import pytest

from infrastructure.automation_target import WindowIdentity
from infrastructure.window_sampling import (
    SampleCancelled, WindowSampleError, WindowSampleRequest, pixel_from_frame, sample_window_pixel,
)


@pytest.fixture
def sample_request():
    return WindowSampleRequest(WindowIdentity(12, 13, 14, "TestWindow"), (-12, -10, 12, 10),
                               (-10, -8, 10, 8), (-3, -2))


class API:
    def __init__(self, sample_request):
        self.sample_request = sample_request
        self.live = True
    def available(self, handle):
        return self.live
    def identity(self, handle):
        return self.sample_request.identity
    def rect(self, handle):
        return self.sample_request.bounds
    def frame_bounds(self, handle):
        return self.sample_request.frame_bounds
    def at(self, point):
        return self.sample_request.identity.handle
    def root(self, handle):
        return handle


def test_request_uses_physical_frame_bounds_and_rejects_replacement(sample_request):
    from dataclasses import replace
    api = API(sample_request)
    assert WindowSampleRequest.at(*sample_request.point, api=api) == sample_request
    api.sample_request = replace(sample_request, identity=replace(sample_request.identity, process_id=15))
    with pytest.raises(WindowSampleError, match="заменено"):
        sample_request.validate(api)


@pytest.mark.parametrize("field", ["bounds", "frame_bounds"])
def test_moved_or_resized_window_invalidates_request(sample_request, field):
    from dataclasses import replace
    api = API(sample_request)
    api.sample_request = replace(sample_request, **{field: (-11, -8, 10, 8)})
    with pytest.raises(WindowSampleError, match="переместилось"):
        sample_request.validate(api)


def test_point_outside_visible_frame_is_not_clamped(sample_request):
    from dataclasses import replace
    invalid = replace(sample_request, point=(-11, -2))
    with pytest.raises(WindowSampleError, match="вне"):
        invalid.validate(API(invalid))


def test_closed_window_rejects_sample(sample_request):
    api = API(sample_request)
    api.live = False
    with pytest.raises(WindowSampleError, match="закрыто"):
        sample_request.validate(api)


def test_transparent_pixel_is_not_reported_as_black(sample_request):
    with pytest.raises(WindowSampleError, match="прозрачен"):
        pixel_from_frame(np.zeros((16, 20, 4), dtype=np.uint8), sample_request)


def test_frame_mapping_respects_negative_origin_and_bgra_order(sample_request):
    frame = np.zeros((16, 20, 4), dtype=np.uint8)
    frame[6, 7] = (20, 40, 60, 255)
    assert pixel_from_frame(frame, sample_request) == (60, 40, 20)
    with pytest.raises(WindowSampleError, match="Размер"):
        pixel_from_frame(frame[:-1], sample_request)


def test_child_sample_returns_valid_rgb(sample_request):
    command = [sys.executable, "-c", 'import sys; sys.stdin.read(); print(\'{"rgb":[0,255,12]}\')']
    assert sample_window_pixel(sample_request, Event(), command=command) == (0, 255, 12)


@pytest.mark.parametrize("entry", ["main.py", "quick_main.py"])
def test_application_dispatches_worker_before_gui_initialization(entry):
    import json
    import subprocess
    result = subprocess.run([sys.executable, entry, "--window-pixel-worker"],
                            input=b"{}", capture_output=True, timeout=5)
    assert result.returncode == 1
    assert "error" in json.loads(result.stdout)


@pytest.mark.parametrize("reply", ['{"rgb":[true,0,1]}', '{"rgb":[256,0,1]}', '[]', 'not json'])
def test_invalid_worker_result_is_an_error(sample_request, reply):
    command = [sys.executable, "-c", "import sys; sys.stdin.read(); print(" + repr(reply) + ")"]
    with pytest.raises(WindowSampleError):
        sample_window_pixel(sample_request, Event(), command=command)


@pytest.mark.parametrize("cancel", [False, True])
def test_hung_native_worker_is_reaped_on_timeout_or_cancel(sample_request, monkeypatch, cancel):
    from infrastructure import window_sampling
    real_popen = window_sampling.subprocess.Popen
    processes = []
    def start(*args, **kwargs):
        process = real_popen(*args, **kwargs)
        processes.append(process)
        return process
    monkeypatch.setattr(window_sampling.subprocess, "Popen", start)
    cancelled = Event()
    timer = Timer(.15, cancelled.set)
    if cancel:
        timer.start()
    try:
        with pytest.raises(SampleCancelled if cancel else WindowSampleError):
            sample_window_pixel(sample_request, cancelled, timeout=2 if cancel else .15,
                                command=[sys.executable, "-c", "import time; time.sleep(60)"])
    finally:
        timer.cancel()
    assert len(processes) == 1 and processes[0].poll() is not None
