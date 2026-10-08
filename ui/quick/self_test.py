"""Check a built copy without showing a window: `OlegPainter.exe --self-test report.json`.

A packaged build can miss a module, a DLL or a data file that the source tree has.
This loads the real QML window offscreen, prepares a synthetic picture through the
engine, compiles the route kernels and imports the native parts, with settings kept
in a temporary folder so the user's own ones are never touched.
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from pathlib import Path
from tempfile import TemporaryDirectory


def _native_imports():
    import cv2
    import numpy
    import PIL
    import pynput  # noqa: F401
    import keyboard  # noqa: F401
    import sklearn
    import windows_capture  # noqa: F401
    import interception  # noqa: F401
    return dict(numpy=numpy.__version__, opencv=cv2.__version__, pillow=PIL.__version__,
                sklearn=sklearn.__version__)


def _onnxruntime():
    import onnxruntime
    return dict(version=onnxruntime.__version__, providers=onnxruntime.get_available_providers())


def _route_kernels():
    import numpy as np
    from engine.olegpainter import route_kernels
    if not route_kernels.NUMBA_OK:
        raise RuntimeError("numba недоступна: маршруты считаются медленным путём")
    mask = np.zeros((24, 32), dtype=np.uint8)
    mask[4:20, 6:28] = 255
    order = route_kernels.snake_order_fast(mask, None, 4, 6, strip_axis=False)
    if not order or len(order) != int((mask == 255).sum()):
        raise RuntimeError("змейка обошла не все клетки области")
    return dict(cells=len(order))


def _window_and_preparation(app, state_dir):
    from PySide6.QtCore import QUrl
    from PySide6.QtGui import QColor, QImage, QPainter
    from ui.quick.application import QuickApplication

    quick = QuickApplication(desktop=False)
    try:
        if quick.qml_warnings:
            raise RuntimeError("предупреждения QML:\n" + "\n".join(quick.qml_warnings))
        picture = QImage(160, 100, QImage.Format_RGB32)
        picture.fill(QColor("#2f647b"))
        painter = QPainter(picture)
        painter.fillRect(20, 20, 60, 50, QColor("#daab76"))
        painter.end()
        source = Path(state_dir) / "self-test.png"
        if not picture.save(str(source)):
            raise RuntimeError("не удалось сохранить тестовую картинку")
        quick.presenter.openSource(QUrl.fromLocalFile(str(source)))
        quick.service.apply_viewport_state(draw_region_desktop_px=(0, 0, 96, 64))
        quick.service.request_preview_refresh()
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not quick.presenter.view["preview_ready"]:
            app.processEvents()
            time.sleep(0.01)
        if not quick.presenter.view["preview_ready"]:
            raise RuntimeError("картинка не подготовилась за 60 с")
        return dict(pages=10, palette=quick.presenter.view.get("palette_count"))
    finally:
        quick.dispose(save=False)


def run(report_path: str | None) -> int:
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    os.environ.setdefault("QT_QUICK_BACKEND", "software")
    state = TemporaryDirectory(prefix="olegpainter-self-test-", ignore_cleanup_errors=True)
    os.environ["OLEGPAINTER_CONFIG_DIR"] = str(Path(state.name) / "configs")
    report = dict(frozen=bool(getattr(sys, "frozen", False)), executable=sys.executable, checks={})

    def check(name, action, *args):
        started = time.monotonic()
        try:
            detail = action(*args)
            report["checks"][name] = dict(ok=True, seconds=round(time.monotonic() - started, 2), detail=detail)
        except Exception as error:
            report["checks"][name] = dict(ok=False, error=f"{type(error).__name__}: {error}",
                                          trace=traceback.format_exc(limit=8))

    from PySide6.QtCore import QSettings
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication
    QSettings.setDefaultFormat(QSettings.IniFormat)
    QSettings.setPath(QSettings.IniFormat, QSettings.UserScope, state.name)
    QQuickStyle.setStyle("Basic")  # as in quick_main: the controls are styled for Basic
    app = QApplication(sys.argv[:1])
    try:
        check("native_imports", _native_imports)
        check("onnxruntime", _onnxruntime)
        check("route_kernels", _route_kernels)
        check("window_and_preparation", _window_and_preparation, app, state.name)
    finally:
        state.cleanup()
    report["ok"] = all(item["ok"] for item in report["checks"].values())
    text = json.dumps(report, ensure_ascii=False, indent=2)
    if report_path:
        Path(report_path).write_text(text, encoding="utf-8")
    elif sys.stdout is not None:
        sys.stdout.write(text + "\n")
    return 0 if report["ok"] else 1
