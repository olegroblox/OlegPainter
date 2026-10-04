"""«Нарисуй меня!» (Roblox): place preset, snapshot cutout, stop handling (2026-10-01 live tests)."""
import os

import numpy as np
import pytest
from PIL import Image, ImageDraw

from application import onboarding
from application.cutout import cut_out
from tests.test_application_controller import controllers  # noqa: F401
from tests.test_quick_presentation import pump


def character_scene():
    """A framed 'player' on sky and grass: dark body, a white face inside dark hair,
    sky showing between two horns, a halo above the head and a red platform under the feet."""
    img = Image.new("RGB", (200, 340), (90, 170, 245))
    d = ImageDraw.Draw(img)
    d.rectangle((0, 170, 200, 340), (70, 190, 70))
    d.rectangle((50, 60, 150, 280), (40, 20, 70))            # body
    d.rectangle((75, 85, 125, 135), (240, 240, 240))          # face, enclosed by the body
    d.line([(88, 61), (108, 36)], (20, 20, 20), 4)             # antlers crossing above the head:
    d.line([(112, 61), (92, 36)], (20, 20, 20), 4)             # a little sky shows between them
    d.ellipse((70, 14, 130, 26), (250, 210, 40))              # a halo floating above
    d.ellipse((40, 296, 160, 318), (220, 50, 20))             # the platform under the feet
    return img


def test_cutout_keeps_the_player_and_drops_sky_grass_and_platform():
    out = np.asarray(cut_out(character_scene()))
    alpha = out[..., 3] > 0
    assert alpha[200, 100] and alpha[110, 100]                # body and the enclosed white face
    assert not alpha[5, 5] and not alpha[330, 10]             # sky and grass
    assert not alpha[56, 100]                                 # sky between the antlers
    assert alpha[20, 100]                                     # the halo is part of the look
    assert not alpha[307, 100]                                # the platform is not
    with pytest.raises(ValueError):
        cut_out(Image.new("RGB", (10, 10), "white"))


def test_draw_me_place_sets_wheel_nudge_and_cell(controllers):
    controller = controllers()
    controller.profiles.select("draw_me", None)
    config = controller.service.snapshot_painter_config()
    assert config["color_picking_method"] == "wheel_square"
    assert config["pen_press_nudge"] is True and config["brush_size"] == 4
    assert config["draw_delay"] == 0.012 and config["pen_max_step"] == 16
    assert config["drawing_algorithm"] == "line_cover"


def test_quick_start_offers_the_screenshot_first_for_draw_me():
    from types import SimpleNamespace
    prep = SimpleNamespace(current_method_id="wheel_square", current_place_id="draw_me", image_loaded=False,
                           area_selected=False, area_stale=False, required_calibrations=("wheel_square",),
                           can_start=False)
    service = SimpleNamespace(get_hotkeys=lambda: {}, engine=SimpleNamespace(wheel_square_calib=None))
    view = onboarding.build(SimpleNamespace(preparation=prep), service, brush_ready=False)
    steps = {s["id"]: s for s in view["steps"]}
    assert view["target"] == "draw_me"
    assert steps["image"]["actions"][0]["action"] == "screen_snapshot"
    assert "Снимок экрана" in steps["image"]["detail"]
    assert [a["action"] for a in steps["colour"]["actions"]] == ["calibrate_wheel_square"]


def test_snapshot_loses_its_background_when_new_pictures_do(controllers):
    controller = controllers()
    controller.profiles.select("draw_me", None)
    service = controller.service
    service.set_auto_background("auto")
    assert service.set_screen_snapshot(character_scene().convert("RGBA"))
    pump(lambda: not service.background_busy)
    alpha = np.asarray(service.engine.source_pil_image)[..., 3]
    assert alpha[5, 5] == 0 and alpha[200, 100] == 255
    assert service.background_method == "auto" and service.has_picture_edits
    assert service.set_background_method("none")             # «Оставить» brings it back
    assert np.asarray(service.engine.source_pil_image)[..., 3].min() == 255


