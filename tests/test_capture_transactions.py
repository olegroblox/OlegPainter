"""Cancellation and explicit finish are publication boundaries for native captures."""
from copy import deepcopy
from threading import Event, Thread

import pytest

from engine.olegpainter import core


class Listener:
    def __init__(self, **callbacks):
        self.callbacks = callbacks
        self.alive = False
    def start(self):
        self.alive = True
    def stop(self):
        self.alive = False
    def is_alive(self):
        return self.alive


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(core.mouse, "Listener", Listener)
    result = core.OlegPainter(status_callback=lambda *_: None)
    result.hex_input_coord = (1, 2)
    result.target_app_layer_coords = [(3, 4)]
    result.outline_fill_brush_coord, result.outline_fill_fill_coord = (5, 6), (7, 8)
    result.manual_mix_canvas_rgb = (9, 10, 11)
    result.extra_actions_state["pre"] = dict(enabled=True, events=[dict(
        device="mouse", action="click", button="left", x=12, y=13, delay=.1)])
    monkeypatch.setattr(result, "_capture_point", lambda fallback: fallback)
    monkeypatch.setattr(result, "_average_screen_rgb_block", lambda *_, **__: (255, 255, 255))
    yield result
    result._cancel_active_captures()
    result._end_capture_session(wait=True)


def saved(engine):
    return deepcopy((engine.hex_input_coord, engine.target_app_layer_coords,
                     engine.outline_fill_brush_coord, engine.outline_fill_fill_coord,
                     engine.manual_mix_canvas_rgb, engine.extra_actions_state))


CAPTURES = [("hex_coord", "hex", True), ("app_layer_coord", "app_layer", True),
            ("outline_fill_coord", "outline_fill", True),
            ("manual_mix_background", "manual_mix_background", True),
            ("extra_actions", "extra_actions", False)]


@pytest.mark.parametrize("kind,listener,pressed", CAPTURES)
@pytest.mark.parametrize("cancel_method", ["_cancel_active_captures", "request_drawing_stop"])
def test_cancellation_discards_inflight_publication_without_waiting(engine, monkeypatch, kind, listener, pressed, cancel_method):
    before = saved(engine)
    getattr(engine, f"start_{kind}_capture")()
    session = engine._capture_session
    original_publish = session.publish
    entered, release, cancelled = Event(), Event(), Event()
    def delayed_publish(callback):
        entered.set()
        assert release.wait(2)
        return original_publish(callback)
    monkeypatch.setattr(session, "publish", delayed_publish)
    engine.ignore_clicks_until = engine._capture_ignore_until = 0
    callback = getattr(engine, f"mouse_listener_{listener}").native.callbacks["on_click"]
    worker = Thread(target=lambda: callback(100, 200, core.mouse.Button.left, pressed))
    closer = Thread(target=lambda: (getattr(engine, cancel_method)(), cancelled.set()))
    worker.start()
    try:
        assert entered.wait(1)
        closer.start()
        assert cancelled.wait(.5), "cancel waited for unfinished result"
        assert saved(engine) == before
        assert session.ownership.current is not None
    finally:
        release.set()
        worker.join(2)
        if closer.ident is not None:
            closer.join(2)
    assert saved(engine) == before
    if cancel_method == "request_drawing_stop":
        assert session.ownership.current is not None
        assert not session.finished.is_set()
        engine.stop_script()
    assert session.finished.is_set()
    assert session.ownership.current is None


def test_layer_draft_is_displayed_but_only_finish_replaces_saved_data(engine):
    updates = []
    engine.layers_changed_callback = lambda coords: updates.append(list(coords))
    engine.start_app_layer_coord_capture()
    engine.ignore_clicks_until = 0
    click = engine.mouse_listener_app_layer.native.callbacks["on_click"]
    click(100, 200, core.mouse.Button.left, True)
    assert engine.target_app_layer_coords == [(3, 4)]
    assert engine.layer_capture_points() == [(100, 200)]
    assert updates == [[], [(100, 200)]]
    engine.finish_app_layer_coord_capture()
    assert engine.target_app_layer_coords == [(100, 200)]
    click(300, 400, core.mouse.Button.left, True)
    assert engine.target_app_layer_coords == [(100, 200)]


@pytest.mark.parametrize("kind,listener,pressed", [CAPTURES[1], CAPTURES[4]])
def test_cancel_keeps_previous_recording_after_new_click(engine, kind, listener, pressed):
    before = saved(engine)
    getattr(engine, f"start_{kind}_capture")()
    engine.ignore_clicks_until = engine._capture_ignore_until = 0
    getattr(engine, f"mouse_listener_{listener}").native.callbacks["on_click"](
        100, 200, core.mouse.Button.left, pressed)
    assert saved(engine) == before
    engine._cancel_active_captures()
    getattr(engine, f"finish_{kind}_capture")()
    assert saved(engine) == before


def test_outline_pair_commits_together_and_cancel_preserves_both(engine, monkeypatch):
    monkeypatch.setattr(engine, "_outline_fill_requires_fill_tool", lambda: True)
    before = saved(engine)
    engine.start_outline_fill_coord_capture()
    click = engine.mouse_listener_outline_fill.native.callbacks["on_click"]
    click(100, 200, core.mouse.Button.left, True)
    assert engine._outline_fill_capture_step == "fill"
    assert saved(engine) == before
    engine._cancel_active_captures()
    assert saved(engine) == before
    engine.start_outline_fill_coord_capture()
    click = engine.mouse_listener_outline_fill.native.callbacks["on_click"]
    click(100, 200, core.mouse.Button.left, True)
    click(300, 400, core.mouse.Button.left, True)
    assert engine.outline_fill_brush_coord == (100, 200)
    assert engine.outline_fill_fill_coord == (300, 400)
    assert engine._capture_session.finished.is_set()


def test_action_draft_is_visible_and_finish_commits_it(engine):
    previous = engine._serialize_actions("pre")
    engine.start_extra_actions_capture()
    engine._capture_ignore_until = 0
    engine.mouse_listener_extra_actions.native.callbacks["on_click"](100, 200, core.mouse.Button.left, False)
    assert engine._serialize_actions("pre") == previous
    draft = engine._serialize_actions("pre", include_draft=True)
    assert len(draft) == engine._extra_actions_summary()["pre"]["count"] == 1
    assert draft[0]["x"] == 100
    engine.finish_extra_actions_capture()
    assert engine._serialize_actions("pre") == draft
    assert engine.extra_actions_state["pre"]["enabled"]


@pytest.mark.parametrize("kind,listener,pressed", CAPTURES)
def test_listener_start_failure_preserves_saved_settings(engine, monkeypatch, kind, listener, pressed):
    before = saved(engine)
    def fail(*_, **__):
        raise RuntimeError("hook unavailable")
    monkeypatch.setattr(Listener, "start", fail)
    try:
        getattr(engine, f"start_{kind}_capture")()
    except RuntimeError as error:
        assert "hook unavailable" in str(error)
    assert saved(engine) == before
    assert engine._capture_session.finished.is_set()
    assert engine._capture_session.ownership.current is None
