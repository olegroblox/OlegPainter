import cv2
import numpy as np

from engine.olegpainter.input_timing import corners_kept, line_is_continuous, recommend, zigzag


def _canvas():
    return np.zeros((160, 160), dtype=np.uint8)


def test_continuous_line_is_recognised_and_a_bare_jump_is_not():
    drawn = _canvas()
    cv2.line(drawn, (20, 80), (140, 80), 1, 1)
    assert line_is_continuous(drawn > 0, (20, 80), (140, 80), 2.5)
    stamps_only = _canvas()
    stamps_only[80, 20] = stamps_only[80, 140] = 1          # a game stamping only where sampled
    assert not line_is_continuous(stamps_only > 0, (20, 80), (140, 80), 2.5)


def test_dropped_turns_are_counted_as_lost_corners():
    pts = zigzag((20, 60))
    full = _canvas()
    cv2.polylines(full, [np.array(pts, dtype=np.int32).reshape(-1, 1, 2)], False, 1, 1)
    assert corners_kept(full > 0, pts, 2.5) == (len(pts), len(pts))
    # the program kept every other vertex: chords skip the odd corners
    chord = _canvas()
    cv2.polylines(chord, [np.array(pts[::2], dtype=np.int32).reshape(-1, 1, 2)], False, 1, 1)
    kept, total = corners_kept(chord > 0, pts, 2.5)
    assert kept < total


def test_recommendation_takes_the_first_dwell_that_passes_twice():
    # The dense zigzag is itself the margin: a lone pass (0.5 ms) is not trusted,
    # the first of two passes in a row is used as is.
    trials = [(0.0, False), (0.0005, True), (0.001, False), (0.002, True), (0.004, True)]
    choice = recommend(True, trials, 1.1)
    assert choice["pen_max_step"] == 0
    assert choice["draw_delay"] == 0.002
    stamping = recommend(False, [(0.0, True), (0.0005, True)], 3.0)
    assert stamping["pen_max_step"] == 3 and stamping["draw_delay"] > 0


def test_streak_and_lost_diagonal_are_both_errors():
    from engine.olegpainter.input_timing import lift_pattern, pattern_errors
    expected = lift_pattern()
    assert pattern_errors(expected, expected, 2.0) == (0, 0)
    streak = expected.astype(np.uint8)
    cv2.line(streak, (5, 70), (70, 45), 1, 1)  # a jump drawn with the pen down
    assert pattern_errors(streak > 0, expected, 2.0)[1] > 0
    lost = expected.copy()
    lost[20:36] = False
    assert pattern_errors(lost, expected, 2.0)[0] > 0


def test_lift_pause_is_the_first_value_that_passed_twice_with_margin():
    from engine.olegpainter.input_timing import LIFT_LADDER, first_reliable
    trials = [(0.0, False), (0.001, False), (0.002, True), (0.004, True)]
    assert first_reliable(trials, LIFT_LADDER) == 0.003
    assert first_reliable(trials, LIFT_LADDER, 2.0) == 0.004
    assert first_reliable([(0.0, False)], LIFT_LADDER) == round(0.033 * 1.5, 4)


def test_measured_lift_pause_replaces_the_three_hand_tuned_pauses():
    from types import SimpleNamespace
    from ui.services.brush_learning import BrushLearningMixin
    engine = SimpleNamespace(draw_delay=0.0, pen_max_step=0, area_fill_delay=0.01, run_length_merge_enabled=False,
                             pen_settle_delay=0.01, mouse_release_settle=0.003, pen_button_delay=0.03)
    host = SimpleNamespace(engine=engine, _emit_session_state_changed=lambda *a, **k: None,
                           _BRIDGE_LIFT_PAUSE=BrushLearningMixin._BRIDGE_LIFT_PAUSE)
    BrushLearningMixin._apply_input_timing(host, {"draw_delay": 0.0015, "pen_max_step": 0,
                                                  "interpolates": True, "lift_pause": 0.006})
    assert (engine.pen_settle_delay, engine.mouse_release_settle, engine.pen_button_delay) == (0.006, 0.006, 0.0)
    assert "отрыве пера 6.0 мс" in host._input_timing_note
    assert engine.astar_bridge_enabled is False  # a cheap lift beats retraced bridges
    BrushLearningMixin._apply_input_timing(host, {"draw_delay": 0.016, "pen_max_step": 3,
                                                  "interpolates": False, "lift_pause": 0.05})
    assert engine.astar_bridge_enabled is True


