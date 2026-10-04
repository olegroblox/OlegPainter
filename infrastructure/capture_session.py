"""Exclusive passive capture with stale-callback invalidation.

Stopping invalidates callbacks immediately. A callback already running keeps the
input lease until it exits; Qt never waits for that callback or joins a hook.
Passive native hooks are stopped after invalidation and cannot deliver further
application callbacks even if their own teardown is delayed.
"""
from threading import Event, RLock, Thread
from functools import wraps
from inspect import signature

from infrastructure.input_ownership import automation_input


def capture_start(method):
    """Roll back a newly acquired session when capture setup itself raises."""
    @wraps(method)
    def start(engine, *args, **kwargs):
        previous = getattr(engine, "_capture_session", None)
        try:
            return method(engine, *args, **kwargs)
        except BaseException:
            current = getattr(engine, "_capture_session", None)
            if current is not None and current is not previous:
                current.close()
            raise
    return start


class CaptureSession:
    def __init__(self, purpose, *, ownership=None, on_error=None):
        self.ownership = automation_input if ownership is None else ownership
        self._lock = RLock()
        self._lease = self.ownership.acquire(self, purpose)
        self._closed = False
        self._listening = True
        self.cancelled = Event()
        self._running = 0
        self._listeners = []
        self._stop_error = None
        self.finished = Event()
        self.on_error = on_error

    @property
    def closed(self):
        return self.cancelled.is_set()

    def wrap(self, callback):
        @wraps(callback)
        def guarded(*args, **kwargs):
            with self._lock:
                if self.closed or not self._listening:
                    return False
                self._running += 1
            try:
                result = callback(*args, **kwargs)
                if result is False:
                    self.close()
                return result
            except BaseException as error:
                try:
                    self.close()
                finally:
                    if self.on_error is not None:
                        self.on_error(error)
                raise
            finally:
                with self._lock:
                    self._running -= 1
                    self._release_if_finished()
        # pynput adapts callback arity (including its optional injected flag) via
        # getfullargspec. Preserve the callable signature, not just __wrapped__.
        guarded.__signature__ = signature(callback)
        return guarded

    def publish(self, callback):
        """Accept a short commit unless cancelled; an accepted commit may finish."""
        with self._lock:
            if self.closed:
                return False
            callback()
            return True

    def start_worker(self, target):
        """Reserve ownership before spawning; release after the worker exits."""
        def run():
            try:
                target()
            finally:
                with self._lock:
                    self._running -= 1
                    self._release_if_finished()
        with self._lock:
            if self.closed:
                raise RuntimeError("Захват уже завершён.")
            self._running += 1
            thread = Thread(target=run, name="OlegPainter capture")
            try:
                thread.start()
            except BaseException:
                self._running -= 1
                self.close()
                raise
        return thread

    def listener(self, factory, **callbacks):
        """Create only passive listeners: every callback goes through this session."""
        with self._lock:
            if self.closed:
                raise RuntimeError("Захват уже завершён.")
            try:
                native = factory(**{name: self.wrap(callback) for name, callback in callbacks.items()})
            except BaseException:
                self.close()
                raise
            listener = CaptureListener(self, native)
            self._listeners.append(listener)
            return listener

    def _release_if_finished(self):
        if (self._closed and not self._running and self._stop_error is None
                and all(listener.stopped for listener in self._listeners) and self._lease is not None):
            self.ownership.release(self._lease)
            self._lease = None
            self.finished.set()

    def stop_listening(self):
        """Stop accepting input while an explicit finish drains accepted work."""
        with self._lock:
            self._listening = False
            self._stop_error = None
            # Stop only raw passive hooks, never re-enter our listener facade.
            # A failed stop keeps the lease and may be explicitly retried.
            for listener in self._listeners:
                if listener.stopped:
                    continue
                try:
                    listener.native.stop()
                    listener.stopped = True
                except Exception as error:
                    self._stop_error = error
            self._release_if_finished()
            if self._stop_error is not None:
                raise RuntimeError("Не удалось остановить захват ввода: " + str(self._stop_error)) from self._stop_error

    def request_cancel(self):
        """Latch cancellation without waiting for an active commit or teardown.

        A commit already accepted by publish may finish; later commits are
        rejected. The stop worker still closes hooks and drains active work.
        """
        self.cancelled.set()

    def close(self):
        self.request_cancel()
        with self._lock:
            self._closed = True
            self.stop_listening()


class CaptureListener:
    def __init__(self, session, native):
        self.session, self.native = session, native
        self.stopped = False

    def start(self):
        with self.session._lock:
            if self.session.closed:
                raise RuntimeError("Захват уже завершён.")
            try:
                self.native.start()
            except BaseException:
                self.session.close()
                raise

    def stop(self):
        self.session.close()

    def is_alive(self):
        return not self.stopped and self.native.is_alive()
