"""Native Qt workflow, virtual hooks and controlled sampling; no desktop I/O."""
from threading import Event

import numpy as np
import pytest
from PySide6.QtCore import QEvent, QCoreApplication, QTimer
from PySide6.QtWidgets import QApplication
from PySide6.QtQuickControls2 import QQuickStyle

from infrastructure.automation_target import WindowIdentity
from infrastructure.palette_sampling import PaletteSampleRequest, palette_from_frame
from ui.helpers.config_store import ConfigStore
from ui.quick.application import QuickApplication
from ui.overlays.region_pick import _BasePickOverlay
from ui.overlays import palette_calibration
from tests.test_quick_presentation import pump


@pytest.fixture
def workflow(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Basic")
    monkeypatch.setattr(_BasePickOverlay, "_start_listeners", lambda _: None)
    request = PaletteSampleRequest(WindowIdentity(1, 2, 3, "Test"), (0, 0, 50, 50),
                                   (0, 0, 50, 50), (12, 12), (10, 10, 4, 4))
    monkeypatch.setattr(PaletteSampleRequest, "from_regions", lambda *_, **__: request)
    class API:
        def available(self, _): return True
        def identity(self, _): return request.identity
        def rect(self, _): return request.bounds
        def frame_bounds(self, _): return request.frame_bounds
    monkeypatch.setattr(palette_calibration, "WindowSampleAPI", API)
    data = np.full((50, 50, 4), 255, np.uint8)
    samples = palette_from_frame(data, request)
    entered, release = Event(), Event()
    def measure(*_):
        entered.set()
        assert release.wait(4)
        return samples
    monkeypatch.setattr(palette_calibration, "sample_window_palette", measure)
    quick = QuickApplication(config_store=ConfigStore(tmp_path / "profiles"), desktop=True)
    assert quick.presenter.setChoice("color_picking_method", "screen_palette")
    previous = dict(lab=np.array([[1., 2., 3.]]), coords=np.array([[4, 5]]), slider=None)
    quick.service.engine.screen_palette_calib = previous
    quick.presenter.refresh()
    yield quick, previous, entered, release
    release.set()
    quick.dispose(save=False)
    assert not quick.qml_warnings
    quick.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def start_measurement(quick):
    assert quick.presenter.action("calibrate_screen_palette")
    overlays = quick.controller.desktop.desktop_overlays
    picker = overlays.picker
    assert not quick.window.isVisible()
    picker._handle_event("down", (10, 10))
    picker._handle_event("up", (14, 14))
    assert overlays.picker is picker and not picker._closed
    # A measurement that fails at once leaves "measure" within one event-loop
    # turn: record the transition instead of polling the current mode.
    seen = []

    def record(mode):
        seen.append(mode)

    quick.service.desktopInteractionChanged.connect(record)
    try:
        picker._handle_event("accept", None)
        pump(lambda: "measure" in seen)
    finally:
        quick.service.desktopInteractionChanged.disconnect(record)
    assert quick.window.isVisible()
    return overlays


def test_palette_measurement_keeps_qt_responsive_publishes_and_roundtrips_config(workflow):
    quick, previous, entered, release = workflow
    overlays = start_measurement(quick)
    pump(entered.is_set)
    ticks = []
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(1))
    timer.start()
    try:
        pump(lambda: len(ticks) >= 4)
        assert quick.service.engine.screen_palette_calib is previous
        assert not quick.presenter.view["can_edit"]
        assert quick.presenter.view["next_step"]["label"] == "Измеряем палитру…"
        release.set()
        pump(lambda: overlays._palette_job is None)
        result = quick.service.engine.screen_palette_calib
        assert len(result["coords"]) == 16
        np.testing.assert_allclose(result["lab"][:, 0], 100, atol=.01)
        config = quick.service.snapshot_painter_config()
        quick.service.engine.screen_palette_calib = None
        assert quick.service.engine.load_config(config)
        np.testing.assert_array_equal(quick.service.engine.screen_palette_calib["coords"], result["coords"])
        assert quick.service.desktop_interaction == ""
    finally:
        timer.stop()


