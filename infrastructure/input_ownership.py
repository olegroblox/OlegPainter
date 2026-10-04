"""Exclusive lifetime of an automation producer inside this process.

Acquisition never waits for another producer to finish. Cleanup is serialized
with acquisition so a late stop cannot release a new producer's buttons.
Native event transport and target-window validation are separate responsibilities.
"""
from contextlib import contextmanager
from dataclasses import dataclass
from threading import RLock, get_ident


class InputBusyError(RuntimeError):
    pass


@dataclass(frozen=True)
class InputLease:
    owner: object
    purpose: str
    thread_id: int


class InputOwnership:
    def __init__(self):
        self._lock = RLock()
        self._lease = None
        self._fault = ""

    @property
    def current(self):
        with self._lock:
            return self._lease

    def acquire(self, owner, purpose):
        """Reserve across asynchronous callbacks; release only this exact token."""
        lease = InputLease(owner, purpose, get_ident())
        if not self._lock.acquire(blocking=False):
            raise InputBusyError("Ввод ещё освобождается. Дождитесь завершения операции.")
        try:
            if self._fault:
                raise InputBusyError("Не удалось освободить ввод. Нажмите остановку для повторного освобождения. " + self._fault)
            if self._lease is not None:
                raise InputBusyError(f"Ввод занят: {self._lease.purpose}. Дождитесь завершения операции.")
            self._lease = lease
        finally:
            self._lock.release()
        return lease

    def release(self, lease):
        with self._lock:
            if self._lease is lease:
                self._lease = None

    @contextmanager
    def claim(self, owner, purpose):
        lease = self.acquire(owner, purpose)
        try:
            yield lease
        finally:
            self.release(lease)

    def cleanup(self, owner, release):
        """Allow idle cleanup or the active owner's stop, never foreign cleanup."""
        with self._lock:
            if self._lease is not None and self._lease.owner is not owner:
                return False
            idle = self._lease is None
            if idle:
                self._lease = InputLease(owner, "освобождение ввода", get_ident())
            try:
                release()
                self._fault = ""
                return True
            except Exception as error:
                self._fault = str(error) or type(error).__name__
                raise
            finally:
                if idle:
                    self._lease = None


automation_input = InputOwnership()
