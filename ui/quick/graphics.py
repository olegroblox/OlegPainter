"""Explicit Qt Quick graphics policy."""
import logging
import os
import sys
from PySide6.QtQuick import QQuickWindow


def configure_graphics():
    """Select before the first Quick window. Qt overrides remain diagnostic inputs.

    Windows Qt 6.9's DXGI vblank service can stop delivering update requests
    while the GUI event loop remains responsive. Use Qt's ordinary delivery
    of update requests, retaining hardware rendering and presentation vsync.
    This does not add periodic repaints. Revalidate when updating pinned Qt.
    """
    # UI text has fixed logical sizes, never 3D transforms. Use the platform's
    # hinted font rasterization instead of distance-field text for small labels.
    QQuickWindow.setTextRenderType(QQuickWindow.NativeTextRendering)
    if sys.platform == "win32":
        value = os.environ.setdefault("QT_D3D_NO_VBLANK_THREAD", "1")
        logging.info("Qt Quick update policy: QT_D3D_NO_VBLANK_THREAD=%s (GRAPHICS-001)", value)
