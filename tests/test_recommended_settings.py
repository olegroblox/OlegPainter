"""One recommended start for every place (PRESET-BASE-001)."""
from application.place_catalog import PLACE_PRESETS, RECOMMENDED_SETTINGS, recommended_profile
from tests.test_application_controller import controllers  # noqa: F401


def _config(controller):
    return controller.service.snapshot_painter_config()


def test_every_place_starts_with_straight_segments_and_the_shared_settings(controllers):
    controller = controllers()
    for place in ("speed_draw", "spray_paint", "draw_donate", "gartic_phone", "draw_me", "rust_sign", "universal"):
        controller.profiles.select(place, None)
        config = _config(controller)
        assert config["drawing_algorithm"] == "line_cover", place
        assert config["tone_sequence"] == "details_last", place
        assert config["area_sequence"] == "nearest", place
        assert config["prep_cleanup_mode"] == "vectorized", place
        # what the place must keep always wins over the shared start
        for key, value in PLACE_PRESETS[place].get("settings", {}).items():
            assert config[key] == value, (place, key)


def test_place_specific_values_win_over_the_shared_ones(controllers):
    controller = controllers()
    controller.profiles.select("speed_draw", None)
    config = _config(controller)
    assert config["pen_press_nudge"] is True
    # Roblox takes only a few moves a frame: a zigzag at 2 ms a turn lost its turns,
    # a 5 ms press was missed (live Speed Draw! 2026-10-06)
    assert config["draw_delay"] == 0.006
    assert config["pen_button_delay"] == 0.03
    controller.profiles.select("universal", None)
    config = _config(controller)
    assert config["pen_button_delay"] == RECOMMENDED_SETTINGS["pen_button_delay"]
    assert config["draw_delay"] == RECOMMENDED_SETTINGS["draw_delay"]
    controller.profiles.select("draw_donate", None)
    assert (_config(controller)["draw_delay"], _config(controller)["brush_size"]) == (0.002, 2)
    controller.profiles.select("spray_paint", None)
    config = _config(controller)
    # measured in the game: stamps only where the cursor is sampled
    assert (config["draw_delay"], config["pen_max_step"], config["pen_settle_delay"]) == (0.002, 2, 0.004)
    controller.profiles.select("draw_me", None)
    assert _config(controller)["draw_delay"] == 0.012       # its turns need 12 ms
    controller.profiles.select("gartic_phone", None)
    config = _config(controller)
    assert config["draw_delay"] == 0.0 and config["pen_split_strokes"] is True
    controller.profiles.select("rust_sign", None)
    config = _config(controller)
    # Rust froze on ~160 px/ms of held moves; 20 px hops at 2 ms stay under 10 px/ms
    assert (config["pen_max_step"], config["draw_delay"], config["brush_size"]) == (20, 0.002, 2)
    assert config["pen_button_delay"] == config["pen_settle_delay"] == 0.012


def test_the_users_own_changes_are_kept_after_the_first_visit(controllers):
    controller = controllers()
    controller.profiles.select("speed_draw", None)
    controller.service.set_draw_delay(0.004)
    controller.profiles.select("universal", None)
    controller.profiles.select("speed_draw", None)
    assert _config(controller)["draw_delay"] == 0.004


def test_another_route_of_a_known_place_does_not_inherit_the_previous_place(controllers):
    # GARTIC-002: calibrations belong to the place. A route never used at a place
    # starts clean, not with the colour field of the place the user came from.
    controller = controllers()
    controller.profiles.select("speed_draw", None)
    controller.profiles.select("universal", None)
    controller.service.engine.hex_input_coord = (11, 22)
    controller.service.set_draw_delay(0.03)
    controller.profiles.select("speed_draw", "dfs_4dir")
    config = _config(controller)
    assert config.get("hex_input_coord") in (None, [], ())
    assert config["draw_delay"] == 0.006


def test_a_saved_session_is_not_overwritten_by_the_recommendation(controllers):
    first = controllers()
    first.profiles.select("speed_draw", "dfs_4dir")
    first.service.set_draw_delay(0.0055)
    first.save()
    first.close(save=False)
    second = controllers()
    assert second.restore()
    assert second.profiles.is_set_up("speed_draw")
    second.profiles.select("speed_draw", None)
    config = _config(second)
    assert config["drawing_algorithm"] == "dfs_4dir" and config["draw_delay"] == 0.0055


def test_first_launch_sets_up_the_start_place(controllers):
    controller = controllers()
    assert controller.restore() is False
    config = _config(controller)
    assert controller.profiles.is_set_up("universal")
    assert config["drawing_algorithm"] == "line_cover"
    assert {key: config[key] for key in recommended_profile("universal")} == recommended_profile("universal")
