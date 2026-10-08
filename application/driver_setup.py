"""Installing, repairing and removing the Interception driver from the program (DRIVER-002).

The window asks first (warnings and boxes to tick), then this center downloads the
author's installer if none is at hand, runs it as administrator, reads the result
from Windows and says what is next — usually a restart, which it can schedule and
cancel. Qt-free: the presenter passes `notify` and marshals it to the GUI.
"""
from __future__ import annotations

import datetime
import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Callable

from infrastructure import input_driver
from infrastructure.input_driver import DriverError

log = logging.getLogger(__name__)

ACTIONS = ("install", "uninstall", "repair")
RESTART_DELAY = 60
# dev.ps1 fresh nodriver: the program as on a computer in this state; Windows is not touched.
PREVIEW_STATES = ("missing", "reboot", "blocked", "unreachable", "incomplete", "broken", "no_mouse", "pending_removal")
_PREVIEW = "Это просмотр (OLEGPAINTER_DRIVER_PREVIEW): драйвер и компьютер не меняются."
_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_BROKEN_AFTER = ("Драйвер по-прежнему повреждён. Не перезагружайте компьютер: попробуйте ещё раз или удалите "
                 "драйвер установщиком со страницы автора. Возможно, его файлы удаляет антивирус.")


def _start_thread(target, name):
    threading.Thread(target=target, name=name, daemon=True).start()


