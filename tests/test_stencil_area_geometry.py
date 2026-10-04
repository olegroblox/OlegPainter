"""The picked area must survive fit, edge editing and repeated restoration."""
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from ui.kalka.canvas_view import EDGE_RIGHT, EDGE_BOTTOM
from ui.widgets.kalka_stencil import KalkaStencilOverlay
from ui.helpers.viewport_mapper import MonitorSnapshot

_app = QApplication.instance() or QApplication([])


@pytest.fixture
def qapp():
    return _app


@pytest.fixture
def stencil(qapp):
    overlay = KalkaStencilOverlay()
    source = QImage(64, 64, QImage.Format_RGB32)
    source.fill(0xFF3366CC)
    overlay.set_source_pixmap(QPixmap.fromImage(source))
    overlay.show()
    qapp.processEvents()
    yield overlay
    overlay.close()
    overlay.deleteLater()


@pytest.mark.parametrize("size", [8, 9, 51, 52, 85, 120, 501])
def test_f1_keeps_small_even_and_odd_area(stencil, size):
    chosen = QRect(100, 100, size, size)
    stencil.begin_area_placement(SimpleNamespace(logical_rect=(0, 0, 1000, 800)))
    stencil._view._finish_placement(chosen)
    QApplication.processEvents()
    assert stencil.geometry() == chosen
    crop, region = stencil._view.stencil_geometry()
    assert region == chosen.getRect()
    assert crop == pytest.approx((0, 0, 1, 1), abs=1e-12)


def test_edge_resize_uses_same_small_area_limit(stencil):
    stencil.setGeometry(100, 100, 52, 52)
    stencil.begin_window_resize(EDGE_RIGHT | EDGE_BOTTOM, QPoint(152, 152))
    stencil.update_window_resize(QPoint(108, 108))
    assert stencil.geometry() == QRect(100, 100, 8, 8)
    stencil.end_window_gesture()


@pytest.mark.parametrize("size", [51, 52, 201])
@pytest.mark.parametrize("crop", [(0, 0, 1, 1), (.25, .25, .75, .75)])
def test_restore_does_not_shrink_or_move_each_time(stencil, monkeypatch, size, crop):
    # No OS monitor mapping in this exact logical-pixel contract.
    monkeypatch.setattr(stencil, "_window_snapshot", lambda: None)
    monkeypatch.setattr("ui.widgets.kalka_stencil.collect_monitor_snapshots", lambda: [])
    region = (100, 100, size, size)
    for _ in range(5):
        assert stencil.apply_draw_region(region, crop)
        actual_crop, actual_region = stencil._view.stencil_geometry()
        assert actual_region == region
        assert actual_crop == pytest.approx(crop, abs=1e-12)


def test_restore_before_first_show_uses_new_viewport(qapp, monkeypatch):
    overlay = KalkaStencilOverlay()
    monkeypatch.setattr("ui.widgets.kalka_stencil.collect_monitor_snapshots", lambda: [])
    source = QImage(64, 64, QImage.Format_RGB32)
    source.fill(0xFF3366CC)
    overlay.set_source_pixmap(QPixmap.fromImage(source))
    try:
        assert overlay.apply_draw_region((100, 100, 51, 51), (0, 0, 1, 1))
        overlay.show()
        qapp.processEvents()
        crop, region = overlay._view.stencil_geometry()
        assert region == (100, 100, 51, 51)
        assert crop == pytest.approx((0, 0, 1, 1), abs=1e-12)
    finally:
        overlay.close()
        overlay.deleteLater()


@pytest.mark.parametrize("negative", [False, True])
def test_dpi_125_region_remains_stable_across_restore(stencil, monkeypatch, negative):
    lx, px = (-1600, -2000) if negative else (0, 0)
    snap = MonitorSnapshot("TEST", "TEST", (px, 0, 2000, 1000),
                           (lx, 0, 1600, 800), 1.25, 1.25)
    monkeypatch.setattr(stencil, "_window_snapshot", lambda: snap)
    monkeypatch.setattr("ui.widgets.kalka_stencil.collect_monitor_snapshots", lambda: [snap])
    chosen = QRect(lx + 100, 100, 51, 51)
    stencil._on_area_placed(chosen)
    region, crop = stencil.get_stencil_geometry()
    assert region == (px + 125, 125, 64, 64)
    assert crop == pytest.approx((0, 0, 1, 1))
    for _ in range(5):
        assert stencil.apply_draw_region(region, crop)
        restored, restored_crop = stencil.get_stencil_geometry()
        assert restored == region
        assert restored_crop == pytest.approx(crop)


def test_cancel_placement_preserves_visible_crop(stencil, monkeypatch):
    monkeypatch.setattr("ui.widgets.kalka_stencil.collect_monitor_snapshots", lambda: [])
    assert stencil.apply_draw_region((100, 100, 51, 51), (.25, .25, .75, .75))
    before = stencil._view.stencil_geometry()
    stencil.begin_area_placement(SimpleNamespace(logical_rect=(0, 0, 1000, 800)))
    stencil._view.cancel_placement()
    assert stencil._view.stencil_geometry() == before


def test_f1_does_not_show_pending_image_over_placement(qapp, monkeypatch):
    overlay = KalkaStencilOverlay()
    monkeypatch.setattr("ui.widgets.kalka_stencil.collect_monitor_snapshots", lambda: [])
    source = QImage(64, 64, QImage.Format_RGB32)
    source.fill(0xFF3366CC)
    overlay.set_source_pixmap(QPixmap.fromImage(source))
    try:
        assert overlay.apply_draw_region((100, 100, 51, 51), (0, 0, 1, 1))
        overlay.begin_area_placement(SimpleNamespace(logical_rect=(0, 0, 1000, 800)))
        qapp.processEvents()
        assert not overlay._view.item().isVisible()
        overlay._view.cancel_placement()
        overlay.show()
        qapp.processEvents()
        crop, region = overlay._view.stencil_geometry()
        assert region == (100, 100, 51, 51)
        assert crop == pytest.approx((0, 0, 1, 1))
    finally:
        overlay.close()
        overlay.deleteLater()
