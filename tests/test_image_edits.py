"""One background state for the picture (IMAGE-EDIT-001): leave it, cut it out
without AI, remove it with a model or by colour; new pictures follow the user's
choice; «Вернуть исходник» brings back the picture as inserted."""
import numpy as np
import pytest
from PIL import Image, ImageDraw

from tests.test_application_controller import controllers  # noqa: F401
from tests.test_quick_presentation import pump, quick  # noqa: F401


def scene():
    img = Image.new("RGBA", (60, 80), (90, 170, 245, 255))
    ImageDraw.Draw(img).rectangle((20, 20, 40, 70), (200, 40, 40, 255))
    return img


def fake_cut_out(image):
    """Keeps the object of scene() by its place, whatever its colours became."""
    rgba = np.array(image.convert("RGBA"))
    keep = np.zeros(rgba.shape[:2], bool)
    keep[20:71, 20:41] = True
    rgba[~keep, 3] = 0
    return Image.fromarray(rgba)


@pytest.fixture
def service(controllers, monkeypatch):
    from application import cutout
    monkeypatch.setattr(cutout, "cut_out", fake_cut_out)
    return controllers().service


def alpha(service):
    return np.asarray(service.engine.source_pil_image.convert("RGBA"))[..., 3]


def test_methods_switch_from_the_picture_with_its_background(service):
    assert service.paste_image(scene(), origin="test")
    assert service.background_method == "none" and not service.has_picture_edits

    assert service.set_background_method("auto")
    assert service.background_busy
    pump(lambda: not service.background_busy)
    assert service.background_method == "auto" and service.has_picture_edits
    assert alpha(service)[5, 5] == 0 and alpha(service)[40, 30] == 255
    assert service.picture_original_qimage() is not None

    assert service.set_background_method("color")      # the colour key works on the full picture
    assert service.background_method == "color" and service.engine.background_removal_enabled
    assert alpha(service).min() == 255 and not service.has_picture_edits

    assert service.set_background_method("none")
    assert service.background_method == "none" and not service.engine.background_removal_enabled
    assert alpha(service).min() == 255


def test_new_pictures_follow_the_users_choice(service):
    service.set_auto_background("auto")
    assert service.paste_image(scene(), origin="test")
    pump(lambda: not service.background_busy)
    assert service.background_method == "auto" and alpha(service)[5, 5] == 0
    assert service.restore_original()
    assert service.background_method == "none" and alpha(service).min() == 255
    assert not service.restore_original()                  # nothing left to bring back

    service.set_auto_background("off")
    assert service.set_screen_snapshot(scene())
    assert not service.background_busy and alpha(service).min() == 255
    with pytest.raises(ValueError):
        service.set_auto_background("sometimes")


def test_ai_method_needs_the_model_and_shares_the_original(service, monkeypatch):
    assert service.paste_image(scene(), origin="test")
    service.ai.prefs.enabled = False
    with pytest.raises(RuntimeError, match="AI"):
        service.set_background_method("ai")
    service.ai.prefs.enabled = True
    monkeypatch.setattr(service.ai, "usable", lambda task: task == "background")
    started = []

    def start(kind, image, key, params, done):
        started.append(kind)
        done(kind, fake_cut_out(image), key)
    monkeypatch.setattr(service.ai, "start", start)
    assert service.set_background_method("ai")
    pump(lambda: not service.background_busy)
    assert started == ["background"] and service.background_method == "ai"
    assert alpha(service)[5, 5] == 0
    # The old AI page path lands in the same state.
    assert service.set_background_method("none") and alpha(service).min() == 255
    service.ai_run("background", {})
    pump(lambda: not service.background_busy)
    assert service.background_method == "ai" and service.ai_has_original()
    assert service.ai_restore_original() and service.background_method == "none"


