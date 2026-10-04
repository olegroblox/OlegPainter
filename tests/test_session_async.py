"""Persistence races use real writers and real documents in isolated directories."""
import threading
import time

from PIL import Image
import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QTimer
from PySide6.QtWidgets import QApplication

from application.controller import ApplicationController
from application.session_image import SessionImage
from infrastructure.documents import read_document
from ui.helpers.config_store import ConfigStore
from ui.services.painter_service import PainterService


def pump(predicate):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        QApplication.processEvents()
        if predicate():
            return
        time.sleep(0.002)
    assert predicate()


@pytest.fixture
def controller(tmp_path):
    app = QApplication.instance() or QApplication([])
    service = PainterService()
    service._rebuild_preview_if_possible = lambda: None
    service._rebuild_preview_strict = lambda: None
    owner = ApplicationController(service, config_store=ConfigStore(tmp_path))
    yield owner
    owner.close(save=False)
    owner.deleteLater()
    service.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    app.processEvents()


def source(owner, color):
    owner.service.engine.source_pil_image = Image.new("RGBA", (30, 20), color)
    owner.service._set_session_image_source("clipboard_cache")
    owner.session.mark_dirty("image")


def saved_pixel(owner):
    doc = read_document(owner.session.store.session_path)
    path = owner.session.store.base_dir / doc["payload"]["session"]["image"]["cache_relpath"]
    with Image.open(path) as image:
        return image.getpixel((0, 0))


def test_writer_owns_pixels_and_coalesces_pending_state_without_losing_new_edits(controller, monkeypatch):
    session = controller.session
    releases = [threading.Event(), threading.Event()]
    entered, workers, ticks = [], [], []
    write = session.store.write_autosave
    def delayed(document, image):
        index = len(entered)
        entered.append(document["payload"]["painter"]["brush_size"])
        workers.append(threading.get_ident())
        assert releases[index].wait(3)
        return write(document, image)
    monkeypatch.setattr(session.store, "write_autosave", delayed)
    timer = QTimer()
    timer.setInterval(5)
    timer.timeout.connect(lambda: ticks.append(1))
    try:
        source(controller, "red")
        controller.service.set_brush_size(3)
        timer.start()
        started = time.monotonic()
        first = session.request_save()
        assert time.monotonic() - started < 0.2
        pump(lambda: bool(entered) and len(ticks) >= 4)
        # Mutation of the original cannot alter the active writer's copy.
        controller.service.engine.source_pil_image.paste("blue", (0, 0, 30, 20))
        controller.service.set_brush_size(5)
        second = session.request_save()
        source(controller, "lime")
        controller.service.set_brush_size(7)
        third = session.request_save()
        assert second is third and first is not second
        releases[0].set()
        pump(lambda: first.done() and len(entered) == 2)
        assert first.result()
        assert saved_pixel(controller) == (255, 0, 0, 255)
        assert session._dirty_sections  # first completion cannot clear new data
        assert controller.service._session_image_png_cache is None
        releases[1].set()
        pump(third.done)
        assert third.result()
        assert entered == [3, 7]  # intermediate waiting snapshot was superseded
        assert all(worker != threading.get_ident() for worker in workers)
        assert saved_pixel(controller) == (0, 255, 0, 255)
        assert not session._dirty_sections and session._active is None
    finally:
        for release in releases:
            release.set()
        timer.stop()


def test_png_failure_preserves_saved_document_dirty_data_and_retry(controller, monkeypatch):
    source(controller, "red")
    controller.save()
    before = controller.session.store.session_path.read_bytes()
    source(controller, "lime")
    errors = []
    controller.errorOccurred.connect(errors.append)
    encode = SessionImage.encode
    def fail(_):
        raise OSError("PNG encoder unavailable")
    monkeypatch.setattr(SessionImage, "encode", fail)
    failed = controller.session.request_save()
    pump(failed.done)
    with pytest.raises(OSError, match="PNG encoder unavailable"):
        failed.result()
    assert errors == ["PNG encoder unavailable"]
    assert controller.session.store.session_path.read_bytes() == before
    assert controller.session._dirty_sections
    monkeypatch.setattr(SessionImage, "encode", encode)
    assert controller.save()
    assert saved_pixel(controller) == (0, 255, 0, 255)


def test_close_waits_for_latest_snapshot_without_blocking_or_losing_state(controller, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    write = controller.session.store.write_autosave
    calls = []
    def delayed(document, image):
        calls.append(document["payload"]["painter"]["brush_size"])
        entered.set()
        assert release.wait(3)
        return write(document, image)
    monkeypatch.setattr(controller.session.store, "write_autosave", delayed)
    try:
        controller.service.set_brush_size(3)
        first = controller.session.request_save()
        pump(entered.is_set)
        controller.service.set_brush_size(9)
        controller.service.engine.begin_drawing_run(17)  # start has not published RUNNING yet
        started = time.monotonic()
        assert not controller.close(wait=False)
        assert controller.service.engine._automation_cancelled()
        assert time.monotonic() - started < 0.2
        assert controller._close_phase == "saving"
        assert controller.service._close_pending
        assert not controller.state.can_edit_source
        assert not controller.close(wait=False)
        release.set()
        pump(lambda: controller._closed)
        assert first.result()
        assert calls == [3, 9]
        assert controller.session._active is None
        assert read_document(controller.session.store.session_path)["payload"]["painter"]["brush_size"] == 9
    finally:
        release.set()


def test_snapshot_failure_cannot_write_an_empty_successful_session(controller, monkeypatch):
    controller.service.set_brush_size(12)
    controller.save()
    before = controller.session.store.session_path.read_bytes()
    def fail():
        raise RuntimeError("snapshot failed")
    monkeypatch.setattr(controller.service, "snapshot_painter_config", fail)
    with pytest.raises(RuntimeError, match="snapshot failed"):
        controller.save()
    assert controller.session.store.session_path.read_bytes() == before


def test_cancelled_waiting_save_never_writes_and_later_request_remains_usable(controller, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    write = controller.session.store.write_autosave
    calls = []
    def delayed(document, image):
        calls.append(document["payload"]["painter"]["brush_size"])
        entered.set()
        assert release.wait(3)
        return write(document, image)
    monkeypatch.setattr(controller.session.store, "write_autosave", delayed)
    try:
        controller.service.set_brush_size(3)
        first = controller.session.request_save()
        pump(entered.is_set)
        controller.service.set_brush_size(4)
        cancelled = controller.session.request_save()
        assert cancelled.cancel()
        release.set()
        pump(first.done)
        assert calls == [3]
        assert controller.session._active is None
        assert not controller.session._write_timer.isActive()
        assert controller.session._dirty_sections
        assert controller.save()
        assert calls == [3, 4]
    finally:
        release.set()


def test_teardown_error_does_not_reopen_a_partially_closed_controller(controller, monkeypatch):
    shutdown = controller.service.shutdown
    calls = []
    def fail_once(**kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise OSError("cleanup unavailable")
        return shutdown(**kwargs)
    monkeypatch.setattr(controller.service, "shutdown", fail_once)
    assert not controller.close(wait=False)
    pump(lambda: bool(controller._close_error))
    assert controller._close_error == "cleanup unavailable"
    assert controller._closing and controller._close_phase == "stopping"
    assert not controller.state.can_edit_source
    controller.close(wait=False)
    pump(lambda: controller._closed)
    assert len(calls) == 2
