"""One running OlegPainter per Windows user (STARTUP-001).

Two copies would own two global keyboard hooks and two input owners: F3/F4
would fire twice and both could move the real mouse. A second launch asks the
first one to show its window and exits. A local socket (named pipe on Windows)
disappears with its process, so a crashed copy never blocks the next start.
"""
from __future__ import annotations

import getpass
import hashlib

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QLocalServer, QLocalSocket

_ACTIVATE = b"activate\n"


def default_name(application: str = "OlegPainter") -> str:
    try:
        user = getpass.getuser()
    except Exception:
        user = "user"
    return f"{application}-{hashlib.sha1(user.encode('utf-8', 'replace')).hexdigest()[:12]}"


class SingleInstance(QObject):
    """`acquire()` is True for the first copy, which then emits `activationRequested`
    whenever another launch knocks; False means another copy is already running."""

    activationRequested = Signal()

    def __init__(self, name: str | None = None, parent=None):
        super().__init__(parent)
        self.name = name or default_name()
        self._server = None

    def acquire(self, timeout_ms: int = 400) -> bool:
        probe = QLocalSocket()
        probe.connectToServer(self.name)
        if probe.waitForConnected(timeout_ms):
            probe.write(_ACTIVATE)
            probe.flush()
            probe.waitForBytesWritten(timeout_ms)
            probe.disconnectFromServer()
            return False
        server = QLocalServer(self)
        server.setSocketOptions(QLocalServer.UserAccessOption)
        if not server.listen(self.name):
            # A stale endpoint left by an abnormal exit: nobody answered above.
            QLocalServer.removeServer(self.name)
            if not server.listen(self.name):
                # Starting is more important than exclusivity; keep running unguarded.
                return True
        server.newConnection.connect(self._knock)
        self._server = server
        return True

    def _knock(self):
        # The connection itself is the request: the knocking copy may already have
        # written, disconnected and exited before this event loop reads anything.
        knocked = False
        while self._server is not None and self._server.hasPendingConnections():
            connection = self._server.nextPendingConnection()
            connection.disconnected.connect(connection.deleteLater)
            connection.abort()
            knocked = True
        if knocked:
            self.activationRequested.emit()

    def release(self):
        if self._server is not None:
            self._server.close()
            self._server = None
