"""Bind a run to a native window and reject commands after its context changes."""
from dataclasses import dataclass
import os
import time


class TargetChanged(RuntimeError):
    pass


@dataclass(frozen=True)
class WindowIdentity:
    handle: int
    thread_id: int
    process_id: int
    class_name: str


class WindowsTargetAPI:
    def __init__(self):
        import win32gui
        import win32process
        self.gui = win32gui
        self.process = win32process

    def root(self, handle):
        return self.gui.GetAncestor(handle, 2) if handle else 0

    def owner_root(self, handle):
        return self.gui.GetAncestor(handle, 3) if handle else 0

    def at(self, point):
        return self.gui.WindowFromPoint(tuple(map(int, point)))

    def identity(self, handle):
        thread, process = self.process.GetWindowThreadProcessId(handle)
        return WindowIdentity(handle, thread, process, self.gui.GetClassName(handle))

    def rect(self, handle):
        return self.gui.GetWindowRect(handle)

    def available(self, handle):
        return self.gui.IsWindow(handle) and self.gui.IsWindowVisible(handle) and not self.gui.IsIconic(handle)

    def foreground(self):
        return self.gui.GetForegroundWindow()

    def cursor(self):
        return self.gui.GetCursorPos()

    def focus(self, handle):
        self.gui.SetForegroundWindow(handle)


class TargetWindowGuard:
    """Решение владельца 2026-09-28 (INPUT-SIMPLE-001): выбранные координаты
    используются как есть. Окно под областью рисования активируется при старте;
    перемещение окна, точки вне него и смена фокуса ввод не прерывают. Останавливает
    только закрытое/свёрнутое окно цели и область поверх собственного окна."""

    # A window cannot close and reopen with the same identity faster than this;
    # checking on every pointer move cost 0.66 s of a 10 s drawing.
    RECHECK_AFTER = 0.1

    def __init__(self, api=None, own_pid=None):
        self.api = WindowsTargetAPI() if api is None else api
        self.own_pid = os.getpid() if own_pid is None else own_pid
        self.target = None
        self._alive_at = None

    def bind(self, region, *, focus=True):
        self.target = None
        self._alive_at = None
        if not region or len(region) != 4 or region[2] <= 0 or region[3] <= 0:
            raise TargetChanged("Не задана область рисования.")
        x, y, width, height = region
        handle = self.api.root(self.api.at((int(x + width / 2), int(y + height / 2))))
        if not handle:
            return
        target = self.api.identity(handle)
        if target.process_id == self.own_pid:
            raise TargetChanged("Область рисования закрыта окном OlegPainter. Сдвиньте его или переключитесь в программу для рисования.")
        if target.class_name in {"Progman", "WorkerW", "Shell_TrayWnd"}:
            return
        self.target = target
        if focus and not self._belongs(self.api.foreground()):
            try:
                self.api.focus(handle)
            except Exception:
                pass

    def _belongs(self, handle):
        if not handle or self.target is None:
            return False
        root = self.api.root(handle)
        identity = self.api.identity(root)
        return identity.process_id == self.target.process_id and (
            root == self.target.handle or self.api.owner_root(root) == self.target.handle
        )

    def check(self, point=None):
        target = self.target
        if target is None:
            return
        now = time.perf_counter()
        if self._alive_at is not None and now - self._alive_at < self.RECHECK_AFTER:
            return
        try:
            alive = self.api.available(target.handle) and self.api.identity(target.handle) == target
        except Exception:
            alive = False
        self._alive_at = now if alive else None
        if not alive:
            raise TargetChanged("Окно рисования закрыто или свёрнуто. Разверните его и запустите заново.")