def test_release_steps_back_along_the_stroke_so_its_last_segment_counts(monkeypatch):
    # Draw Me! draws a segment only when the cursor moves on: a repeated point is
    # ignored, a 4 px step back over the painted segment keeps the last one.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import engine.olegpainter.core as core
    from engine.olegpainter.core import OlegPainter
    monkeypatch.setattr(core.time, "sleep", lambda *_: None)
    engine = OlegPainter(status_callback=lambda _m: None)
    engine.pen_button_delay = engine.mouse_release_settle = 0.0
    engine.pen_press_nudge = True
    events = engine._input.backend.events
    engine._move_abs(100, 100)
    engine._pen_down()
    engine._move_abs(160, 100)
    engine._move_abs(160, 120)
    events.clear()
    engine._mouse_up_with_settle()
    assert [e[1:] for e in events if e[0] == "move"] == [(160, 120), (160, 116)]
    assert events[-1] == ("up", "left")

    engine._move_abs(300, 300)              # a dot: no segment to step back along
    engine._pen_down()
    events.clear()
    engine._mouse_up_with_settle()
    assert [e[1:] for e in events if e[0] == "move"] == [(300, 300)]

    engine.pen_press_nudge = False          # other programs: the release is untouched
    engine._move_abs(400, 300)
    engine._pen_down()
    engine._move_abs(440, 300)
    events.clear()
    engine._mouse_up_with_settle()
    assert events == [("up", "left")]


def test_stop_during_an_euler_route_is_not_redrawn_another_way():
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from engine.olegpainter.core import OlegPainter
    from infrastructure.automation_transport import InputCancelled
    engine = OlegPainter(status_callback=lambda _m: None)
    engine.astar_bridge_enabled = False
    mask = np.ones((3, 6), bool)
    fallback = []

    def cancelled(*_args, **_kwargs):
        raise InputCancelled("stop")

    with pytest.raises(InputCancelled):
        engine._draw_component_with_route(mask, [(0, 1), (1, 0), (0, -1), (-1, 0)], 0, 0,
                                          lambda: fallback.append(1), route_draw_fn=cancelled)
    assert not fallback


def test_control_presses_and_test_dots_press_like_the_pen(monkeypatch):
    # Live 2026-10-01: the game took a press right after a jump where the cursor had
    # waited — the brush slider moved every other time and the learning's test dots
    # were lost. Controls and test dots press as the drawing pen does.
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import engine.olegpainter.core as core
    import engine.olegpainter.dynamic_brush as dynamic_brush
    from engine.olegpainter.core import OlegPainter
    monkeypatch.setattr(core.time, "sleep", lambda *_: None)
    monkeypatch.setattr(dynamic_brush.time, "sleep", lambda *_: None)
    messages = []
    engine = OlegPainter(status_callback=messages.append)
    assert not [m for m in messages if "HEX" in m]           # no false error on creation
    engine.pen_button_delay = engine.mouse_release_settle = 0.0
    engine.pen_press_nudge = True
    monkeypatch.setattr(engine, "_ui_input_wait", lambda _s: False)
    events = engine._input.backend.events

    def before_press():
        down = next(i for i, e in enumerate(events) if e[0] == "down")
        return [e[1:] for e in events[:down] if e[0] == "move"][-4:]

    engine._ui_drag(500, 700, 500, 700, wiggle=(2, 0))       # the brush slider
    assert before_press() == [(501, 700), (500, 700), (499, 700), (500, 700)]
    events.clear()
    engine._ui_click_at(300, 200, clicks=1)
    assert before_press() == [(301, 200), (300, 200), (299, 200), (300, 200)]
    events.clear()
    assert engine._dynamic_brush_execute_probe("point", anchor=(800, 400), patch_half=20)
    assert before_press() == [(801, 400), (800, 400), (799, 400), (800, 400)]
    assert [e[0] for e in events].count("down") == 1 and events[-1] == ("up", "left")
