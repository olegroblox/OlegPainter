"""After a finished drawing the pointer leaves the canvas without clicking."""
import ctypes
from types import SimpleNamespace

from engine.olegpainter.core import OlegPainter


def _engine(region):
    events = []
    engine = SimpleNamespace(draw_region=region, stop_flag=False, _pen_pos=None, _pen_prev=None,
                             _PARK_GAP=OlegPainter._PARK_GAP, _log=lambda *_: None)
    engine._input = SimpleNamespace(move=lambda x, y: events.append(("move", x, y)))
    engine._move_abs = lambda x, y: OlegPainter._move_abs(engine, x, y)
    return engine, events


def _screen():
    metrics = ctypes.windll.user32.GetSystemMetrics
    return tuple(int(metrics(i)) for i in (76, 77, 78, 79))


def test_cursor_goes_right_of_canvas_without_click():
    left, top, _w, _h = _screen()
    engine, events = _engine((left + 100, top + 100, 200, 200))
    OlegPainter._park_cursor_off_canvas(engine)
    assert events == [("move", left + 340, top + 200)]


def test_canvas_at_right_edge_parks_on_the_left():
    left, top, width, _h = _screen()
    engine, events = _engine((left + width - 210, top + 100, 200, 200))
    OlegPainter._park_cursor_off_canvas(engine)
    assert events == [("move", left + width - 250, top + 200)]


def test_stopped_drawing_keeps_cursor():
    engine, events = _engine((0, 0, 200, 200))
    engine.stop_flag = True
    OlegPainter._park_cursor_off_canvas(engine)
    assert events == []
