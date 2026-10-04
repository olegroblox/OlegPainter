"""Straight-stroke mode (pen_split_strokes): every straight segment is its own press.

Browser games such as Gartic Phone sample the mouse once a frame and smooth a
held stroke into curves, so the engine must never turn inside a stroke there.
"""
from engine.olegpainter.core import OlegPainter


class _Recorder:
    def __init__(self, log):
        self.log = log

    def button_down(self, button="left"):
        self.log.append("down")

    def button_up(self, button="left"):
        self.log.append("up")

    def move(self, x, y):
        self.log.append(("move", x, y))


def _engine(split=True):
    e = OlegPainter(status_callback=lambda m: None)
    log = []
    e._input = _Recorder(log)
    e._move_abs = lambda x, y: log.append(("move", int(x), int(y)))
    e._sleep_with_abort = lambda *a, **k: False
    e._automation_cancelled = lambda: False
    e.stop_flag = False
    e.drawing_enabled = True
    e.draw_delay = 0.0
    e.pen_button_delay = 0.0
    e.mouse_release_settle = 0.0
    e.pen_press_nudge = False
    e.area_fill_delay = 0.0
    e.pen_max_step = 0
    e.brush_size = 1
    e.pen_split_strokes = split
    return e, log


def test_serpentine_rows_become_one_stroke_each_and_skip_the_row_steps():
    e, log = _engine()
    e._pen_down()
    e.draw_line(0, 0, 5, 0)
    e.draw_line(5, 0, 5, 1)   # one cell down to the next row: covered by both rows
    e.draw_line(5, 1, 0, 1)
    e.draw_line(0, 1, 0, 2)   # the stroke ends on a skipped step
    e._mouse_up_with_settle()
    assert log == ["down", ("move", 5, 0), "up", ("move", 5, 1), "down", ("move", 0, 1), "up",
                   ("move", 0, 2), "down", "up"]


def test_staircase_steps_are_drawn_as_separate_strokes():
    e, log = _engine()
    e._pen_down()
    e.draw_line(0, 0, 0, 1)
    e.draw_line(0, 1, 1, 1)
    e._mouse_up_with_settle()
    assert log == ["down", ("move", 0, 1), "up", "down", ("move", 1, 1), "up"]


def test_off_keeps_one_continuous_stroke_and_config_round_trips():
    e, log = _engine(split=False)
    e._pen_down()
    e.draw_line(0, 0, 5, 0)
    e.draw_line(5, 0, 5, 1)
    e._mouse_up_with_settle()
    assert log == ["down", ("move", 5, 0), ("move", 5, 1), "up"]
    other = OlegPainter(status_callback=lambda m: None)
    other.load_config({"pen_split_strokes": True, "pen_stroke_gap": 5})
    assert other.pen_split_strokes is True and other.pen_stroke_gap == 1.0
    assert other.get_config()["pen_split_strokes"] is True


def test_live_backend_skips_a_right_release_that_windows_reports_up(monkeypatch):
    import importlib.util
    from infrastructure import automation_transport
    # the test session swaps the live backend for a recorder: load the real class separately
    spec = importlib.util.spec_from_file_location("live_transport", automation_transport.__file__)
    transport = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(transport)
    backend = transport.WindowsInputBackend()
    sent = []
    monkeypatch.setattr(backend, "_send_mouse", lambda flags, state, x=0, y=0: sent.append(state))
    monkeypatch.setattr(transport, "_os_button_down", lambda vk: False)
    backend.button("right", False)
    assert sent == []       # an unpaired right release opens a browser's context menu
    backend.button("left", False)
    monkeypatch.setattr(transport, "_os_button_down", lambda vk: True)
    backend.button("right", False)
    assert len(sent) == 2   # left always, right only while it is down