def test_f4_cancels_pending_result_and_keeps_lease_until_worker_exits(workflow):
    from infrastructure.capture_session import automation_input
    quick, previous, entered, release = workflow
    overlays = start_measurement(quick)
    pump(entered.is_set)
    job = overlays._palette_job
    assert quick.presenter.action("stop")
    pump(lambda: job.session.closed)
    assert automation_input.current is not None
    assert quick.service.engine.screen_palette_calib is previous
    release.set()
    pump(lambda: not quick.service._stop_pending and overlays._palette_job is None)
    assert quick.service.engine.screen_palette_calib is previous
    assert automation_input.current is None


def test_cancel_second_region_discards_first_and_restores_window(workflow):
    quick, previous, entered, _ = workflow
    assert quick.presenter.action("calibrate_screen_palette")
    overlays = quick.controller.desktop.desktop_overlays
    picker = overlays.picker
    picker._handle_event("down", (10, 10))
    picker._handle_event("up", (14, 14))
    picker.cancel()
    pump(lambda: overlays.picker is None)
    assert quick.window.isVisible() and not entered.is_set()
    assert quick.service.engine.screen_palette_calib is previous


def test_both_regions_use_one_picker_until_release(workflow, monkeypatch):
    quick, _, _, release = workflow
    accepted = []
    original = PaletteSampleRequest.from_regions
    def capture(*regions):
        accepted.append(regions)
        return original(*regions)
    monkeypatch.setattr(PaletteSampleRequest, "from_regions", capture)
    assert quick.presenter.action("calibrate_screen_palette")
    overlays = quick.controller.desktop.desktop_overlays
    picker = overlays.picker
    session = picker._capture_session
    picker._handle_event("down", (10, 10))
    picker._handle_event("up", (14, 14))
    assert not session.closed and not accepted
    picker._handle_event("down", (30, 10))
    picker._handle_event("accept", None)  # Enter cannot cut a held drag in half.
    assert not accepted
    picker._handle_event("up", (34, 30))
    assert accepted == [((10, 10, 4, 4), (30, 10, 4, 20))]
    assert session.finished.is_set()
    release.set()
    pump(lambda: overlays._palette_job is None)


def test_target_change_before_qt_publication_rejects_result(workflow, monkeypatch):
    quick, previous, entered, release = workflow
    overlays = start_measurement(quick)
    job = overlays._palette_job
    job.timer.stop()
    pump(entered.is_set)
    release.set()
    pump(lambda: not job.thread.is_alive())
    def moved():
        raise RuntimeError("window moved")
    monkeypatch.setattr(palette_calibration, "WindowSampleAPI", moved)
    job._poll()
    assert quick.service.engine.screen_palette_calib is previous
    assert "window moved" in quick.presenter.message


@pytest.mark.parametrize("failure", ["sample", "target", "stale", "thread"])
def test_failed_or_stale_measurement_preserves_existing_calibration(workflow, monkeypatch, failure):
    quick, previous, entered, release = workflow
    def fail(*_, **__):
        raise RuntimeError("calibration failed")
    if failure == "sample":
        monkeypatch.setattr(palette_calibration, "sample_window_palette", fail)
    elif failure == "target":
        monkeypatch.setattr(palette_calibration, "WindowSampleAPI", fail)
    elif failure == "thread":
        from infrastructure.capture_session import CaptureSession
        monkeypatch.setattr(CaptureSession, "start_worker", fail)
    if failure == "thread":
        assert quick.presenter.action("calibrate_screen_palette")
        overlays = quick.controller.desktop.desktop_overlays
        picker = overlays.picker
        picker._handle_event("down", (10, 10))
        picker._handle_event("up", (14, 14))
        picker._handle_event("accept", None)
    else:
        overlays = start_measurement(quick)
    if failure == "stale":
        pump(entered.is_set)
        quick.service.engine.color_picking_method = "hex_field"
    release.set()
    pump(lambda: overlays._palette_job is None)
    assert quick.service.engine.screen_palette_calib is previous
    assert quick.presenter.messageError
    assert quick.window.isVisible()


def test_shutdown_waits_for_measurement_without_publishing(workflow):
    quick, previous, entered, release = workflow
    start_measurement(quick)
    pump(entered.is_set)
    assert not quick.presenter.closeApplication()
    assert quick.controller._closing and not quick.controller._closed
    release.set()
    pump(lambda: quick.controller._closed)
    assert quick.service.engine.screen_palette_calib is previous
