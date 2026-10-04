"""Exercise native Qt lifetime in a subprocess: a failure can poison all of Qt."""
import os
from pathlib import Path
import subprocess
import sys
import textwrap


def run_qt_scenario(source, tmp_path):
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen",
           "OLEGPAINTER_CONFIG_DIR": str(tmp_path / "configs")}
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent(source)],
        cwd=Path(__file__).resolve().parents[1], env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_service_collection_preserves_main_thread_and_image_plugins(tmp_path):
    run_qt_scenario("""
        import gc
        import os
        from threading import Thread
        from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QThread
        from PySide6.QtGui import QImage
        from PySide6.QtWidgets import QApplication
        from ui.services.painter_service import PainterService

        app = QApplication([])
        destroyed = []
        def exercise_service():
            # Allocate this wrapper before the service to exercise GC traversal
            # order. Never call QObject.thread(): PySide gives it a false parent.
            main_thread = QThread.currentThread()
            main_thread.destroyed.connect(lambda: destroyed.append(True))
            service = PainterService()
            calls = []
            service._invoke_on_qt(lambda: calls.append(QThread.isMainThread()))
            assert calls == [True]
            worker = Thread(target=lambda: service._invoke_on_qt(
                lambda: calls.append(QThread.isMainThread())))
            worker.start()
            worker.join(timeout=2)
            assert not worker.is_alive()
            assert calls == [True], 'worker must enqueue, not call the UI directly'
            app.processEvents()
            assert calls == [True, True]
            service.shutdown()

        for _ in range(3):
            exercise_service()
            gc.collect()
            if destroyed:
                print('Service collection destroyed the Qt main thread', flush=True)
                # No more Qt calls after its native main-thread data was freed.
                os._exit(1)
            image = QImage(8, 8, QImage.Format_RGB32)
            image.fill(0xff336699)
            data = QByteArray()
            buffer = QBuffer(data)
            assert buffer.open(QIODevice.WriteOnly)
            assert image.save(buffer, 'PNG')
            assert QImage.fromData(data).pixelColor(0, 0).name() == '#336699'
    """, tmp_path)


def test_service_requires_the_application_main_thread(tmp_path):
    run_qt_scenario("""
        from threading import Thread
        from PySide6.QtWidgets import QApplication
        from ui.services.painter_service import PainterService

        try:
            PainterService()
        except RuntimeError as error:
            assert 'main thread' in str(error)
        else:
            raise AssertionError('service created without a Qt application')
        app = QApplication([])
        errors = []
        def attempt():
            try:
                PainterService()
            except RuntimeError as error:
                errors.append(str(error))
        worker = Thread(target=attempt)
        worker.start()
        worker.join(timeout=2)
        assert not worker.is_alive()
        assert len(errors) == 1 and 'main thread' in errors[0]
    """, tmp_path)