class DriverCenter:
    def __init__(self, app_root: Path, *, notify: Callable[[], None] = lambda: None, check=None, fetch=None,
                 run=None, command=None, launch=None, opener=None, start=None):
        self.app_root = Path(app_root)
        self._notify = notify
        preview = os.environ.get("OLEGPAINTER_DRIVER_PREVIEW", "").strip()
        self.preview = check is None and preview in PREVIEW_STATES
        if self.preview:
            check = lambda: input_driver.DriverStatus(preview, detail="preview")  # noqa: E731
        self._check = check or (lambda: input_driver.check(self.app_root))
        self._fetch = fetch or (lambda progress, cancelled: input_driver.fetch_installer(
            progress=progress, cancelled=cancelled))
        self._run = run or input_driver.run_installer
        # shutdown answers at once; the windows opened for the user must not block this one
        self._command = command or (lambda args: subprocess.run(args, creationflags=_NO_WINDOW, check=False,
                                                                capture_output=True))
        self._launch = launch or (lambda args: subprocess.Popen(args, close_fds=True))
        # ShellExecute: Windows asks for administrator rights where the target needs them
        self._open = opener or getattr(os, "startfile", None)
        self._start = start or _start_thread
        self._lock = threading.RLock()
        self.status = self._check()
        self.busy = ""                 # "" | install | uninstall | repair
        self.progress = 0.0
        self.message = ""
        self.error = False
        self.restart_scheduled = False

    # ----- view -------------------------------------------------------------------------
    def snapshot(self) -> dict:
        with self._lock:
            status = self.status
            when = (datetime.datetime.fromtimestamp(status.installed_at).strftime("%d.%m.%Y")
                    if status.installed_at else "")
            return dict(state=status.state, ready=status.ready, installed=status.installed,
                        location=status.location, custom_location=status.custom_location, renamed=status.renamed,
                        version=status.version, installed_at=when, has_files=any(part.file for part in status.parts),
                        hvci=status.hvci, anticheats=list(status.anticheats), users=list(status.users),
                        leftovers=status.leftovers, keyboards=status.keyboards, mice=status.mice,
                        unsupported_arch=status.detail == "arch", has_installer=bool(status.installer),
                        busy=self.busy, progress=self.progress, message=self.message, error=self.error,
                        restart_scheduled=self.restart_scheduled)

    def _set(self, **changes) -> None:
        with self._lock:
            for key, value in changes.items():
                setattr(self, key, value)
        self._notify()

    # ----- commands ---------------------------------------------------------------------
    def recheck(self) -> str:
        if self.busy:
            return self.status.state           # the running job reads Windows when it ends
        self._set(status=self._check())
        return self.status.state

    def change(self, action: str) -> bool:
        """install / uninstall / repair in the background; False while busy."""
        if action not in ACTIONS:
            raise ValueError("Неизвестное действие с драйвером.")
        with self._lock:
            if self.busy:
                return False
            if self.preview:
                raise DriverError(_PREVIEW)
            if self.status.state == "unsupported":
                raise DriverError("На этом компьютере драйвер Interception не работает.")
            if action != "uninstall" and self.status.hvci:
                # Its INIT section is writable and executable: with Memory Integrity Windows
                # refuses to load it, and a class filter that cannot load leaves the keyboard
                # and mouse without a driver after the restart.
                raise DriverError("Включена «Целостность памяти»: с ней Windows не загрузит Interception, а мышь "
                                  "и клавиатура могут перестать работать. Драйвер не установлен.")
            self.busy, self.progress, self.error = action, 0.0, False
            self.message = "Готовим установщик драйвера…"
        self._notify()
        self._start(lambda: self._work(action), f"driver-{action}")
        return True

    def _work(self, action: str) -> None:
        before = self.status
        try:
            installer = before.installer
            if not installer:
                self._set(message="Скачиваем официальный установщик с GitHub автора…")
                installer = str(self._fetch(lambda done, total: self._set(progress=min(1.0, done / total) if total else 0.0),
                                            lambda: False))
            self._set(progress=1.0, message="Windows спросит разрешение администратора — нажмите «Да».")
            code = self._run(installer, "uninstall" if action == "uninstall" else "install")
            after = self._check()
        except DriverError as error:
            self._set(busy="", message=str(error), error=True, status=self._check())
            return
        except Exception as error:                        # never leave the window busy
            log.exception("Driver %s failed", action)
            self._set(busy="", message=f"Не удалось изменить драйвер: {error}", error=True, status=self._check())
            return
        log.info("Driver %s: exit code %s, state %s -> %s", action, code, before.state, after.state)
        if after.state == "broken":
            done, message = False, _BROKEN_AFTER
        elif action == "uninstall":
            done = after.state in ("missing", "pending_removal")
            message = ("Драйвер удалён. Он работает до перезагрузки компьютера — после неё рисовать будет нельзя."
                       if after.state == "pending_removal" else
                       "Драйвер удалён. Перезагрузите компьютер, чтобы он выгрузился." if done else
                       f"Установщик закончил работу (код {code}), но драйвер остался. Попробуйте ещё раз "
                       "или удалите его установщиком со страницы автора.")
        else:
            done = after.state in ("reboot", "ready", "no_mouse")
            message = ("Драйвер установлен. Перезагрузите компьютер — после этого OlegPainter сможет рисовать."
                       if after.state == "reboot" else
                       "Драйвер установлен и уже работает." if after.state == "ready" else
                       "Драйвер установлен и работает, но не видит мышь — переподключите её." if done else
                       f"Установщик закончил работу (код {code}), но драйвер не появился. Попробуйте ещё раз "
                       "или установите его вручную со страницы автора.")
        self._set(busy="", status=after, message=message, error=not done)

    def schedule_restart(self, seconds: int = RESTART_DELAY) -> None:
        if self.preview:
            raise DriverError(_PREVIEW)
        # Windows shows the comment in its own language-neutral notice.
        result = self._command(["shutdown", "/r", "/t", str(int(seconds)), "/c", "OlegPainter — Interception"])
        code = getattr(result, "returncode", 0)
        if code == 1190:                                  # ERROR_SHUTDOWN_IS_SCHEDULED
            raise DriverError("Перезагрузка уже запланирована — возможно, Центром обновления Windows.")
        if code != 0:
            raise DriverError(f"Windows не приняла перезагрузку (код {code}). Перезагрузите компьютер через меню «Пуск».")
        self._set(restart_scheduled=True, error=False,
                  message=f"Компьютер перезагрузится через {int(seconds)} секунд. Сохраните работу в других программах.")

    def cancel_restart(self) -> None:
        result = self._command(["shutdown", "/a"])
        code = getattr(result, "returncode", 0)
        if code not in (0, 1116):                         # 1116: nothing to cancel any more
            raise DriverError(f"Windows не отменила перезагрузку (код {code}).")
        self._set(restart_scheduled=False, message="Перезагрузка отменена.", error=False)

    def show_files(self) -> None:
        path = next((part.path for part in self.status.parts if part.file), "")
        if not path:
            raise DriverError("Файлов драйвера на компьютере нет.")
        self._launch(["explorer", "/select,", path])

    def _shell(self, target: str, failure: str) -> None:
        if self._open is None:
            raise DriverError(failure)
        try:
            self._open(target)
        except OSError as error:
            raise DriverError(failure) from error

    def open_core_isolation(self) -> None:
        self._shell("windowsdefender://coreisolation",
                    "Откройте «Безопасность Windows» → «Безопасность устройства» → «Изоляция ядра».")

    def open_restore_points(self) -> None:
        self._shell("SystemPropertiesProtection.exe",
                    "Откройте «Панель управления» → «Восстановление» → «Настройка восстановления системы».")
