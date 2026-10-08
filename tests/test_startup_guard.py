"""STARTUP-001: one running copy and a visible start failure (the driver: test_input_driver)."""
import time
import uuid
from pathlib import Path

from PySide6.QtWidgets import QApplication

from infrastructure.single_instance import SingleInstance

_app = QApplication.instance() or QApplication([])


def _pump(predicate, seconds=3.0):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        _app.processEvents()
        if predicate():
            return True
        time.sleep(0.01)
    return predicate()


def test_second_launch_asks_the_first_window_to_show_and_exits():
    name = "OlegPainter-test-" + uuid.uuid4().hex[:8]
    first, second = SingleInstance(name), SingleInstance(name)
    knocks = []
    try:
        assert first.acquire()
        first.activationRequested.connect(lambda: knocks.append(True))
        assert not second.acquire()  # the first copy answered: the second must quit
        assert _pump(lambda: knocks)
    finally:
        first.release()
        second.release()
    # After the first copy is gone the name is free again (a crash leaves no lock).
    third = SingleInstance(name)
    try:
        assert third.acquire()
    finally:
        third.release()


def test_second_process_is_turned_away_and_first_window_is_asked_to_show():
    import subprocess
    import sys
    name = "OlegPainter-test-" + uuid.uuid4().hex[:8]
    first = SingleInstance(name)
    knocks = []
    code = ("import sys; from PySide6.QtCore import QCoreApplication; app = QCoreApplication([]);"
            "from infrastructure.single_instance import SingleInstance;"
            f"sys.exit(0 if SingleInstance({name!r}).acquire() else 3)")
    try:
        assert first.acquire()
        first.activationRequested.connect(lambda: knocks.append(True))
        child = subprocess.Popen([sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parents[1]))
        assert _pump(lambda: child.poll() is not None, 20)
        assert child.returncode == 3  # the second copy quits
        assert _pump(lambda: knocks)
    finally:
        first.release()


def test_trial_run_keeps_window_settings_out_of_the_registry(tmp_path):
    """dev.ps1 fresh: with OLEGPAINTER_CONFIG_DIR the window's own settings (quick start
    seen, chosen program) go to that folder too — a first run that changes nothing real."""
    import os
    import subprocess
    import sys
    code = ("import os, sys; sys.path.insert(0, os.getcwd()); import quick_main\n"
            "from PySide6.QtCore import QCoreApplication, QSettings\n"
            "assert quick_main._isolate_trial_settings()\n"
            "app = QCoreApplication([]); app.setOrganizationName('OlegPainter'); app.setApplicationName('OlegPainter')\n"
            "settings = QSettings(); settings.setValue('quick/quickStartSeen', True); settings.sync()\n"
            "print(settings.fileName())\n")
    env = dict(os.environ, OLEGPAINTER_CONFIG_DIR=str(tmp_path / "configs"))
    result = subprocess.run([sys.executable, "-c", code], cwd=str(Path(__file__).resolve().parents[1]), env=env,
                            capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr
    stored = Path(result.stdout.strip().splitlines()[-1])
    assert stored.is_file() and tmp_path in stored.parents


def test_failed_start_shows_a_message_with_the_session_log(monkeypatch):
    import quick_main
    shown = []
    monkeypatch.setattr(quick_main, "session_log_path", lambda: Path("C:/logs/session_1.log"))
    monkeypatch.setattr(quick_main.QMessageBox, "critical", lambda parent, title, text: shown.append(text))
    try:
        raise RuntimeError("Не удалось загрузить интерфейс QML.")
    except RuntimeError as error:
        quick_main._startup_failed(error)
    assert shown and "Не удалось загрузить интерфейс QML." in shown[0]
    assert "session_1.log" in shown[0]


def test_uncaught_exceptions_reach_the_session_log(monkeypatch, caplog):
    import sys
    from ui.helpers import app_setup
    monkeypatch.setattr(sys, "excepthook", lambda *args: None)
    app_setup._log_uncaught_exceptions()
    try:
        raise ValueError("сбой в обработчике")
    except ValueError:
        with caplog.at_level("ERROR"):
            sys.excepthook(*sys.exc_info())
    assert any("Uncaught exception" in record.message and record.exc_info for record in caplog.records)
