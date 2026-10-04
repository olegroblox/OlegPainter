"""Qt properties/commands for the native presentation, owned by its controller."""
from dataclasses import asdict
from functools import wraps
from pathlib import Path
import ctypes
import logging
import math
import re
import sys

from PySide6.QtCore import QObject, Property, Signal, Slot, QTimer, QUrl, QEvent, Qt, QSettings, QCoreApplication
from PySide6.QtGui import QKeySequence, QShortcut, QWindow

from application.place_catalog import PLACE_OPTIONS, ALGORITHM_DISPLAY_KEYS, LEGACY_SCREEN_PLACE
from ui.helpers.hotkey_definitions import HOTKEY_DEFINITIONS
from ui.i18n import tr, i18n
from ui.helpers.theme_manager import theme_manager
from ui.quick import window_frame
from ui.quick import translation
from application.settings import SETTINGS, COLOR_METHODS, apply_setting, descriptors
from application import calibration_view
from application.workflow import preparation_step
from application import image_filters, onboarding
from application import presets
from application import support
from application import image_sources
from ui.helpers.config_payload import collect_payload, apply_payload, DEFAULT_CATEGORY_IDS, CATEGORY_INFO
from infrastructure.documents import write_document
from infrastructure import input_driver
from app_paths import get_app_paths

log = logging.getLogger(__name__)

# Title bar colours (COLORREF 0x00BBGGRR) matching Theme.qml sidebar/text.
_TITLE_BAR = {True: (0x00212121, 0x00E6E6E6), False: (0x00FAFBFB, 0x001A1A1A)}


def open_url(url):
    """Browser/Explorer for links and folders (a seam for tests)."""
    from PySide6.QtGui import QDesktopServices
    return QDesktopServices.openUrl(QUrl(url) if isinstance(url, str) else url)


def report_errors(function):
    @wraps(function)
    def wrapped(self, *args):
        if self._message_error:
            # A new command: the previous problem no longer describes the state.
            self._set_message("")
        try:
            return function(self, *args)
        except Exception as error:
            self._set_message(str(error), True)
            return False
    return wrapped


