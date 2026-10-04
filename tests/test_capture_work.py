from threading import Event

from infrastructure.capture_session import CaptureSession
from infrastructure.capture_work import CaptureWorkQueue
from infrastructure.input_ownership import InputOwnership


def test_finish_drains_selected_samples_and_rejects_new_input():
    ownership = InputOwnership()
    session = CaptureSession("palette", ownership=ownership)
    entered, release = Event(), Event()
    result, failures = [], []
    def sample(request, cancelled):
        entered.set()
        assert release.wait(2)
        return request * 2
    work = CaptureWorkQueue(session, sample, lambda r, v: result.append((r, v)),
                            session.close, failures.append)
    try:
        assert work.submit(1)
        assert entered.wait(1)
        assert work.submit(2)
        work.finish()
        assert not work.submit(3)
        assert ownership.current is not None
    finally:
        release.set()
        work.thread.join(2)
    assert result == [(1, 2), (2, 4)] and not failures
    assert session.finished.is_set() and ownership.current is None


def test_cancel_discards_inflight_result_and_pending_samples():
    session = CaptureSession("palette", ownership=InputOwnership())
    entered, release = Event(), Event()
    result, sampled = [], []
    def sample(request, cancelled):
        sampled.append(request)
        entered.set()
        assert release.wait(2)
        return request
    work = CaptureWorkQueue(session, sample, lambda *args: result.append(args),
                            lambda: result.append("finished"), lambda e: result.append(e))
    try:
        work.submit(1)
        assert entered.wait(1)
        work.submit(2)
        session.close()
        assert not session.finished.is_set()
    finally:
        release.set()
        work.thread.join(2)
    assert sampled == [1] and result == []
    assert session.finished.is_set()


def test_queue_full_reports_error_without_discarding_accepted_samples():
    import pytest
    session = CaptureSession("palette", ownership=InputOwnership())
    entered, release = Event(), Event()
    def sample(request, cancelled):
        entered.set()
        assert release.wait(2)
        return request
    work = CaptureWorkQueue(session, sample, lambda *_: None, session.close, lambda _: session.close(), capacity=1)
    try:
        work.submit(1)
        assert entered.wait(1)
        work.submit(2)
        with pytest.raises(RuntimeError, match="заполнена"):
            work.submit(3)
        work.finish()
    finally:
        release.set()
        work.thread.join(2)
        session.close()


def test_sample_failure_is_reported_and_releases_capture():
    session = CaptureSession("palette", ownership=InputOwnership())
    errors = []
    def sample(*_):
        raise RuntimeError("no frame")
    def failed(error):
        errors.append(str(error))
        session.close()
    work = CaptureWorkQueue(session, sample, lambda *_: None, session.close, failed)
    work.submit(1)
    work.thread.join(2)
    assert errors == ["no frame"] and session.finished.is_set()
