import logging

from PySide6.QtCore import QtMsgType

from ui.helpers.app_setup import _qt_msg_filter


def test_fatal_qt_message_is_flushed_to_session_log(tmp_path):
    path = tmp_path / "session.log"
    logger = logging.getLogger("olegpainter.qt")
    handler = logging.FileHandler(path, encoding="utf-8")
    logger.addHandler(handler)
    try:
        _qt_msg_filter(QtMsgType.QtFatalMsg, None, "QThread: Destroyed while thread is still running")
        # Read before closing the handler: fatal termination leaves no cleanup.
        assert "QThread: Destroyed while thread is still running" in path.read_text(encoding="utf-8")
        _qt_msg_filter(QtMsgType.QtFatalMsg, None, "QPainter::fatal diagnostic")
        assert "QPainter::fatal diagnostic" in path.read_text(encoding="utf-8")
    finally:
        logger.removeHandler(handler)
        handler.close()


def test_known_harmless_warning_remains_filtered(caplog):
    with caplog.at_level(logging.DEBUG, logger="olegpainter.qt"):
        _qt_msg_filter(QtMsgType.QtWarningMsg, None, "QPainter::harmless warning")
        _qt_msg_filter(QtMsgType.QtWarningMsg, None, "unhandled Qt warning")
    assert [record.message for record in caplog.records if record.name == "olegpainter.qt"] == [
        "unhandled Qt warning"
    ]
