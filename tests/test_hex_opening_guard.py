"""HEX box opened by recorded clicks (Gartic Phone): never type the code into the
program when the box did not appear."""
import numpy as np

from engine.olegpainter.core import OlegPainter


def _engine(opens_after):
    e = OlegPainter(status_callback=lambda m: None)
    e.color_picking_method = "hex_field"
    e.hex_input_coord = (100, 100)
    e.hex_field_opened_by_actions = True
    e.stop_flag = False
    e.drawing_enabled = True
    plays = []
    closed, opened = np.zeros((12, 24, 3), np.int16), np.full((12, 24, 3), 255, np.int16)
    e._should_play_actions = lambda slot: slot == "pre"
    def play(slot):
        plays.append(slot)
        return True
    e._play_actions_sequence = play
    e._hex_field_probe = lambda: opened if len(plays) >= opens_after else closed
    typed = []
    e._pick_color_hex_field = lambda hex_color: typed.append(hex_color)
    return e, plays, typed


def test_code_is_typed_when_the_box_opens():
    e, plays, typed = _engine(opens_after=1)
    e.pick_color("#112233")
    assert plays == ["pre"] and typed == ["#112233"] and not e.stop_flag


def test_one_retry_when_the_first_opening_is_lost():
    e, plays, typed = _engine(opens_after=2)
    e.pick_color("#112233")
    assert plays == ["pre", "pre"] and typed == ["#112233"]


def test_refuses_to_type_into_the_program_when_the_box_never_opens():
    e, plays, typed = _engine(opens_after=99)
    e.pick_color("#112233")
    assert plays == ["pre", "pre"] and typed == [] and e.stop_flag and not e.drawing_enabled
    assert "Окно выбора цвета не открылось" in e._drawing_failure_message


def test_other_places_are_not_checked():
    e, plays, typed = _engine(opens_after=99)
    e.hex_field_opened_by_actions = False
    e.pick_color("#112233")
    assert plays == ["pre"] and typed == ["#112233"]