def test_a_failed_ai_job_does_not_leave_the_picture_busy(service, monkeypatch):
    assert service.paste_image(scene(), origin="test")
    service.ai.prefs.enabled = True
    monkeypatch.setattr(service.ai, "usable", lambda task: True)
    job = {"running": False}
    monkeypatch.setattr(service.ai, "busy", lambda: job["running"])
    monkeypatch.setattr(service.ai, "start", lambda *a, **k: job.update(running=True))  # never calls back
    assert service.set_background_method("ai")
    assert service.background_busy
    job["running"] = False
    service.aiChanged.emit()                                           # the job ended without a result
    assert not service.background_busy and service.background_method == "none"


def test_presenter_keeps_the_choice_and_a_place_only_suggests_it(quick):
    from PySide6.QtCore import QSettings
    QSettings().remove("image/autoBackground")
    quick.service.set_auto_background("off")
    try:
        assert quick.presenter.chooseTarget("draw_me")       # screenshots of a player: suggested
        assert QSettings().value("image/autoBackground") == "auto"
        assert quick.presenter.view["background"]["auto_new"] == "auto"
        assert quick.presenter.setAutoBackground("off")
        assert quick.presenter.chooseTarget("speed_draw") and quick.presenter.chooseTarget("draw_me")
        assert quick.service.auto_background == "off"         # the user's own choice stays
        assert not quick.presenter.setAutoBackground("maybe")
    finally:
        QSettings().remove("image/autoBackground")


# ----- filters, colour and the edit history (IMAGE-EDIT-002) --------------------------
@pytest.mark.parametrize("filter_id", sorted(__import__("application.image_filters", fromlist=["FILTERS"]).FILTERS))
def test_every_filter_keeps_the_removed_background(filter_id):
    from application import image_filters
    picture = fake_cut_out(scene())
    if filter_id == "trim":
        out = image_filters.apply_filter(picture, filter_id)
        assert out.size == (21, 51)                               # just the object
        with pytest.raises(ValueError):
            image_filters.apply_filter(scene(), filter_id)         # nothing transparent to trim
        return
    out = image_filters.apply_filter(picture, filter_id)
    a = np.asarray(out.convert("RGBA"))[..., 3]
    if filter_id == "rotate":
        assert out.size == (80, 60) and a[5, 5] == 0
        return
    assert out.size == picture.size and out.mode == "RGBA"
    assert a[5, 5] == 0 and a[40, 30] == 255


def test_colour_sliders_and_their_limits():
    from application import image_filters
    grey = Image.new("RGBA", (4, 4), (100, 100, 100, 255))
    brighter = image_filters.adjust(grey, {"brightness": 100})
    assert brighter.getpixel((0, 0))[0] > 180
    assert image_filters.adjust(grey, {}).getpixel((0, 0)) == (100, 100, 100, 255)
    assert image_filters.is_neutral({"brightness": 0, "contrast": 0})
    with pytest.raises(ValueError):
        image_filters.adjust(grey, {"contrast": 150})


def test_every_change_of_the_picture_is_one_step_back(service):
    assert service.paste_image(scene(), origin="test")
    assert not service.edit_history()["can_undo"]
    assert service.apply_filter("grayscale")
    pump(lambda: not service.edit_busy)
    assert service.edit_history()["undo_label"] == "Чёрно-белое"
    grey = np.asarray(service.engine.source_pil_image)
    assert (grey[..., 0] == grey[..., 1]).all()

    assert service.set_background_method("auto")
    pump(lambda: not service.edit_busy)
    assert service.background_method == "auto" and alpha(service)[5, 5] == 0

    assert service.undo_edit()                                    # the background is back, still grey
    assert service.background_method == "none" and alpha(service).min() == 255
    assert (np.asarray(service.engine.source_pil_image)[..., 0] == grey[..., 0]).all()
    assert service.undo_edit()                                    # and the colours too
    assert np.asarray(service.engine.source_pil_image)[40, 30, 0] == 200
    assert not service.undo_edit() and not service.has_picture_edits

    assert service.redo_edit() and service.redo_edit()
    assert service.background_method == "auto" and alpha(service)[5, 5] == 0
    assert not service.redo_edit()

    assert service.restore_original() and alpha(service).min() == 255
    assert service.undo_edit() and service.background_method == "auto"      # «Вернуть исходник» is a step too