class Presenter(QObject):
    viewChanged = Signal()
    imagesChanged = Signal()
    statsChanged = Signal()
    messageChanged = Signal()
    recordingChanged = Signal()
    openRequested = Signal()
    helpRequested = Signal()
    brushSetupRequested = Signal()
    pageRequested = Signal(int)
    shellChanged = Signal()
    offerChanged = Signal()
    imageSearchRequested = Signal()
    # A picture came from search, a link, a drop or the screen: the shell shows it
    # on «Рисование» unless the user is following the quick start steps.
    imageArrived = Signal()

    def __init__(self, controller, images, parent=None):
        super().__init__(parent)
        self.controller, self.service, self.images = controller, controller.service, images
        self._view, self._stats = {}, {}
        self._presets = self._read_presets()
        self._source_url = self._preview_url = self._message = ""
        self._selection_url = ""
        self._original_url = ""
        self._adjust_url = ""
        self._message_error = False
        self._recording_code, self._recording_token = "", 0
        self._window = None
        self._shell_settings = QSettings()
        self._sidebar_collapsed = self._shell_settings.value("quick/sidebarCollapsed", False, type=bool)
        self._always_on_top = self._shell_settings.value("quick/alwaysOnTop", True, type=bool)
        self._quick_start_seen = self._shell_settings.value("quick/quickStartSeen", False, type=bool)
        # People who already used this window get «Что нового» once per version;
        # newcomers (and 1.3 users) start with the quick start instead.
        self._whats_new_due = (self._quick_start_seen and
                               self._shell_settings.value("quick/whatsNewSeen", "", type=str) != support.APP_VERSION)
        # A newcomer has not chosen where they draw yet; people who already used the
        # window keep their place selected (ONBOARD-003).
        self._target_chosen = self._shell_settings.value("quick/targetChosen", self._quick_start_seen, type=bool)
        # SETTINGS-004: newcomers see «Основное»; the other groups are one switch away.
        self._advanced_settings = self._shell_settings.value("quick/advancedSettings", False, type=bool)
        # IMAGE-EDIT-001: what happens to new pictures is the user's choice, kept between runs.
        auto_background = self._shell_settings.value("image/autoBackground", "off", type=str)
        if auto_background in ("off", "auto", "ai"):
            self.service.set_auto_background(auto_background)
        # COLORS-AUTO-001: new pictures get their colour count unless the user counts them.
        self.service.set_auto_colors(self._shell_settings.value("image/autoColors", True, type=bool))
        self._custom_places = getattr(self, "_custom_places", [])      # filled by _read_presets
        self._custom_target = self._shell_settings.value("quick/customTarget", "", type=str)
        # IMAGE-SOURCE-001: after «Найти картинку» a picture copied in the browser is offered for pasting.
        self._awaiting_image = False
        self._clipboard_offer = False
        self._clipboard_watched = False
        # Theme and language live in the session (controller preferences); the
        # QML shell only presents and changes them.
        self._translator = translation.DictTranslator(self)
        translation.activate(i18n.current_language())
        QCoreApplication.installTranslator(self._translator)
        i18n.languageChanged.connect(self._language_changed)
        theme_manager.themeChanged.connect(self._theme_changed)
        self._app_icon_url = QUrl.fromLocalFile(str(get_app_paths().assets_root / "app-icon.png")).toString()
        self._driver = input_driver.check(get_app_paths().app_root)
        self._desktop_capture_return = False
        self._desktop_capture_opened = False
        self._brush_learning_return = False
        self._shortcuts = []
        self._refresh_timer = QTimer(self)
        self._refresh_timer.setSingleShot(True)
        self._refresh_timer.timeout.connect(self.refresh)
        controller.stateChanged.connect(self.schedule_refresh)
        controller.closingChanged.connect(self.schedule_refresh)
        controller.errorOccurred.connect(lambda message: self._set_message(message, True))
        for name in ("sessionStateChanged", "configLoaded", "manualPaletteChanged", "hotkeysChanged", "brushLearningChanged"):
            getattr(self.service, name).connect(self.schedule_refresh)
        self.service.hotkeysChanged.connect(self._rebind_shortcuts)
        self.service.hotkeyCaptureChanged.connect(self._capture_changed)
        self.service.previewReady.connect(self._preview_ready)
        self.service.drawingStatsChanged.connect(self._stats_ready)
        self.service.statusChanged.connect(self._status)
        self.service.desktopInteractionChanged.connect(self._desktop_capture_finished)
        self.service.aiChanged.connect(self.schedule_refresh)
        self.service.hotkey_action_launcher = self._launch_hotkey_action
        self.service.brushLearningFinished.connect(self._brush_learning_finished)
        self.refresh()
        self._preview_ready(self.service._last_qimg)

    settingGroups = Property("QVariantList", lambda self: translation.view(descriptors()), notify=shellChanged)
    view = Property("QVariantMap", lambda self: self._view, notify=viewChanged)
    stats = Property("QVariantMap", lambda self: self._stats, notify=statsChanged)
    sourceUrl = Property(str, lambda self: self._source_url, notify=imagesChanged)
    previewUrl = Property(str, lambda self: self._preview_url, notify=imagesChanged)
    selectionUrl = Property(str, lambda self: self._selection_url, notify=imagesChanged)
    # the picture as inserted, published only while an edit replaces it (IMAGE-EDIT-001)
    originalUrl = Property(str, lambda self: self._original_url, notify=imagesChanged)
    # live «Цвет и свет» on a small copy while the sliders are moved (IMAGE-EDIT-002)
    adjustUrl = Property(str, lambda self: self._adjust_url, notify=imagesChanged)
    message = Property(str, lambda self: self._message, notify=messageChanged)
    messageError = Property(bool, lambda self: self._message_error, notify=messageChanged)
    recordingCode = Property(str, lambda self: self._recording_code, notify=recordingChanged)
    sidebarCollapsed = Property(bool, lambda self: self._sidebar_collapsed, notify=shellChanged)
    alwaysOnTop = Property(bool, lambda self: self._always_on_top, notify=shellChanged)
    quickStartSeen = Property(bool, lambda self: self._quick_start_seen, notify=shellChanged)
    darkTheme = Property(bool, lambda self: theme_manager.theme() != "light", notify=shellChanged)
    language = Property(str, lambda self: i18n.current_language(), notify=shellChanged)
    languageApplied = Signal()
    supportLinks = Property("QVariantList", lambda self: translation.view(support.view()), notify=shellChanged)
    colorMethods = Property("QVariantList", lambda self: translation.view([dict(m) for m in COLOR_METHODS]),
                            notify=shellChanged)
    qualityPresets = Property("QVariantList", lambda self: translation.view(presets.view()), notify=shellChanged)
    advancedSettings = Property(bool, lambda self: self._advanced_settings, notify=shellChanged)

    @Slot(bool)
    def setAdvancedSettings(self, enabled):
        if self._advanced_settings != bool(enabled):
            self._advanced_settings = bool(enabled)
            self._shell_settings.setValue("quick/advancedSettings", self._advanced_settings)
            self.shellChanged.emit()

    @Slot(str, result=bool)
    @report_errors
    def setQualityPreset(self, preset_id):
        """PRESETS-001: «Быстро / Баланс / Точно» change preparation and the colour order only."""
        presets.apply(self.controller, preset_id)
        self._set_auto_colors(False)              # the preset's colour count is a choice
        self.refresh()
        return True

    imageSearch = Property("QVariantMap", lambda self: self._image_search_view(), notify=shellChanged)
    clipboardOffer = Property(bool, lambda self: self._clipboard_offer, notify=offerChanged)

    def _image_search_view(self):
        view = translation.view(image_sources.view())
        services = [s["id"] for s in image_sources.SEARCH_SERVICES]
        default = "yandex" if i18n.current_language() == "ru" else "google"
        service = self._shell_settings.value("quick/imageSearchService", default, type=str)
        kind = self._shell_settings.value("quick/imageSearchKind", "any", type=str)
        view["service"] = service if service in services else default
        view["kind"] = kind if kind in dict(image_sources.KINDS) else "any"
        return view

    @Slot(str, str, str, result=bool)
    @report_errors
    def searchImages(self, service_id, query, kind):
        """Open the picture search in the browser; the found picture comes back by drop or copy."""
        url = image_sources.search_url(service_id, query, kind)
        if not open_url(url):
            raise RuntimeError("Не удалось открыть браузер. Откройте поиск картинок вручную.")
        self._shell_settings.setValue("quick/imageSearchService", service_id)
        self._shell_settings.setValue("quick/imageSearchKind", kind)
        self._awaiting_image = True
        self._watch_clipboard()
        self.shellChanged.emit()
        self._set_message(translation.text("Найдите картинку и перетащите её в окно OlegPainter "
                                           "или скопируйте: программа предложит её вставить."))
        return True

    def _watch_clipboard(self):
        if self._clipboard_watched:
            return
        from PySide6.QtGui import QGuiApplication
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.dataChanged.connect(self._clipboard_changed)
            self._clipboard_watched = True

    def _clipboard_changed(self):
        if not self._awaiting_image:
            return
        from PySide6.QtGui import QGuiApplication
        try:
            mime = QGuiApplication.clipboard().mimeData()
            offer = mime is not None and (
                mime.hasImage() or self.service._clipboard_image_file(mime) is not None
                or self.service._clipboard_image_link(mime) is not None)
        except Exception:
            log.debug("clipboard check failed", exc_info=True)
            offer = False
        if offer != self._clipboard_offer:
            self._clipboard_offer = offer
            self.offerChanged.emit()
            if offer and self._window is not None and not self._window.isActive():
                # Windows keeps the browser in front: flash the taskbar button instead.
                self._window.alert(0)

    def _set_clipboard_offer(self, offer):
        if self._clipboard_offer != offer:
            self._clipboard_offer = offer
            self.offerChanged.emit()

    @Slot(result=bool)
    @report_errors
    def acceptClipboardOffer(self):
        self._set_clipboard_offer(False)
        self._require_edit()
        self._awaiting_image = False
        self.imageArrived.emit()
        return self.action("paste_clipboard")

    @Slot()
    def dismissClipboardOffer(self):
        self._set_clipboard_offer(False)

    @Slot(str, result=bool)
    @report_errors
    def openImageUrl(self, text):
        """A link to a picture (or to a page with one), pasted into the search dialog."""
        self._require_edit()
        if image_sources.image_url_from_text(text) is None:
            raise ValueError("Это не ссылка на картинку. Ссылка начинается с http:// или https://.")
        started = self.service.open_image_url(text.strip())
        if started:
            self._awaiting_image = False
            self._set_clipboard_offer(False)
            self.imageArrived.emit()
        self.schedule_refresh()
        return bool(started)

    @Slot("QVariantList", str, str, result=bool)
    @report_errors
    def dropImage(self, urls, html, text):
        """A file from Explorer or a picture dragged out of a browser, dropped on the window."""
        self._require_edit()
        links = []
        for url in urls or []:
            url = QUrl(url) if not isinstance(url, QUrl) else url
            if url.isLocalFile():
                return self.openSource(url)
            links.append(url.toString())
        # A browser drag carries the <img> the user saw; the link may lead to a page.
        link = image_sources.image_url_from_html(html)
        for candidate in links + [text]:
            link = link or image_sources.image_url_from_text(candidate)
        if link is None:
            raise ValueError("Перетащите картинку из браузера или файл картинки.")
        started = self.service.open_image_url(link)
        if started:
            self._awaiting_image = False
            self._set_clipboard_offer(False)
            self.imageArrived.emit()
        return bool(started)

    @Slot(result=bool)
    @report_errors
    def captureScreenImage(self):
        """«Снимок экрана»: frame any part of the screen — a game, a video, a site."""
        self._require_edit()
        if self.controller.desktop is None:
            raise RuntimeError("Экранные инструменты не подключены.")
        overlays = self.controller.desktop.desktop_overlays
        self._run_desktop_capture(lambda: overlays.capture_screen_image(self._screen_snapshot_taken))
        return True

    @Slot(result=bool)
    @report_errors
    def pickMixCanvasColor(self):
        """The clean canvas colour for mixing, sampled by a click in the target program."""
        self._require_edit()
        if self.controller.desktop is None:
            raise RuntimeError("Экранные инструменты не подключены.")
        self._run_desktop_capture(self.service.capture_manual_mix_canvas_color)
        return True

    @Slot(str, result=bool)
    @report_errors
    def flipImage(self, direction):
        """Mirror the picture itself, so the stencil, preview and drawing stay one image.

        The engine's own flip flags are reset by every stencil placement (STENCIL
        geometry owns the crop); a mirrored copy of the source survives that.
        """
        self._require_edit()
        from PIL import Image
        operation = {"horizontal": Image.Transpose.FLIP_LEFT_RIGHT,
                     "vertical": Image.Transpose.FLIP_TOP_BOTTOM}.get(direction)
        if operation is None:
            raise ValueError("Неизвестное направление отражения.")
        source = getattr(self.service.engine, "source_pil_image", None)
        if source is None:
            raise ValueError("Сначала откройте картинку.")
        if not self.service.paste_image(source.transpose(operation), origin="Отражённая картинка"):
            raise RuntimeError("Не удалось отразить картинку.")
        self._set_message("Картинка отражена.")
        self.schedule_refresh()
        return True

    def _screen_snapshot_taken(self, image):
        if self.service.set_screen_snapshot(image):
            self.imageArrived.emit()
        self.schedule_refresh()

    # ----- picture processing (IMAGE-EDIT-001) ------------------------------------
    @Slot(str, result=bool)
    @report_errors
    def setBackgroundMethod(self, method):
        """«Оставить» / «Автоматически» / «Нейросетью» / «По цвету»: one background state."""
        self._require_edit()
        started = self.service.set_background_method(method)
        self.refresh()
        return bool(started)

    @Slot(str, result=bool)
    @report_errors
    def setAutoBackground(self, value):
        """«Убирать фон у новых картинок»: off, auto (without AI) or ai."""
        self.service.set_auto_background(value)
        self._shell_settings.setValue("image/autoBackground", value)
        self.refresh()
        return True

    @Slot(result=bool)
    @report_errors
    def editStencilZone(self):
        """The manual way: paint on the stencil what is drawn (the zone brush)."""
        self._require_edit()
        desktop = self.controller.desktop
        if desktop is None:
            raise RuntimeError("Экранные инструменты не подключены.")
        self._run_desktop_capture(desktop.edit_stencil_zone)
        return True

    @Slot(result=bool)
    @report_errors
    def undoEdit(self):
        """«Отменить»: the picture one step back."""
        self._require_edit()
        done = self.service.undo_edit()
        self.refresh()
        return bool(done)

    @Slot(result=bool)
    @report_errors
    def redoEdit(self):
        self._require_edit()
        done = self.service.redo_edit()
        self.refresh()
        return bool(done)

    @Slot(str, result=bool)
    @report_errors
    def applyFilter(self, filter_id):
        """One-click filter (no neural network), one step of «Отменить»."""
        self._require_edit()
        started = self.service.apply_filter(filter_id)
        self.refresh()
        return bool(started)

    @Slot("QVariantMap", result=bool)
    @report_errors
    def previewAdjust(self, values):
        self._require_edit()
        url = self.images.publish("adjust", self.service.adjustment_preview(dict(values or {})))
        if url != self._adjust_url:
            self._adjust_url = url
            self.imagesChanged.emit()
        return True

    @Slot("QVariantMap", result=bool)
    @report_errors
    def applyAdjust(self, values):
        """«Цвет и свет» → «Применить»: the slider values become one edit."""
        self._require_edit()
        started = self.service.apply_adjustments(dict(values or {}))
        self.previewAdjust({})
        self.refresh()
        return bool(started)

    @Slot(result=bool)
    @report_errors
    def restoreOriginal(self):
        """«Вернуть исходник»: the picture as inserted, without background removal or AI edits."""
        self._require_edit()
        restored = self.service.restore_original()
        self.refresh()
        return bool(restored)

    @Slot(str, result=bool)
    @report_errors
    def setHsvDirection(self, direction):
        """Which way the hue goes round the colour wheel (the old Pro 1.3 option)."""
        self._require_edit()
        if direction not in ("ccw", "cw"):
            raise ValueError("Неизвестное направление круга.")
        self.service.set_palette_rotation_direction(direction)
        self.refresh()
        return True

    @Slot("QVariantMap", result=bool)
    @report_errors
    def setOutlineFill(self, patch):
        """«Контур + заливка»: how the target program switches between pen and bucket."""
        self._require_edit()
        unknown = set(patch) - {"mode", "brush_key", "fill_key"}
        if unknown:
            raise ValueError("Неизвестная настройка переключения инструментов.")
        if "mode" in patch:
            if patch["mode"] not in ("keys", "coords"):
                raise ValueError("Неизвестный способ переключения инструментов.")
            self.service.set_outline_fill_tool_mode(patch["mode"])
        for key, setter in (("brush_key", self.service.set_outline_fill_brush_key),
                            ("fill_key", self.service.set_outline_fill_fill_key)):
            if key in patch:
                value = str(patch[key]).strip()
                if not value:
                    raise ValueError("Укажите клавишу инструмента.")
                setter(value)
        self.refresh()
        return True

    @Slot(result=bool)
    @report_errors
    def captureOutlineFillTools(self):
        self._require_edit()
        self._run_desktop_capture(self.service.start_outline_fill_coord_capture)
        return True

    @Slot(result=bool)
    @report_errors
    def showCalibration(self):
        """Show on screen every point the program will click, over the target program."""
        self._require_edit()
        if self.controller.desktop is None:
            raise RuntimeError("Экранные инструменты не подключены.")
        items = translation.view(calibration_view.items(self.service))
        if not items:
            raise ValueError("Пока нечего показывать: обведите холст и настройте выбор цвета.")
        overlays = self.controller.desktop.desktop_overlays
        overlays.show_calibration(items, hint=translation.text("Так программа видит ваши настройки. Щелчок или Esc — закрыть."))
        flash = getattr(overlays, "flash", None)
        if flash is not None and self._window is not None and self._window.isVisible():
            self._window.hide()
            flash.closed.connect(self._calibration_view_closed)
        return True

    def _calibration_view_closed(self):
        if self._window is not None and not self.controller._closing:
            self._window.show()
            self._window.requestActivate()

    @Slot(result=bool)
    @report_errors
    def resetAllHotkeys(self):
        ok, message = self.service.reset_all_hotkeys()
        if not ok:
            raise ValueError(message)
        self._set_message("Все горячие клавиши сброшены.")
        self.refresh()
        return True
    whatsNew = Property("QVariantList", lambda self: [translation.text(item) for item in support.WHATS_NEW],
                        notify=shellChanged)
    whatsNewDue = Property(bool, lambda self: self._whats_new_due, notify=shellChanged)

    @Slot()
    def dismissWhatsNew(self):
        self._shell_settings.setValue("quick/whatsNewSeen", support.APP_VERSION)
        if self._whats_new_due:
            self._whats_new_due = False
            self.shellChanged.emit()

    appVersion = Property(str, lambda self: support.APP_VERSION, constant=True)

    @Slot(str, result=bool)
    @report_errors
    def openLink(self, link_id):
        """HELP-001: support and community links; a disabled one («скоро») opens nothing."""
        item = support.link(link_id)
        if not item.get("enabled", True):
            raise ValueError("Эта ссылка появится позже.")
        open_url(item["url"])
        return True

    @Slot(result=bool)
    @report_errors
    def openLogsFolder(self):
        """Support asks for the session log: open the folder with it."""
        from ui.helpers.app_logging import session_log_path
        path = session_log_path()
        folder = path.parent if path is not None else get_app_paths().app_root / "logs"
        folder.mkdir(parents=True, exist_ok=True)
        open_url(QUrl.fromLocalFile(str(folder)))
        return True

    driverChanged = Signal()
    inputDriver = Property("QVariantMap", lambda self: dict(state=self._driver.state,
                                                            ready=self._driver.ready,
                                                            can_install=bool(self._driver.installer)),
                           notify=driverChanged)

    @Slot(str, result=bool)
    @report_errors
    def driverAction(self, command):
        """DRIVER-001: install/uninstall (bundled installer, UAC) or open the author's page; recheck.
        QML asks for an informed yes first: the driver is third-party and may misbehave."""
        if command == "install":
            if self._driver.installer:
                if not input_driver.run_installer(self._driver.installer):
                    raise RuntimeError("Установка не началась: подтвердите запрос Windows «Разрешить изменения» или запустите установщик вручную.")
                self._set_message("Установщик запущен. Когда он закончит, перезагрузите компьютер.")
            else:
                open_url(input_driver.OFFICIAL_PAGE)
                self._set_message("Скачайте Interception.zip, распакуйте, запустите «command line installer\\install-interception.exe /install» от имени администратора и перезагрузите компьютер.")
        elif command == "uninstall":
            if self._driver.installer:
                if not input_driver.run_installer(self._driver.installer, "uninstall"):
                    raise RuntimeError("Удаление не началось: подтвердите запрос Windows «Разрешить изменения» или запустите установщик вручную.")
                self._set_message("Удаление драйвера запущено. Когда установщик закончит, перезагрузите компьютер.")
            else:
                open_url(input_driver.OFFICIAL_PAGE)
                self._set_message("Запустите «install-interception.exe /uninstall» от имени администратора и перезагрузите компьютер.")
        elif command == "recheck":
            self._driver = input_driver.check(get_app_paths().app_root)
            self.driverChanged.emit()
            if self._driver.ready:
                self._set_message("Драйвер управления мышью работает.")
        else:
            raise ValueError("Неизвестная команда драйвера.")
        return True

    @Slot(str, result=bool)
    @report_errors
    def setLanguage(self, language):
        self.controller.set_language(language)
        return True

    def _language_changed(self, language):
        translation.activate(language)
        self._set_message("")
        self.languageApplied.emit()  # the application retranslates the QML engine
        self.shellChanged.emit()
        self.refresh()

    def _theme_changed(self, _theme):
        self._apply_title_bar()
        self.shellChanged.emit()
    appIconUrl = Property(str, lambda self: self._app_icon_url, constant=True)

    @Slot(bool)
    def setDarkTheme(self, dark):
        theme_manager.set_theme("dark" if dark else "light")

    def _apply_title_bar(self):
        """Colour the native Windows title bar like the app (DWM; Windows 10 1809+/11)."""
        if sys.platform != "win32" or self._window is None:
            return
        try:
            hwnd = int(self._window.winId())
            dwm = ctypes.windll.dwmapi
            is_dark = theme_manager.theme() != "light"
            dark = ctypes.c_int(1 if is_dark else 0)
            dwm.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(dark), ctypes.sizeof(dark))  # immersive dark mode
            caption, text = (ctypes.c_uint(value) for value in _TITLE_BAR[is_dark])
            dwm.DwmSetWindowAttribute(hwnd, 35, ctypes.byref(caption), ctypes.sizeof(caption))  # Windows 11 only
            dwm.DwmSetWindowAttribute(hwnd, 36, ctypes.byref(text), ctypes.sizeof(text))
        except Exception:
            log.debug("title bar colour not applied", exc_info=True)

    @Slot(bool)
    def setQuickStartSeen(self, seen):
        if self._quick_start_seen != seen:
            self._quick_start_seen = seen
            self._shell_settings.setValue("quick/quickStartSeen", seen)
            self.shellChanged.emit()

    @Slot(str, result=bool)
    @report_errors
    def chooseTarget(self, target_id):
        """Quick start: pick where the user draws (a Roblox place or anything else)."""
        self._require_edit()
        if target_id.startswith("custom:"):
            slug = target_id[len("custom:"):]
            if not any(p["slug"] == slug for p in self._custom_places):
                raise ValueError("Это место больше не сохранено.")
            if not self.preset("apply", slug):
                return False
            # The place is applied last and sets its own fixed values (draw_delay of
            # «Нарисуй меня!»...): an own place keeps what the user saved.
            painter = (self.controller.config_store.load(slug).get("payload") or {}).get("painter")
            if isinstance(painter, dict) and painter:
                apply_payload(self.service, {"painter": painter})
            self._custom_target = slug
            self._shell_settings.setValue("quick/customTarget", slug)
            self._mark_target_chosen()
            self.refresh()
            return True
        target = next((t for t in onboarding.TARGETS if t["id"] == target_id), None)
        if target is None:
            raise ValueError("Неизвестное место рисования.")
        self._custom_target = ""
        self._shell_settings.setValue("quick/customTarget", "")
        # A Roblox place always re-applies its colour method; "other" keeps the
        # method the user already chose for the universal profile.
        if target["id"] != "other" or self.controller.state.preparation.current_place_id != target["place"]:
            self.controller.profiles.select(target["place"], None)
        from application.place_catalog import PLACE_PRESETS
        if PLACE_PRESETS.get(target["place"], {}).get("snapshot_cutout") and not self._shell_settings.contains("image/autoBackground"):
            # A place drawn from screenshots of a character suggests removing their
            # background — visibly, once; the user's own choice is never overridden.
            self.setAutoBackground("auto")
        self._mark_target_chosen()
        self.refresh()
        return True

    def _mark_target_chosen(self):
        if not self._target_chosen:
            self._target_chosen = True
            self._shell_settings.setValue("quick/targetChosen", True)

    @Slot(str, result=bool)
    @report_errors
    def quickStartAction(self, code):
        if code == "open_brush":
            self.pageRequested.emit(5)
            return True
        if code == "open_palette":
            self.pageRequested.emit(4)
            return True
        if code == "learn_speed":
            return self.brushCommand("learn_speed")
        if code == "capture_scratch":
            return self.captureBrush("scratch", 0)
        if code == "search_image":
            self.imageSearchRequested.emit()
            return True
        if code == "screen_snapshot":
            return self.captureScreenImage()
        return self.action(code)

    @Slot(bool)
    def setAlwaysOnTop(self, enabled):
        if self._always_on_top != enabled:
            self._always_on_top = enabled
            self._shell_settings.setValue("quick/alwaysOnTop", enabled)
            self.shellChanged.emit()
            # Qt rewrites the window styles when the stay-on-top flag changes
            QTimer.singleShot(0, lambda: window_frame.apply(self._window))

    @Slot(bool)
    def setSidebarCollapsed(self, collapsed):
        if self._sidebar_collapsed != collapsed:
            self._sidebar_collapsed = collapsed
            self._shell_settings.setValue("quick/sidebarCollapsed", collapsed)
            self.shellChanged.emit()

    def schedule_refresh(self, *_):
        if not self._refresh_timer.isActive():
            self._refresh_timer.start(0)

    def refresh(self):
        state = self.controller.state
        profile = self.service.get_hotkeys()
        config = self.service.snapshot_painter_config()
        # QVariantMap does not recursively turn Python tuples into JS arrays.
        data = {key: list(value) if isinstance(value, tuple) else value
                for key, value in asdict(state.preparation).items()}
        data.update(phase=state.drawing.value, run_id=state.run_id, can_start=state.can_start,
                    closing=state.closing, close_error=self.controller._close_error,
                    close_phase=self.controller._close_phase,
                    can_edit=state.can_edit_source, capture=state.capture.to_payload(),
                    desktop_mode=state.desktop_mode,
                    next_step=preparation_step(state, self.service),
                    settings={key: config[key] for key in SETTINGS},
                    quality_preset=presets.detect(config, auto_colors=self.service.auto_colors),
                    auto_colors=self.service.auto_colors,
                    image_downloading=self.service.image_download_active or self.service.background_busy,
                    palette_count=len(getattr(self.service.engine, "color_palette", None) or []),
                    setting_visibility={key: setting.is_visible(config) for key, setting in SETTINGS.items()},
                    setting_editability={key: setting.is_editable(state) for key, setting in SETTINGS.items()},
                    background=dict(enabled=config["background_removal_enabled"],
                                    mode=config["background_removal_mode"],
                                    alpha_threshold=config["background_alpha_threshold"],
                                    color_tolerance=config["background_color_tolerance"],
                                    reference_rgb=config["background_reference_rgb"],
                                    method=self.service.background_method,
                                    auto_new=self.service.auto_background,
                                    busy=self.service.background_busy,
                                    has_original=self.service.has_picture_edits),
                    edits=dict(self.service.edit_history(), busy=self.service.edit_busy),
                    filters=image_filters.catalog(),
                    places=[dict(id=p["id"], label=tr(p["key"])) for p in PLACE_OPTIONS if p["id"] != LEGACY_SCREEN_PLACE],
                    algorithms=[dict(id=key, label=tr(value)) for key, value in ALGORITHM_DISPLAY_KEYS.items()],
                    hotkeys=[dict(code=d.code, label=tr(d.label_key) if d.label_key else d.label,
                                  scope=d.scope, sequence=profile[d.scope].get(d.code, ""))
                             for d in HOTKEY_DEFINITIONS],
                    bindings={key: value for group in profile.values() for key, value in group.items()},
                    manual_palette=self.service.manual_palette_snapshot(),
                    hsv_direction=str(getattr(self.service.engine, "palette_rotation_direction_calib", "ccw") or "ccw"),
                    outline_fill=calibration_view.outline_fill(self.service),
                    area_rect=calibration_view.area_rect(self.service),
                    manual_mix=self.service.manual_mix_snapshot(),
                    input_sequences=self.service.input_sequences_snapshot(),
                    brush={key: list(value) if isinstance(value, tuple) else value
                           for key, value in self.service.engine.get_dynamic_brush_settings().items()
                           if key not in ("profile", "calibration", "cached_calibration")},
                    brush_learning=self.service.brush_learning_snapshot(),
                    ai=dict(self.service.ai.snapshot(i18n.current_language()),
                            has_original=self.service.ai_has_original()),
                    presets=self._presets)
        self._restore_after_drawing()
        brush_settings = self.service.engine.get_dynamic_brush_settings()
        data["quick_start"] = onboarding.build(
            state, self.service,
            brush_ready=bool(brush_settings.get("enabled")) and brush_settings.get("profile_state") == "ready",
            language=i18n.current_language(), target_chosen=self._target_chosen,
            custom_places=self._custom_places, custom_target=self._custom_target)
        data = translation.view(data)
        if data != self._view:
            self._view = data
            self.viewChanged.emit()
        self._update_shortcut_context()
        url = self.images.publish("source", self.service._last_source_qimg)
        if url != self._source_url:
            self._source_url = url
            self.imagesChanged.emit()
        selection = self.images.publish("selection", self.service.ai_selection_preview())
        if selection != self._selection_url:
            self._selection_url = selection
            self.imagesChanged.emit()
        original = self.images.publish("original", self.service.picture_original_qimage())
        if original != self._original_url:
            self._original_url = original
            self.imagesChanged.emit()

    def _preview_ready(self, image):
        url = self.images.publish("preview", image)
        if url != self._preview_url:
            self._preview_url = url
            self.imagesChanged.emit()
        self.schedule_refresh()

    def _stats_ready(self, data):
        if data != self._stats:
            self._stats = dict(data)
            self.statsChanged.emit()

    def _set_message(self, message, error=False):
        self._message, self._message_error = translation.text(str(message)), bool(error)
        self.messageChanged.emit()

    @Slot()
    def dismissMessage(self):
        self._set_message("")

    def _status(self, message):
        text = str(message).strip()
        lowered = text.lower()
        # Строки [INFO]/info: — технический журнал движка на английском; он остаётся в логе.
        if lowered.startswith(("[info]", "info:")):
            return
        # Warnings are refusals the user has to act on ("clipboard has no picture"):
        # same red line as errors, without the technical prefix.
        for prefix in ("[error]", "error:", "[ошибка]", "[критично]", "[warn]", "warn:", "[warning]", "warning:"):
            if lowered.startswith(prefix):
                self._set_message(text[len(prefix):].strip(), True)
                return
        self._set_message(text)

    def _require_edit(self):
        if not self.controller.state.can_edit_source:
            raise RuntimeError("Сначала завершите рисование или активный экранный инструмент.")

    @Slot(QUrl, result=bool)
    @report_errors
    def openSource(self, url):
        self._require_edit()
        if not url.isLocalFile():
            raise ValueError("Выберите локальный файл изображения.")
        from PIL import Image
        try:
            result = self.service.open_image(url.toLocalFile())
        except (OSError, ValueError, Image.DecompressionBombError) as error:  # not a picture, damaged or huge
            log.warning("Image not opened: %s", url.toLocalFile(), exc_info=True)
            raise ValueError("Не удалось открыть файл: это не картинка, файл повреждён или слишком большой.") from error
        self.refresh()
        return bool(result)

    _SCREEN_TOOL_ACTIONS = ("select_area", "capture_hex_palette", "edit_stencil", "define_manual_palette",
                            "define_app_layers", "record_pre_color_actions", "record_post_color_actions")

    @Slot(str, result=bool)
    @report_errors
    def action(self, code):
        if self.controller._closing:
            raise RuntimeError("Приложение завершает работу.")
        if self.service.brush_learning_active and code not in ("stop", "show_help"):
            raise RuntimeError("Дождитесь завершения обучения кисти или остановите его.")
        if code == "open_file":
            self._require_edit()
            self.openRequested.emit()
        elif code == "paste_clipboard":
            self._require_edit()
            self.service.paste_from_clipboard()
        elif code == "show_help":
            self.helpRequested.emit()
        elif code in ("calibrate_color_circle", "calibrate_brightness_slider", "calibrate_screen_palette",
                      "calibrate_alpha_slider", "calibrate_wheel_square"):
            self._require_edit()
            self._run_desktop_capture(getattr(self.service, code))
        elif code in self.service._global_action_map():
            if code not in ("start_pause", "stop", "toggle_overlay", "toggle_stencil", "edit_stencil",
                            "define_manual_palette", "define_app_layers",
                            "record_pre_color_actions", "record_post_color_actions"):
                self._require_edit()
            if code == "start_pause":
                self._step_aside_for_drawing()
            if code in self._SCREEN_TOOL_ACTIONS and self.controller.desktop is not None:
                # The user works in the target program: the window must not cover it.
                self._run_desktop_capture(self.service._global_action_map()[code])
            else:
                self.service._global_action_map()[code]()
        else:
            raise ValueError("Неизвестная команда: " + code)
        self.schedule_refresh()
        return True

    @Slot(result=bool)
    @report_errors
    def nextStep(self):
        step = preparation_step(self.controller.state, self.service)
        command = step["action"]
        if command == "open_palette":
            self.pageRequested.emit(4)
        elif command == "finish_capture":
            capture = self.controller.state.capture
            if capture.kind == "palette":
                self.service.engine.finish_manual_palette_capture()
            elif capture.kind == "layers":
                self.service.input_sequence_command("layers", "finish", "")
            elif capture.kind == "extra":
                self.service.input_sequence_command(capture.slot, "finish", "")
            self.service._emit_capture_state()
        elif command:
            return self.action(command)
        else:
            return False
        self.schedule_refresh()
        return True

    @Slot(str, str, result=bool)
    @report_errors
    def selectProfile(self, place, algorithm):
        self._require_edit()
        self.controller.profiles.select(place, algorithm or None)
        self._mark_target_chosen()
        self.refresh()
        return True

    @Slot(str, "QVariant", result=bool)
    @report_errors
    def setSetting(self, key, value):
        apply_setting(self.controller, key, value)
        if key == "k_clusters":
            self._set_auto_colors(False)          # a count typed by the user is kept
        self.refresh()
        return True

    @Slot(str, float, result=bool)
    def setNumber(self, key, value):
        return self.setSetting(key, value)

    @Slot(result=bool)
    @report_errors
    def autoColors(self):
        """Recommended colour count: distinct colours that survive the current
        merge setting (engine.recommend_color_count)."""
        self._require_edit()
        mode, count = self.service.apply_recommended_colors()
        self.refresh()
        if mode == "bw":
            self._set_message("Картинка чёрно-белая: включён режим «Чёрно-белый». Его можно сменить в настройках.")
        else:
            self._set_message(f"Цветной режим, заметно разных цветов: {count}. Число можно изменить вручную.")
        return True

    def _set_auto_colors(self, enabled):
        if bool(enabled) != self.service.auto_colors:
            self.service.set_auto_colors(enabled)
        self._shell_settings.setValue("image/autoColors", bool(enabled))

    @Slot(bool, result=bool)
    @report_errors
    def setAutoColors(self, enabled):
        """«Авто» at «Цветов» as a switch (COLORS-AUTO-001): on, the count of the
        current picture and of every new one is chosen by the picture (after its
        automatic background); off, the user's number stays."""
        if enabled and self.service.engine.source_pil_image is not None:
            self.autoColors()
        self._set_auto_colors(enabled)
        if not enabled:
            self._set_message("Число цветов выбирается вручную, новые картинки его не меняют.")
        self.refresh()
        return True

    @Slot(str, float, result=bool)
    @report_errors
    def setDisplayedSetting(self, key, value):
        if key not in SETTINGS:
            raise ValueError("Неизвестная настройка.")
        return self.setSetting(key, value / SETTINGS[key].display_scale)

    @Slot("QVariantMap", result=bool)
    @report_errors
    def setManualMix(self, patch):
        self._require_edit()
        self.service.update_manual_mix(patch)
        self.refresh()
        return True

    def open_brush_setup(self, payload=None):
        self.brushSetupRequested.emit()
        if self._window is not None:
            self._window.show()
            self._window.requestActivate()
        self._set_message("Обучите кисть, затем переключитесь в целевую программу и запустите рисунок.")

    @Slot("QVariantMap", result=bool)
    @report_errors
    def setBrush(self, patch):
        self._require_edit()
        self.service.update_brush_settings(patch)
        self.refresh()
        self._set_message("")
        return True

    @Slot(str, str, str, result=bool)
    @report_errors
    def sequenceCommand(self, key, command, revision):
        if self.controller._closing:
            raise RuntimeError("Приложение завершает работу.")
        if command == "start" and self.controller.desktop is not None:
            self._run_desktop_capture(lambda: self.service.input_sequence_command(key, command, revision))
        else:
            self.service.input_sequence_command(key, command, revision)
        self.refresh()
        return True

    @Slot(str, bool, str, result=bool)
    @report_errors
    def setSequenceEnabled(self, key, enabled, revision):
        self._require_edit()
        self.service.set_sequence_enabled(key, enabled, revision)
        self.refresh()
        return True

    @Slot(str, float, result=bool)
    @report_errors
    def captureBrush(self, kind, value):
        self._require_edit()
        self._run_desktop_capture(lambda: self.service.request_brush_capture(kind, value))
        return True

    def _launch_hotkey_action(self, code, callback):
        """Global hotkeys for screen tools (F9, F1, …) step the window aside like
        the buttons do. A hotkey that finishes a tool finds the window already
        hidden and just runs; the pending return then shows the window again."""
        window = self._window
        if (code in self._SCREEN_TOOL_ACTIONS and self.controller.desktop is not None
                and window is not None and window.isVisible()
                and window.visibility() != QWindow.Minimized):
            self._run_desktop_capture(callback)
        else:
            if code == "start_pause" and self.controller.state.can_start:
                # F3 from the game: the window is pinned on top by default and may
                # cover the canvas — step aside exactly as the start button does.
                self._step_aside_for_drawing()
            callback()

    def _step_aside_for_drawing(self):
        """The start button is pressed in this window, which usually covers the
        canvas (and may be pinned on top): minimise it for the drawing."""
        window = self._window
        if (window is None or self.controller.desktop is None or self.controller.state.drawing.value != "idle"
                and not self.controller.state.drawing.terminal):
            return
        if window.isVisible() and window.visibility() != QWindow.Minimized:
            self._drawing_return = True
            window.showMinimized()

    def _restore_after_drawing(self):
        if getattr(self, "_drawing_return", False) and self.controller.state.drawing.terminal:
            self._drawing_return = False
            if self._window is not None and not self.controller._closing:
                self._window.showNormal()
                self._window.requestActivate()

    def _run_desktop_capture(self, callback):
        if self.controller.desktop is None:
            raise RuntimeError("Экранные инструменты не подключены.")
        self._desktop_capture_return = True
        self._desktop_capture_opened = False
        if self._window is not None:
            self._window.hide()
        try:
            callback()
        finally:
            if self._desktop_capture_opened and self.service.desktop_interaction in ("", "measure"):
                # opened and already failed or finished inside the call
                self._desktop_capture_finished("", checked=True)
            else:
                # Coordinate captures may report their mode through queued
                # signals: give them a moment before calling the tool cancelled.
                QTimer.singleShot(400, self._desktop_capture_check)

    def _desktop_capture_check(self):
        if not self._desktop_capture_opened and self.service.desktop_interaction in ("", "measure"):
            self._desktop_capture_finished("", checked=True)

    def _desktop_capture_finished(self, mode, checked=False):
        # Tools pass through "" while switching inside one call (area placement:
        # stencil -> "" -> stencil): decide on the next event loop turn, only
        # after the tool opened and then stayed closed.
        # Palette measurement reads the target window itself: the user may watch it.
        if mode and mode != "measure":
            self._desktop_capture_opened = True
            return
        if self._desktop_capture_return and self._desktop_capture_opened and not checked:
            QTimer.singleShot(0, lambda: self.service.desktop_interaction not in ("", "measure")
                              or self._desktop_capture_finished("", checked=True))
            return
        if self._desktop_capture_return and checked:
            self._desktop_capture_return = False
            if self._window is not None and not self.controller._closing:
                self._window.show()
                self._window.requestActivate()

    @Slot(str, result=bool)
    @report_errors
    def brushCommand(self, command):
        if command == "cancel":
            self.service.cancel_brush_learning()
        elif command in ("learn", "learn_speed"):
            self._require_edit()
            self._brush_learning_return = self._window is not None and self._window.isVisible()
            if self._brush_learning_return:
                self._window.showMinimized()
            try:
                if command == "learn_speed":
                    self.service.start_speed_learning()
                else:
                    self.service.start_brush_learning()
            except Exception:
                self._brush_learning_finished(False)
                raise
        elif command == "clear_points":
            self._require_edit()
            self.service.clear_dynamic_brush_points()
        elif command == "reset":
            self._require_edit()
            self.service.reset_dynamic_brush_profile()
        else:
            raise ValueError("Неизвестная команда кисти.")
        self.refresh()
        return True

    def _brush_learning_finished(self, _success):
        if self._brush_learning_return:
            self._brush_learning_return = False
            if self._window is not None and not self.controller._closing:
                self._window.showNormal()
                self._window.requestActivate()

    @Slot(str, str, result=bool)
    def setChoice(self, key, value):
        return self.setSetting(key, value)

    # ----- local AI (AI-001) ---------------------------------------------------
    @Slot(str, "QVariant", result=bool)
    @report_errors
    def aiSet(self, key, value):
        ai = self.service.ai
        if key == "enabled":
            ai.set_enabled(bool(value))
        elif key == "device":
            ai.set_device(str(value))
        elif key == "background_model":
            ai.set_background_model(str(value))
        elif key == "depth_order":
            self._require_edit()
            ai.set_depth_order(bool(value))
            # Depth decides back-to-front order through the semantic order mode.
            self.service.engine._semantic_saliency_cache = None
            self.service.set_semantic_order_mode("bg_first" if value else "off")
        else:
            raise ValueError("Неизвестная настройка AI.")
        self.refresh()
        return True

    @Slot(str, str, result=bool)
    @report_errors
    def aiModel(self, command, model_id):
        ai = self.service.ai
        if command == "install":
            ai.install(model_id)
        elif command == "cancel":
            ai.cancel_install(model_id)
        elif command == "remove":
            ai.remove(model_id)
        else:
            raise ValueError("Неизвестная команда модели.")
        self.refresh()
        return True

    @Slot(str, "QVariantMap", result=bool)
    @report_errors
    def aiRun(self, kind, params):
        self._require_edit()
        self.service.ai_run(kind, dict(params or {}))
        self.refresh()
        return True

    @Slot(float, float, bool, result=bool)
    @report_errors
    def aiSelect(self, x, y, positive):
        self._require_edit()
        self.service.ai_select(x, y, positive)
        return True

    @Slot(str, result=bool)
    @report_errors
    def aiCommand(self, command):
        if command == "cancel":
            self.service.ai.cancel_job()
        elif command == "clear_selection":
            self.service.ai.clear_selection()
        elif command == "restore":
            self._require_edit()
            self.service.restore_original()
        elif command == "open_folder":
            from PySide6.QtGui import QDesktopServices
            self.service.ai.store.root.mkdir(parents=True, exist_ok=True)
            QDesktopServices.openUrl(QUrl.fromLocalFile(str(self.service.ai.store.root)))
        else:
            raise ValueError("Неизвестная команда AI.")
        self.refresh()
        return True

    @Slot("QVariantMap", result=bool)
    @report_errors
    def setBackground(self, patch):
        self._require_edit()
        if set(patch) - {"enabled", "mode", "alpha_threshold", "color_tolerance", "reference_rgb"}:
            raise ValueError("Неизвестная настройка фона.")
        self.service.set_background_removal_state(**patch, invalidate=True)
        self.refresh()
        self._set_message("")
        return True

    @Slot(str, result=bool)
    @report_errors
    def setBackgroundColor(self, text):
        self._require_edit()
        if not re.fullmatch(r"#?[0-9a-fA-F]{6}", text.strip()):
            raise ValueError("Введите цвет в формате #RRGGBB, например #FFFFFF.")
        value = text.strip().lstrip("#")
        return self.setBackground(dict(mode="picked", reference_rgb=[int(value[i:i+2], 16) for i in (0, 2, 4)]))

    @Slot(float, float, str, result=bool)
    @report_errors
    def pickBackground(self, x, y, source_url):
        self._require_edit()
        # Coordinates are normalized to the displayed source, not the screen or
        # quantized preview. Reject a click queued before the source was replaced.
        if source_url != self._source_url or not source_url:
            raise ValueError("Изображение изменилось. Выберите цвет заново.")
        if not all(math.isfinite(v) and 0 <= v < 1 for v in (x, y)):
            raise ValueError("Нажмите внутри изображения.")
        current = self.service._last_source_qimg
        if current is None or current.isNull():
            raise ValueError("Сначала откройте изображение.")
        image = self.images.requestImage(source_url.removeprefix("image://painter/"), None, None)
        if image.isNull() or image.cacheKey() != current.cacheKey():
            raise ValueError("Изображение изменилось. Выберите цвет заново.")
        color = image.pixelColor(int(x * image.width()), int(y * image.height()))
        if color.alpha() == 0:
            raise ValueError("Пиксель полностью прозрачный. Выберите видимый цвет.")
        return self.setBackground(dict(mode="picked", reference_rgb=[color.red(), color.green(), color.blue()]))

    @Slot(str, int, int, str, str, str, result=bool)
    @report_errors
    def editPalette(self, operation, index, revision, x, y, color):
        self._require_edit()
        self.service.edit_manual_palette(operation, index, revision, x, y, color)
        self.refresh()
        self._set_message("Палитра обновлена.")
        return True

    @Slot(result=bool)
    @report_errors
    def appendPalette(self):
        self._require_edit()
        if self.controller.desktop is not None:
            # the colours are in the target program: the window must not cover them
            self._run_desktop_capture(self.service.append_manual_palette_capture)
        else:
            self.service.append_manual_palette_capture()
        self.schedule_refresh()
        return True

    # Keys and screen points are personal: a profile made to share leaves them out (P1 #9).
    _SHARED_OFF = {"hotkeys", "image"}

    profileCategories = Property("QVariantList", lambda self: translation.view(
        [dict(id=c["id"], label=c["label"], detail=c["description"], default=c["id"] not in self._SHARED_OFF)
         for c in CATEGORY_INFO]), notify=shellChanged)

    def _read_presets(self):
        labels = {c["id"]: c["label"] for c in CATEGORY_INFO}
        presets = [dict(slug=d.slug, name=d.name, quick_place=onboarding.QUICK_PLACE in d.categories,
                        categories=translation.text(", ".join(labels.get(c, c) for c in d.categories if c in labels)))
                   for d in self.controller.config_store.list() if not d.slug.startswith("__")]
        self._custom_places = [p for p in presets if p["quick_place"]]
        return presets

    @Slot(str, result=bool)
    @report_errors
    def saveQuickPlace(self, name):
        """«Сохранить как своё место»: the current setup becomes a quick start tile (PLACES-003).

        Keys and the picture stay personal: an own place keeps how to draw there,
        not what is drawn."""
        self._require_edit()
        if not name.strip():
            raise ValueError("Введите название игры или программы.")
        parts = {"drawing", "area", "calibration", "palette", "layers", "place"}
        payload = collect_payload(self.service, self.controller.profiles, parts)
        descriptor = self.controller.config_store.save_new(
            name.strip(), sorted(parts | {onboarding.QUICK_PLACE}), payload)
        self._custom_target = descriptor.slug
        self._shell_settings.setValue("quick/customTarget", descriptor.slug)
        self.refreshPresets()
        self._set_message("Место «" + descriptor.name + "» сохранено: оно появилось в «Быстром старте».")
        return True

    @Slot(str, "QVariantList", result=bool)
    @report_errors
    def savePreset(self, name, categories):
        """New profile with the chosen parts: what it will change when applied."""
        self._require_edit()
        known = {c["id"] for c in CATEGORY_INFO}
        chosen = sorted({str(c) for c in categories or []} & known)
        if not name.strip():
            raise ValueError("Введите имя профиля.")
        if not chosen:
            raise ValueError("Отметьте хотя бы один раздел профиля.")
        payload = collect_payload(self.service, self.controller.profiles, set(chosen))
        self.controller.config_store.save_new(name.strip(), chosen, payload)
        self.refreshPresets()
        self._set_message("Профиль сохранён.")
        return True

    @Slot(result=bool)
    @report_errors
    def openProfilesFolder(self):
        folder = self.controller.config_store.base_dir
        folder.mkdir(parents=True, exist_ok=True)
        if not open_url(QUrl.fromLocalFile(str(folder))):
            raise RuntimeError("Не удалось открыть папку профилей: " + str(folder))
        return True

    @Slot(result=bool)
    @report_errors
    def refreshPresets(self):
        self._presets = self._read_presets()
        self.refresh()
        return True

    @Slot(str, str, result=bool)
    @report_errors
    def preset(self, operation, name):
        self._require_edit()
        store = self.controller.config_store
        if operation == "save":
            if not name.strip():
                raise ValueError("Введите имя профиля.")
            payload = collect_payload(self.service, self.controller.profiles, DEFAULT_CATEGORY_IDS)
            store.save_new(name.strip(), sorted(DEFAULT_CATEGORY_IDS), payload)
        elif operation == "apply":
            payload = store.load(name).get("payload")
            if not isinstance(payload, dict):
                raise ValueError("Профиль повреждён.")
            # The first QML slice stored a flat drawing profile. Keep those
            # documents usable, while all new files use the existing format.
            if not any(key in payload for key in ("painter", "place", "hotkeys", "session")):
                if not any(key in payload for key in self.service.snapshot_painter_config()):
                    raise ValueError("В профиле нет поддерживаемых настроек.")
                payload = {"painter": payload}
            results = apply_payload(self.service, payload, settings_page=self.controller.profiles)
            failures = [f"{section}: {message}" for section, (ok, message) in results.items() if not ok]
            if failures:
                self.controller.refresh_preparation()
                self.refresh()
                raise RuntimeError("Профиль применён не полностью: " + "; ".join(failures))
            if not results:
                raise ValueError("В профиле нет поддерживаемых настроек.")
            if "place" in results:
                self._mark_target_chosen()
        elif operation == "overwrite":
            # Same parts as when the profile was made; only the values are replaced.
            descriptor = next((d for d in store.list() if d.slug == name), None)
            if descriptor is None:
                raise ValueError("Профиль не найден.")
            categories = set(descriptor.categories) or set(DEFAULT_CATEGORY_IDS)
            payload = collect_payload(self.service, self.controller.profiles, categories)
            store.update(descriptor.slug, descriptor.name, sorted(categories), payload)
            self._set_message("Профиль перезаписан текущими настройками.")
        elif operation == "delete":
            store.delete(name)
        else:
            raise ValueError("Неизвестная операция профиля.")
        self.controller.refresh_preparation()
        self.refreshPresets()
        return True

    @staticmethod
    def _local_path(url):
        if not url.isLocalFile():
            raise ValueError("Выберите локальный файл.")
        return Path(url.toLocalFile())

    @Slot(QUrl, result=bool)
    @report_errors
    def importPreset(self, url):
        self._require_edit()
        descriptor = self.controller.config_store.import_external(self._local_path(url))
        self.refreshPresets()
        self._set_message("Импортирован профиль: " + descriptor.name)
        return True

    @Slot(str, QUrl, result=bool)
    @report_errors
    def exportPreset(self, slug, url):
        document = self.controller.config_store.load(slug)
        write_document(self._local_path(url), document)
        self._set_message("Профиль экспортирован.")
        return True

    def attach_window(self, window):
        self._window = window
        self._apply_title_bar()
        window_frame.apply(window)
        window.installEventFilter(self)
        window.activeFocusItemChanged.connect(self._update_shortcut_context)
        self._rebind_shortcuts()

    def _rebind_shortcuts(self, *_):
        for shortcut in self._shortcuts:
            shortcut.setEnabled(False)
            shortcut.deleteLater()
        self._shortcuts.clear()
        if self._window is not None:
            for code, sequence in self.service.get_hotkeys()["app"].items():
                if sequence:
                    shortcut = QShortcut(QKeySequence(sequence), self._window)
                    shortcut.setAutoRepeat(False)
                    shortcut.activated.connect(lambda action=code: self.action(action)
                                               if not self._recording_code else None)
                    self._shortcuts.append(shortcut)
        self._update_shortcut_context()

    def _update_shortcut_context(self, *_):
        focus = self._window.activeFocusItem() if self._window is not None else None
        typing = focus is not None and (focus.inherits("QQuickTextInput") or focus.inherits("QQuickTextEdit"))
        for shortcut in self._shortcuts:
            shortcut.setEnabled(not typing and not self._recording_code and not self.controller._closing)

    @Slot(str, result=bool)
    @report_errors
    def beginRecording(self, code):
        self._recording_token = self.service.begin_hotkey_capture()
        self._recording_code = code
        self._update_shortcut_context()
        self.recordingChanged.emit()
        return True

    @Slot()
    def cancelRecording(self):
        self.service.end_hotkey_capture(self._recording_token)

    def _capture_changed(self, payload):
        if not payload["active"] or payload["token"] != self._recording_token:
            self._recording_code = ""
            self._update_shortcut_context()
            self.recordingChanged.emit()

    @Slot(str, str, result=bool)
    @report_errors
    def changeHotkey(self, code, sequence):
        ok, message = (self.service.reset_hotkey(code) if sequence == "default"
                       else self.service.set_hotkey(code, sequence))
        if not ok:
            raise ValueError(message)
        self.refresh()
        return True

    def eventFilter(self, obj, event):
        if self._recording_code:
            if event.type() == QEvent.WindowDeactivate:
                self.cancelRecording()
            elif event.type() == QEvent.ShortcutOverride:
                event.accept()
                return True
            elif event.type() == QEvent.KeyPress:
                if event.key() == Qt.Key_Escape:
                    self.cancelRecording()
                elif not event.isAutoRepeat() and event.key() not in (
                        Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta, Qt.Key_AltGr):
                    code = self._recording_code
                    sequence = QKeySequence(event.keyCombination()).toString(QKeySequence.PortableText)
                    ok, message = self.service.commit_hotkey_capture(self._recording_token, code, sequence)
                    self._set_message("Сочетание сохранено." if ok else message, not ok)
                    self.refresh()
                return True
        return super().eventFilter(obj, event)

    @Slot(result=bool)
    @report_errors
    def closeApplication(self):
        self.cancelRecording()
        closed = self.controller.close(wait=False)
        self.refresh()
        if closed:
            self._refresh_timer.stop()
        return closed
