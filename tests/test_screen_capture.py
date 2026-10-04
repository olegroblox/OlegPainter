"""Screen tools must not contaminate calibration or remain hidden in screenshots."""
import pytest

from infrastructure.screen_capture import CaptureError, ScreenCapture


class Backend:
    def __init__(self):
        self.affinity = {10: 0, 20: 1}
        self.restored = []
        self.flushes = 0

    def exclude(self, handle):
        previous = self.affinity[handle]
        self.affinity[handle] = 17
        return previous

    def restore(self, handle, previous):
        self.restored.append(handle)
        self.affinity[handle] = previous

    def flush(self):
        self.flushes += 1


def test_capture_excludes_all_tools_and_restores_previous_modes():
    backend = Backend()
    calls = []
    def grab(**kwargs):
        assert backend.affinity == {10: 17, 20: 17}
        calls.append(kwargs)
        return "pixels below the tools"
    capture = ScreenCapture(backend, grab)
    capture.register("guide", 10)
    capture.register("hud", 20)
    assert capture.grab(bbox=(-1200, -400, -1199, -399)) == "pixels below the tools"
    assert calls == [{"bbox": (-1200, -400, -1199, -399), "all_screens": True}]
    assert backend.affinity == {10: 0, 20: 1}
    assert backend.flushes == 2


def test_failed_grab_restores_every_tool():
    backend = Backend()
    def fail(**kwargs):
        raise OSError("capture unavailable")
    capture = ScreenCapture(backend, fail)
    capture.register("guide", 10)
    capture.register("hud", 20)
    with pytest.raises(OSError, match="capture unavailable"):
        capture.grab()
    assert backend.affinity == {10: 0, 20: 1}


def test_failed_exclusion_never_returns_contaminated_pixels():
    backend = Backend()
    def exclude(handle):
        raise CaptureError("cannot exclude")
    backend.exclude = exclude
    capture = ScreenCapture(backend, lambda **kwargs: pytest.fail("unsafe capture"))
    capture.register("guide", 10)
    with pytest.raises(CaptureError, match="cannot exclude"):
        capture.grab()


def test_native_window_recreation_during_capture_discards_image():
    backend = Backend()
    def grab(**kwargs):
        capture.unregister("guide")
        capture.register("guide", 30)
        return "possibly contaminated"
    capture = ScreenCapture(backend, grab)
    capture.register("guide", 10)
    with pytest.raises(CaptureError, match="changed during capture"):
        capture.grab()
    assert backend.affinity[10] == 0


def test_failed_restore_attempts_remaining_windows_and_reports_error():
    backend = Backend()
    def restore(handle, previous):
        backend.restored.append(handle)
        raise CaptureError("restore failed")
    backend.restore = restore
    capture = ScreenCapture(backend, lambda **kwargs: "pixels")
    capture.register("guide", 10)
    capture.register("hud", 20)
    with pytest.raises(CaptureError, match="Cannot restore screen tools"):
        capture.grab()
    assert set(backend.restored) == {10, 20}


def test_hidden_and_destroyed_tools_are_not_touched():
    capture = ScreenCapture(Backend(), lambda **kwargs: "pixels")
    capture.register("guide", 30)  # absent from native backend
    capture.unregister("guide")
    capture.unregister("guide")
    assert capture.grab() == "pixels"


def test_partial_exclusion_failure_restores_already_changed_window():
    backend = Backend()
    original = backend.exclude
    excluded = []
    def exclude(handle):
        if excluded:
            raise CaptureError("second window failed")
        excluded.append(handle)
        return original(handle)
    backend.exclude = exclude
    capture = ScreenCapture(backend, lambda **kwargs: pytest.fail("contaminated capture"))
    capture.register("a", 10)
    capture.register("b", 20)
    with pytest.raises(CaptureError, match="second window failed"):
        capture.grab()
    assert backend.affinity == {10: 0, 20: 1}
    assert backend.restored == excluded


def test_qt_registration_tracks_show_hide_and_destruction(monkeypatch):
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtGui import QGuiApplication
    from PySide6.QtWidgets import QApplication, QWidget
    from ui.overlays.capture_registration import register_screen_tool
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(QGuiApplication, "platformName", lambda: "windows")
    capture = ScreenCapture(Backend(), lambda **kwargs: "unused")
    window = QWidget()
    register_screen_tool(window, capture)
    assert not capture._windows
    window.show()
    app.processEvents()
    assert list(capture._windows.values()) == [int(window.effectiveWinId())]
    window.hide()
    assert not capture._windows
    window.show()
    app.processEvents()
    assert capture._windows
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
    assert not capture._windows
