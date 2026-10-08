"""Application ownership shared by Widgets and the incoming QML presentation.

No main window or settings page is constructed here. Desktop tools are enabled
explicitly by an interactive entry point; headless consumers need no widgets.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from PySide6.QtCore import QObject, Signal, QEventLoop

from application.session import SessionManager
from application.state import ApplicationState, CaptureState
from ui.helpers.config_store import ConfigStore
from ui.helpers.preparation_state import build_preparation_state
from ui.helpers.theme_manager import theme_manager
from ui.i18n import i18n


class ApplicationController(QObject):
    preparationChanged = Signal(object)
    stateChanged = Signal(object)
    errorOccurred = Signal(str)
    closingChanged = Signal()
    closeFailed = Signal(str)
    closed = Signal()

    def __init__(self, service, *, config_store=None, shell=None, parent=None):
        super().__init__(parent)
        self.service = service
        self.profiles = service.profiles
        self.profiles.select(self.profiles.place_id, self.profiles.algorithm)
        self.config_store = config_store if config_store is not None else ConfigStore()
        self._shell_state = {}
        self._shell = shell
        self._restored = False
        self._closed = False
        self._closing = False
        self._close_error = ""
        self._close_phase = ""
        self._close_exception = None
        self._close_bindings = None
        self.service.shutdownFinished.connect(self._shutdown_finished)
        self.desktop = None
        self.session = SessionManager(service, self.profiles, self.config_store, shell=self, parent=self)
        self.session.saveFailed.connect(self._session_save_failed)
        i18n.languageChanged.connect(self._preferences_changed)
        theme_manager.themeChanged.connect(self._preferences_changed)
        self._preparation = None
        self.state = None
        self.state_revision = 0
        self._bindings = []
        for name in ("previewReady", "previewStateChanged", "areaChanged", "viewportStateChanged", "configLoaded", "sessionStateChanged",
                     "drawingEvent", "captureStateChanged", "desktopInteractionChanged", "manualPaletteChanged",
                     "appLayersChanged", "dynamicBrushSettingsChanged", "hotkeyCaptureChanged", "brushLearningChanged"):
            signal = getattr(service, name)
            signal.connect(self.refresh_preparation)
            self._bindings.append(signal)
        self.profiles.placeStateChanged.connect(self.refresh_preparation)
        self.refresh_preparation()

    @property
    def preparation(self):
        return self._preparation

    def enable_desktop(self):
        if self._closed:
            raise RuntimeError("Application controller is closed")
        if self.desktop is None:
            from ui.overlays.workspace import DesktopWorkspace
            self.desktop = DesktopWorkspace(self.service, parent=self)
            self.desktop.desktop_overlays.modeChanged.connect(self.service.set_desktop_interaction)
            self.desktop.restore_state(self._shell_state)
            self.desktop.stateChanged.connect(self._desktop_changed)
            self.refresh_preparation()
        return self.desktop

    def _desktop_changed(self):
        self.session.mark_dirty("ui")

    def _preferences_changed(self, *_):
        self.session.mark_dirty("ui")
        self.refresh_preparation()

    def set_language(self, language):
        if language not in ("ru", "en"):
            raise ValueError("Unsupported interface language")
        i18n.set_language(language)
        return i18n.current_language()

    def _restore_preferences(self, state):
        language = state.get("language")
        if language in ("ru", "en"):
            i18n.set_language(language)
        theme = state.get("theme")
        if theme in ("dark", "light"):
            theme_manager.set_theme(theme)

    def refresh_preparation(self, *_, notify=True):
        if self._closed:
            return
        if self._closing:
            state = replace(self.state, closing=True)
            if state != self.state:
                self.state = state
                self.state_revision += 1
                self.stateChanged.emit(state)
            return
        snapshot = self.service.get_preparation_snapshot()
        self.preparation_snapshot = snapshot
        if self.desktop is not None:
            self.desktop.hud_overlay.set_hex_ready(bool(snapshot.get("has_hex")))
        state = build_preparation_state(snapshot, self.profiles.export_place_state())
        application_state = ApplicationState(state, self.service.drawing_phase,
                                             self.service._drawing_run_id,
                                             CaptureState.from_payload(self.service._compute_capture_state()),
                                             self.service.desktop_interaction,
                                             self.service._hotkey_capture_active,
                                             brush_learning=self.service.brush_learning_active)
        if application_state != self.state:
            self.state = application_state
            self.state_revision += 1
            if self.desktop is not None:
                self.desktop.hud_overlay.set_application_state(application_state)
            self.stateChanged.emit(application_state)
        elif self.desktop is not None:
            self.desktop.hud_overlay.set_application_state(application_state)
        if state != self._preparation:
            self._preparation = state
            if notify:
                self.preparationChanged.emit(state)

    def restore(self):
        if self._restored:
            return False
        restored = self.session.restore()
        if not restored:
            # First launch: the start place gets the settings recommended for every
            # place (PRESET-BASE-001) instead of the neutral engine defaults.
            self.profiles.select(self.profiles.place_id, None)
        self._restored = True
        self.refresh_preparation()
        return restored

    def export_shell_state(self):
        state = deepcopy(self._shell_state)
        if self._shell is not None:
            state.update(self._shell.export_shell_state())
        if self.desktop is not None:
            state.update(self.desktop.export_state())
        state.update(language=i18n.current_language(), theme=theme_manager.theme())
        return state

    def restore_shell_state(self, state):
        self._shell_state = deepcopy(state) if isinstance(state, dict) else {}
        self._restore_preferences(self._shell_state)
        if self._shell is not None:
            self._shell.restore_shell_state(self._shell_state)
        if self.desktop is not None:
            self.desktop.restore_state(self._shell_state)

    def update_shell_state(self, state):
        if not isinstance(state, dict):
            raise ValueError("Shell state must be an object")
        if state != self._shell_state:
            self._shell_state = deepcopy(state)
            self._restore_preferences(state)
            self.session.mark_dirty("ui")

    def save(self):
        return self.session.flush_now(force=True)

    def close(self, *, save=True, wait=True):
        if self._closed:
            return True
        if not self._closing:
            self._close_exception = None
            self._close_error = ""
            self._closing = True
            self._close_phase = "saving" if save else "stopping"
            self.service.set_close_pending(True)
            # Stop real input before image encoding or disk writes.
            # Also cover a start worker whose RUNNING event is still queued.
            self.service.engine.request_drawing_stop()
            if self.service._is_drawing:
                self.service.stop()
            self.refresh_preparation()
            self.closingChanged.emit()
            if save:
                try:
                    if self.desktop is not None:
                        self.desktop.prepare_save()
                    self._request_close_save()
                except Exception as error:
                    self._close_save_failed(error)
            else:
                self._finish_close()
        elif self._close_error and self._close_phase == "stopping":
            self._close_error = ""
            self._close_exception = None
            self.closingChanged.emit()
            self._finish_close()
        if wait and not self._closed and self._closing and not self._close_error:
            loop = QEventLoop()
            def finished():
                if self._closed or not self._closing or self._close_error:
                    loop.quit()
            self.closed.connect(finished)
            self.closingChanged.connect(finished)
            try:
                loop.exec(QEventLoop.ExcludeUserInputEvents)
            finally:
                self.closed.disconnect(finished)
                self.closingChanged.disconnect(finished)
        if wait and self._close_exception is not None:
            raise self._close_exception
        return self._closed

    def _session_save_failed(self, message):
        if not self._closing:
            self.errorOccurred.emit(message)

    def _request_close_save(self):
        self.session.request_save(force=True).add_done_callback(self._close_saved)

    def _close_saved(self, future):
        try:
            future.result()
            # An already queued source/capture change may have arrived while
            # writing. Persist it before shutting down the state owner.
            if self.session._dirty_sections:
                self._request_close_save()
            else:
                self._finish_close()
        except Exception as error:
            self._close_save_failed(error)

    def _close_save_failed(self, error):
        # No desktop/service teardown happened: editing and retry stay usable.
        self._close_exception = error
        self._close_error = str(error)
        self._closing = False
        self._close_phase = ""
        self.service.set_close_pending(False)
        self.refresh_preparation()
        self.closingChanged.emit()
        self.errorOccurred.emit("Не удалось сохранить сессию: " + str(error))
        self.closeFailed.emit("Не удалось сохранить сессию: " + str(error))

    def _finish_close(self):
        self._close_phase = "stopping"
        self.closingChanged.emit()
        try:
            self._teardown_after_save()
        except Exception as error:
            # Persistence has succeeded. Teardown failure must not reopen a
            # partially disconnected application or be reported as a save error.
            self._shutdown_finished(str(error))

    def _teardown_after_save(self):
        self.session.close()
        while self._bindings:
            self._bindings[0].disconnect(self.refresh_preparation)
            self._bindings.pop(0)
        if self._close_bindings is None:
            self._close_bindings = [
                (self.profiles.placeStateChanged, self.refresh_preparation),
                (i18n.languageChanged, self._preferences_changed),
                (theme_manager.themeChanged, self._preferences_changed),
            ]
            if self.desktop is not None:
                self._close_bindings.append((self.desktop.stateChanged, self._desktop_changed))
        while self._close_bindings:
            signal, slot = self._close_bindings[0]
            signal.disconnect(slot)
            self._close_bindings.pop(0)
        if self.desktop is not None:
            self.desktop.close()
        if self.service.shutdown(wait=False) and not self._closed:
            self._shutdown_finished("")

    def _shutdown_finished(self, error):
        if not self._closing or self._close_phase != "stopping":
            return
        self._close_error = error
        self._close_exception = RuntimeError(error) if error else None
        self.closingChanged.emit()
        if error:
            self.errorOccurred.emit("Не удалось завершить освобождение ввода: " + error)
            self.closeFailed.emit("Не удалось завершить освобождение ввода: " + error)
            return
        self._closed = True
        self.closed.emit()
