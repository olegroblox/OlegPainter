"""Exercise completion races separately: a QThread deletion can abort Python."""
from tests.test_service_lifetime import run_qt_scenario


def test_next_preview_waits_for_queued_thread_completion(tmp_path):
    run_qt_scenario("""
        from threading import Event
        import time
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from ui.services.painter_service import PainterService

        app = QApplication([])
        service = PainterService()
        entered = Event()
        release = Event()
        tasks = []
        owned = []
        service._can_prepare_preview = lambda: True
        service._invoke_engine_preprocess = lambda: None
        def prepare():
            tasks.append(True)
            entered.set()
            if len(tasks) > 1:
                assert release.wait(5)
        service.engine.prepare_image_and_palette = prepare
        try:
            service._render_revision = service._preview_requested_revision = 10
            service._start_preview_worker(strict=False)
            first = service._preview_thread
            first_worker = service._preview_worker
            owned.append((first, first_worker))
            assert entered.wait(2)
            # Finish natively without delivering its queued Qt completion.
            first.quit()
            assert first.wait(2000)
            entered.clear()
            service._render_revision = service._preview_requested_revision = 11
            service._start_preview_worker(strict=False)
            owned.append((service._preview_thread, service._preview_worker))
            service._start_preview_worker(strict=True)
            owned.append((service._preview_thread, service._preview_worker))
            assert service._preview_thread is first, 'replaced before completion delivery'
            assert service._preview_worker is first_worker
            assert service._preview_active_revision == 10
            assert service._preview_pending == 'strict'
            assert len(tasks) == 1
            for _ in range(100):
                QTest.qWait(10)
                time.sleep(0.005)
                if entered.is_set():
                    break
            assert entered.is_set(), (
                'coalesced request was lost', service._preview_pending,
                service._preview_thread is None, service._preview_active_revision,
                service._preview_timer.isActive() if service._preview_timer else None,
                service._compute_capture_state(), service._is_drawing,
            )
            assert len(tasks) == 2
            second = service._preview_thread
            owned.append((second, service._preview_worker))
            assert second is not None and second is not first and second.isRunning()
            assert service._preview_active_revision == 11
            release.set()
            for _ in range(100):
                QTest.qWait(10)
                time.sleep(0.005)
                if service._preview_thread is None:
                    break
            assert service._preview_thread is None
            assert service._preview_worker is None
            assert service._preview_active_revision == 0
            assert service._preview_pending is None
        finally:
            release.set()
            for thread, worker in owned:
                try:
                    thread.quit()
                    assert thread.wait(2000)
                except RuntimeError:
                    pass  # already deleted by Qt after normal completion
            service.shutdown()
    """, tmp_path)


def test_stale_finished_signal_preserves_active_preview(tmp_path):
    run_qt_scenario("""
        from threading import Event
        from PySide6.QtCore import QThread, Qt
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from ui.services.painter_service import PainterService

        app = QApplication([])
        service = PainterService()
        entered, release = Event(), Event()
        def prepare():
            entered.set()
            assert release.wait(5)
        service.engine.prepare_image_and_palette = prepare
        stale = QThread()
        stale.finished.connect(lambda: service._on_preview_worker_finished(stale), Qt.QueuedConnection)
        service._render_revision = service._preview_requested_revision = 12
        service._start_preview_worker(strict=False)
        active, worker = service._preview_thread, service._preview_worker
        try:
            assert entered.wait(2)
            stale.finished.emit()
            QTest.qWait(30)
            assert service._preview_thread is active, 'stale signal retired a running thread'
            assert service._preview_worker is worker
            assert service._preview_active_revision == 12
            assert active.isRunning()
        finally:
            release.set()
            active.quit()
            assert active.wait(2000)
            service.shutdown()
    """, tmp_path)


def test_shutdown_deletes_finished_preview_thread(tmp_path):
    run_qt_scenario("""
        import time
        from threading import Event
        from PySide6.QtCore import QCoreApplication, QEvent
        from PySide6.QtTest import QTest
        from PySide6.QtWidgets import QApplication
        from shiboken6 import isValid
        from ui.services.painter_service import PainterService

        app = QApplication([])
        service = PainterService()
        entered, release = Event(), Event()
        def prepare():
            entered.set()
            assert release.wait(5)
        service.engine.prepare_image_and_palette = prepare
        service._start_preview_worker(strict=False)
        active = service._preview_thread
        try:
            assert entered.wait(2)
            service.shutdown(wait=False)
            assert not service._shutdown_completed
            release.set()
            for _ in range(200):
                QTest.qWait(10)
                time.sleep(0.005)
                QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)
                if service._shutdown_completed and not isValid(active):
                    break
            assert service._shutdown_completed
            assert not isValid(active), 'shutdown retained a native preview thread'
        finally:
            release.set()
            service.shutdown()
    """, tmp_path)
