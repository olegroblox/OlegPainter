"""Installer mode of a downloaded version (UPDATE-001).

The previous copy starts `OlegPainter.exe --install-update <folder> --wait-pid <pid>`
from the unpacked update and closes itself. This copy waits for it, swaps the program
files in <folder> (application.updates.install_into puts them back on failure) and
starts the program there. A small window says what is going on: the old window is
already gone, and without it a slow swap would look like nothing happened.
"""
from __future__ import annotations

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from application import updates

log = logging.getLogger(__name__)


def _start(exe: Path, *args: str) -> None:
    subprocess.Popen([str(exe), *args], cwd=str(exe.parent), close_fds=True,
                     creationflags=getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0))


def run(argv) -> int:
    options = updates.parse_flag_args(list(argv), updates.INSTALL_FLAG)
    if options is None:
        return 2
    target = Path(options["path"])
    source = Path(sys.executable).resolve().parent
    version = options["version"] or "?"

    from PySide6.QtCore import QTimer, Qt
    from PySide6.QtWidgets import QApplication, QLabel, QMessageBox, QProgressBar, QVBoxLayout, QWidget

    app = QApplication(sys.argv[:1])
    window = QWidget()
    window.setWindowTitle("OlegPainter")
    window.setWindowFlag(Qt.WindowStaysOnTopHint, True)
    layout = QVBoxLayout(window)
    label = QLabel(f"Обновляем OlegPainter до версии {version}…\nUpdating OlegPainter to {version}…")
    bar = QProgressBar()
    bar.setRange(0, 0)                       # busy: the swap reports no steps
    layout.addWidget(label)
    layout.addWidget(bar)
    window.resize(420, 110)
    window.show()

    result: dict = {}

    def work():
        try:
            if not updates.wait_for_exit(options["wait_pid"], 120):
                raise updates.UpdateError("Прежняя версия не закрылась. Закройте OlegPainter и запустите обновление снова.")
            updates.install_into(source, target)
            result["ok"] = True
        except Exception as error:                     # every failure leaves the old version in place
            log.exception("Update install failed")
            result["error"] = str(error)

    thread = threading.Thread(target=work, name="update-install", daemon=True)
    thread.start()

    def finish():
        if thread.is_alive():
            return
        timer.stop()
        window.hide()
        exe = target / updates.EXE
        if result.get("ok"):
            # unpacked/OlegPainter → the update folder the finished copy cleans up
            _start(exe, updates.FINISHED_FLAG, str(source.parent.parent), "--wait-pid", str(os.getpid()), "--version", version)
            app.quit()
            return
        QMessageBox.critical(None, "OlegPainter",
                             "Не удалось обновить OlegPainter.\n\n" + result.get("error", "")
                             + "\n\nНовую версию можно скачать вручную: " + updates.RELEASES_PAGE)
        if exe.is_file():
            _start(exe)                                  # the previous version, as it was
        app.quit()

    timer = QTimer()
    timer.timeout.connect(finish)
    timer.start(200)
    app.exec()
    return 0 if result.get("ok") else 1