def test_dynamic_brush_uses_detail_size_only_when_target_connects_points():
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from engine.olegpainter.core import OlegPainter
    engine = OlegPainter(status_callback=lambda _m: None)
    engine.dynamic_brush_session_active = True
    engine.drawing_algorithm = "dfs_4dir"
    engine._dynamic_brush_active_for_current_draw = lambda: True
    engine._dynamic_brush_v2_radius_table = lambda: ([0.5, 5.0], [1.0, 10.0])
    assert engine._dynamic_brush_passes() == ["coarse", "detail", "thin"]
    engine.target_connects_points = True
    assert engine._dynamic_brush_passes() == ["thin"]
    restored = OlegPainter(status_callback=lambda _m: None)
    restored.load_config(engine.get_config())
    assert restored.target_connects_points is True



def test_trail_along_counts_paint_left_on_the_jump_only():
    from engine.olegpainter.input_timing import trail_along
    clean = _canvas()
    cv2.line(clean, (100, 50), (130, 50), 1, 1)             # the stroke itself, starting at the press
    assert trail_along(clean > 0, (40, 110), (100, 50), 2.0) == 0
    dotted = clean.copy()
    for t in (0.2, 0.4, 0.6, 0.8):                            # stamps the game left along the jump
        x, y = round(40 + 60 * t), round(110 - 60 * t)
        cv2.rectangle(dotted, (x - 2, y - 2), (x + 2, y + 2), 1, -1)
    assert trail_along(dotted > 0, (40, 110), (100, 50), 2.0) > 0


def test_press_nudge_wiggles_before_press_and_repeats_before_release(monkeypatch):
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import engine.olegpainter.core as core
    from engine.olegpainter.core import OlegPainter
    monkeypatch.setattr(core.time, "sleep", lambda *_: None)
    engine = OlegPainter(status_callback=lambda _m: None)
    engine.pen_button_delay = 0.0
    engine.mouse_release_settle = 0.0
    events = engine._input.backend.events
    for nudge, expected_moves in ((False, 1), (True, 1 + 4 + 1)):
        engine.pen_press_nudge = nudge
        events.clear()
        engine._move_abs(50, 60)
        engine._pen_down()
        engine._mouse_up_with_settle()
        moves = [e for e in events if e[0] == "move"]
        assert len(moves) == expected_moves
        assert moves[-1] == ("move", 50, 60)                  # always ends where the pen is
        assert [e[0] for e in events if e[0] in ("down", "up")] == ["down", "up"]
    assert OlegPainter(status_callback=lambda _m: None).get_config()["pen_press_nudge"] is False


def test_press_nudge_keeps_control_clicks_on_the_control(monkeypatch):
    # Live Speed Draw! 2026-10-01: with the nudge on, every wheel/slider click was
    # pressed and released at the last canvas point, and the whole picture came out
    # in the previous colour.
    import os
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    import engine.olegpainter.core as core
    from engine.olegpainter.core import OlegPainter
    monkeypatch.setattr(core.time, "sleep", lambda *_: None)
    engine = OlegPainter(status_callback=lambda _m: None)
    engine.pen_button_delay = 0.0
    engine.mouse_release_settle = 0.0
    engine.pen_press_nudge = True
    events = engine._input.backend.events
    engine._move_abs(900, 400)          # the last pen point on the canvas
    events.clear()
    engine._click_abs(182, 847)         # a colour on the wheel
    engine._input_click(button="left")
    presses = [i for i, e in enumerate(events) if e[0] in ("down", "up")]
    assert len(presses) == 2
    moves = [e for e in events if e[0] == "move"]
    assert all(abs(x - 182) <= 1 and y == 847 for _, x, y in moves)
    assert moves[-1] == ("move", 182, 847)



def test_a_lost_last_move_is_learned_as_the_nudge(monkeypatch):
    # «Нарисуй меня!» draws a segment only when the cursor moves on: a press, one
    # move and a release leave a dot unless the release steps back first.
    from types import SimpleNamespace
    from engine.olegpainter import input_timing
    monkeypatch.setattr(input_timing, "_stroke", lambda *_a, **_k: None)

    def target(draws_last_move):
        engine = SimpleNamespace(pen_press_nudge=None)

        def probe(draw):
            draw(60, 60)
            mask = np.zeros((120, 120), bool)
            mask[58:63, 28:33] = True                                 # the press dot
            if draws_last_move(engine.pen_press_nudge):
                mask[58:63, 28:93] = True
            return mask, (0, 0)
        return engine, probe

    draw_me, probe = target(lambda nudge: nudge)
    assert input_timing._last_move_needs_step(draw_me, probe, 2.5) is True
    paint, probe = target(lambda nudge: True)
    assert input_timing._last_move_needs_step(paint, probe, 2.5) is False
    stamps, probe = target(lambda nudge: False)                    # never connects: the jump probe decides
    assert input_timing._last_move_needs_step(stamps, probe, 2.5) is False