def test_colour_key_and_adjustments_are_steps_with_a_live_preview(service):
    assert service.paste_image(scene(), origin="test")
    assert service.set_background_method("color")
    assert service.undo_edit() and not service.engine.background_removal_enabled
    assert service.adjustment_preview({"brightness": 0}) is None
    preview = service.adjustment_preview({"brightness": 60})
    assert preview is not None and not preview.isNull()
    assert service.apply_adjustments({"brightness": 60})
    pump(lambda: not service.edit_busy)
    assert np.asarray(service.engine.source_pil_image)[5, 5, 2] == 255
    assert service.edit_history()["undo_label"] == "Цвет и свет"
    assert not service.apply_adjustments({})                      # nothing to apply
    with pytest.raises(ValueError):
        service.apply_filter("glitter")


def test_a_new_picture_starts_a_new_history(service):
    assert service.paste_image(scene(), origin="test")
    assert service.apply_filter("invert")
    pump(lambda: not service.edit_busy)
    assert service.edit_history()["can_undo"]
    assert service.paste_image(scene(), origin="test")
    assert not service.edit_history()["can_undo"] and not service.edit_history()["can_redo"]


# ----- the colour count of new pictures (COLORS-AUTO-001) ------------------------------
def three_colours():
    img = Image.new("RGBA", (90, 60), (255, 255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle((5, 5, 30, 55), (220, 30, 30, 255))
    d.rectangle((35, 5, 60, 55), (30, 160, 40, 255))
    d.rectangle((65, 5, 85, 55), (30, 60, 220, 255))
    return img


def test_new_pictures_get_their_colour_count_after_the_background(service):
    messages = []
    service.statusChanged.connect(messages.append)
    service.set_k_clusters(30)
    service.set_auto_colors(True)
    assert service.paste_image(three_colours(), origin="test")
    assert service.engine.k_clusters == 4 and service.engine.mode == "color"     # white and three
    assert messages[-1] == "Цветов подобрано автоматически: 4."

    service.set_auto_background("auto")                   # counted on the picture that is drawn
    service.set_k_clusters(30)
    assert service.paste_image(scene(), origin="test")
    assert service.engine.k_clusters == 30                # the background is still being removed
    pump(lambda: not service.background_busy)
    assert service.engine.k_clusters == 1                 # the sky went, the red figure is left
    assert "Фон убран автоматически" in messages[-1] and "Цветов подобрано автоматически: 1." in messages[-1]

    service.set_auto_background("off")
    assert service.set_screen_snapshot(three_colours())
    assert messages[-1] == ("Снимок экрана вставлен. Обрежьте лишнее или уберите фон, если нужно. "
                            "Цветов подобрано автоматически: 4.")

    service.set_auto_colors(False)                        # off: the user's number stays
    service.set_k_clusters(30)
    assert service.paste_image(three_colours(), origin="test")
    assert service.engine.k_clusters == 30


def test_auto_colours_is_a_kept_switch_that_a_typed_count_or_preset_turns_off(quick):
    from PySide6.QtCore import QObject, QSettings
    button = quick.window.findChild(QObject, "drawingAutoColors")
    try:
        assert quick.presenter.setAutoColors(True)
        pump()
        assert QSettings().value("image/autoColors", type=bool) and quick.presenter.view["auto_colors"]
        assert button.property("selected")
        assert quick.presenter.setNumber("k_clusters", 9)  # a count typed by the user is kept
        assert not quick.service.auto_colors and not QSettings().value("image/autoColors", type=bool)
        pump()
        assert not button.property("selected")
        assert quick.presenter.setAutoColors(True)
        assert quick.presenter.setQualityPreset("fast")    # so is the count of a chosen preset
        assert not quick.service.auto_colors and quick.service.engine.k_clusters == 8
        quick.service.set_auto_colors(True)
        quick.service.set_k_clusters(5)                    # automatic: the count is not the preset's
        quick.presenter.refresh()
        assert quick.presenter.view["quality_preset"] == "fast"
        assert quick.presenter.setAutoColors(False) and "вручную" in quick.presenter.message
    finally:
        QSettings().setValue("image/autoColors", False)
