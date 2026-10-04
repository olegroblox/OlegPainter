"""Bounded sequential work under a capture lease, separate from native hooks."""
from collections import deque
from threading import Condition


class CaptureWorkQueue:
    def __init__(self, session, sample, publish, finished, failed, *, capacity=8):
        self.session = session
        self.sample, self.publish = sample, publish
        self.finished, self.failed = finished, failed
        self.capacity = capacity
        self._condition = Condition()
        self._pending = deque()
        self._finishing = False
        self.thread = session.start_worker(self._run)

    def submit(self, request):
        with self._condition:
            if self._finishing or self.session.cancelled.is_set():
                return False
            if len(self._pending) >= self.capacity:
                raise RuntimeError("Очередь образцов заполнена. Дождитесь измерения выбранных цветов.")
            self._pending.append(request)
            self._condition.notify()
            return True

    def finish(self):
        self.session.stop_listening()
        with self._condition:
            self._finishing = True
            self._condition.notify()

    def _run(self):
        try:
            while not self.session.cancelled.is_set():
                with self._condition:
                    if not self._pending and not self._finishing:
                        self._condition.wait(.05)
                        continue
                    request = self._pending.popleft() if self._pending else None
                if self.session.cancelled.is_set():
                    return
                if request is None:
                    self.session.publish(self.finished)
                    return
                value = self.sample(request, self.session.cancelled)
                self.session.publish(lambda: self.publish(request, value))
        except Exception as error:
            self.session.publish(lambda error=error: self.failed(error))
        finally:
            with self._condition:
                self._pending.clear()
