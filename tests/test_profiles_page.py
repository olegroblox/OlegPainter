"""Profiles page: chosen parts, overwrite, folder."""
from PySide6.QtCore import QUrl
from PySide6.QtQuick import QQuickItem

from tests.test_quick_presentation import quick, pump  # noqa: F401
from ui.quick import presenter as presenter_module


def _profile(quick, name):
    store = quick.controller.config_store
    descriptor = next(d for d in store.list() if d.name == name)
    return descriptor, store.load(descriptor.slug)["payload"]


def test_saved_profile_holds_only_the_chosen_parts(quick):
    presenter = quick.presenter
    defaults = [c["id"] for c in presenter.profileCategories if c["default"]]
    assert "hotkeys" not in defaults and "image" not in defaults and "drawing" in defaults
    parts = quick.window.findChild(QQuickItem, "profileParts")
    assert sorted(parts.property("chosen").toVariant() if hasattr(parts.property("chosen"), "toVariant")
                  else parts.property("chosen")) == sorted(defaults)
    assert presenter.savePreset("Для друга", ["drawing", "place"])
    descriptor, payload = _profile(quick, "Для друга")
    assert sorted(descriptor.categories) == ["drawing", "place"]
    assert "hotkeys" not in payload
    listed = next(p for p in presenter.view["presets"] if p["slug"] == descriptor.slug)
    assert "Параметры рисования" in listed["categories"] and "Горячие клавиши" not in listed["categories"]
    assert not presenter.savePreset("Пустой", [])
    assert presenter.messageError


def test_overwrite_keeps_parts_and_replaces_values(quick):
    presenter = quick.presenter
    assert presenter.savePreset("Мой Paint", ["drawing"])
    presenter.setNumber("k_clusters", 17)
    descriptor, _ = _profile(quick, "Мой Paint")
    assert presenter.preset("overwrite", descriptor.slug)
    descriptor, payload = _profile(quick, "Мой Paint")
    assert descriptor.categories == ["drawing"]
    assert payload["painter"]["k_clusters"] == 17
    assert not presenter.preset("overwrite", "missing-profile")


def test_profiles_folder_opens_in_explorer(quick, monkeypatch):
    opened = []
    monkeypatch.setattr(presenter_module, "open_url", lambda url: opened.append(url) or True)
    assert quick.presenter.openProfilesFolder()
    assert isinstance(opened[0], QUrl) and opened[0].isLocalFile()
    assert opened[0].toLocalFile().replace("\\", "/").rstrip("/") == \
        str(quick.controller.config_store.base_dir).replace("\\", "/").rstrip("/")


def test_flip_mirrors_the_picture_itself(quick, tmp_path):
    """An undoable edit: inserting the mirror as a new picture lost «Отменить»,
    the inserted original and a removed background (2026-10-05)."""
    from PIL import Image
    from tests.test_quick_presentation import pump
    source = tmp_path / "arrow.png"
    image = Image.new("RGB", (4, 2), "white")
    image.putpixel((0, 0), (255, 0, 0))
    image.save(source)
    presenter, service = quick.presenter, quick.service
    assert not presenter.flipImage("horizontal")          # nothing to flip yet
    assert presenter.openSource(QUrl.fromLocalFile(str(source)))
    pump(lambda: not service.edit_busy)       # an automatic background left by another test
    assert presenter.flipImage("horizontal")
    pump(lambda: not service.edit_busy)
    flipped = service.engine.source_pil_image
    assert flipped.getpixel((3, 0))[:3] == (255, 0, 0) and flipped.getpixel((0, 0))[:3] == (255, 255, 255)
    assert presenter.flipImage("vertical")
    pump(lambda: not service.edit_busy)
    assert service.engine.source_pil_image.getpixel((3, 1))[:3] == (255, 0, 0)
    assert not presenter.flipImage("sideways")
    assert service.edit_history()["can_undo"] and service.has_picture_edits
    assert service.undo_edit()
    assert service.engine.source_pil_image.getpixel((3, 0))[:3] == (255, 0, 0)


def test_mix_canvas_colour_can_be_picked_from_screen(quick, monkeypatch):
    # Without desktop tools (tests) the command explains itself; with them it hides
    # the window and starts the engine's click-to-sample capture.
    assert not quick.presenter.pickMixCanvasColor()
    assert "Экранные инструменты" in quick.presenter.message
    started = []
    monkeypatch.setattr(quick.controller, "desktop", object(), raising=False)
    monkeypatch.setattr(quick.presenter, "_run_desktop_capture", lambda callback: started.append(callback))
    assert quick.presenter.pickMixCanvasColor()
    assert started == [quick.service.capture_manual_mix_canvas_color]
    monkeypatch.undo()  # the controller closes its real desktop state on teardown


def test_whats_new_is_shown_once_to_people_who_used_the_window(tmp_path):
    from PySide6.QtCore import QObject, QSettings
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication
    from tests.test_quick_presentation import _open_quick
    from application.support import APP_VERSION, WHATS_NEW
    QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Basic")
    QSettings().setValue("quick/whatsNewSeen", "1.3")
    first = _open_quick(tmp_path)
    try:
        assert first.presenter.whatsNewDue and len(first.presenter.whatsNew) == len(WHATS_NEW)
        dialog = first.window.findChild(QObject, "whatsNewDialog")
        pump(lambda: dialog.property("visible"))
        dialog.close()
        pump(lambda: not first.presenter.whatsNewDue)
        assert QSettings().value("quick/whatsNewSeen") == APP_VERSION
    finally:
        first.dispose(save=False)
        first.deleteLater()



def test_newcomer_who_left_the_quick_start_gets_no_whats_new(tmp_path):
    """«Что нового» compares with 1.3: at a newcomer's second launch it meant nothing
    (audit 2026-10-05). Whoever started with the quick start has this version."""
    from PySide6.QtCore import QSettings
    from PySide6.QtQuickControls2 import QQuickStyle
    from PySide6.QtWidgets import QApplication
    from tests.test_quick_presentation import _open_quick
    from application.support import APP_VERSION
    QApplication.instance() or QApplication([])
    QQuickStyle.setStyle("Basic")
    QSettings().setValue("quick/whatsNewSeen", "")
    first = _open_quick(tmp_path, first_run=True)
    try:
        assert not first.presenter.whatsNewDue
        first.presenter.setQuickStartSeen(True)
        assert QSettings().value("quick/whatsNewSeen") == APP_VERSION
    finally:
        first.dispose(save=False)
        first.deleteLater()
        QSettings().setValue("quick/whatsNewSeen", APP_VERSION)


def test_profiles_explain_themselves_in_plain_words(quick, tmp_path):
    """A broken file said «Expecting value: line 1 column 1 (char 0)» (audit 2026-10-05)."""
    junk = tmp_path / "not-a-profile.json"
    junk.write_text("hello", encoding="utf-8")
    assert not quick.presenter.importPreset(QUrl.fromLocalFile(str(junk)))
    assert "не похож на профиль" in quick.presenter.message
    assert quick.presenter.savePreset("Мой профиль", ["drawing", "calibration"])
    listed = next(p for p in quick.presenter.view["presets"] if p["name"] == "Мой профиль")
    assert "Выбор цвета и кисть" in listed["categories"]
