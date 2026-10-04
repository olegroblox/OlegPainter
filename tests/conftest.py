"""Keep Qt tests headless and their persistent state away from user sessions."""
import os
import tempfile

os.environ["QT_QPA_PLATFORM"] = "offscreen"

# Set before collection: some test modules create Qt objects at import time.
# Keep the TemporaryDirectory alive until interpreter shutdown, including Qt cleanup.
_test_state = tempfile.TemporaryDirectory(prefix="olegpainter-tests-")
os.environ["OLEGPAINTER_CONFIG_DIR"] = os.path.join(_test_state.name, "configs")
# Telemetry keeps only the newest 100 sessions: test drawings would push the
# owner's real diagnostics out of %LOCALAPPDATA%.
os.environ["OLEGPAINTER_TELEMETRY_DIR"] = os.path.join(_test_state.name, "telemetry")

from PySide6.QtCore import QSettings

QSettings.setDefaultFormat(QSettings.Format.IniFormat)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, _test_state.name)
QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.SystemScope, _test_state.name)


# Each test represents an independent process lifetime, including recovery from
# deliberately injected device-release failures. Keep the real coordinator.
import pytest


@pytest.fixture(autouse=True)
def quick_start_already_seen():
    # Suites exercise the drawing page; the first-launch quick start is opted into per test.
    QSettings().setValue("quick/quickStartSeen", True)
    QSettings().remove("quick/targetChosen")  # derived from quickStartSeen unless a test chooses
    QSettings().remove("quick/advancedSettings")  # newcomers see «Основное» only
    QSettings().setValue("image/autoColors", False)  # suites keep the colour count they set
    from application.support import APP_VERSION
    QSettings().setValue("quick/whatsNewSeen", APP_VERSION)  # a modal «Что нового» would block clicks
    yield


@pytest.fixture(autouse=True)
def independent_input_ownership(monkeypatch):
    from infrastructure.input_ownership import InputOwnership
    from infrastructure import automation_transport, capture_session
    from tests.helpers.input_transport import RecordingInputBackend
    from engine.olegpainter import input_emulation
    ownership = InputOwnership()
    monkeypatch.setattr(input_emulation, "automation_input", ownership)
    monkeypatch.setattr(capture_session, "automation_input", ownership)
    monkeypatch.setattr(automation_transport, "WindowsInputBackend", RecordingInputBackend)
