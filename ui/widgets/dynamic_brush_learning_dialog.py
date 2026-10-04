from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QVBoxLayout,
    QLabel,
    QHBoxLayout,
    QPushButton,
    QRadioButton,
    QButtonGroup,
    QDialogButtonBox,
    QInputDialog,
)

from ui.i18n import tr
import logging

log = logging.getLogger("olegpainter.ui.widgets.dynamic_brush_learning_dialog")


class DynamicBrushLearningDialog(QDialog):
    def __init__(self, service, parent=None):
        super().__init__(parent)
        self.service = service
        self._state = {}
        self.setModal(True)
        self._build()
        self._bind()
        self.service.brushLearningFinished.connect(self._learning_finished)
        self.service.brushLearningChanged.connect(self._learning_changed)
        self._refresh_from_service()

    def _build(self):
        self.setWindowTitle(tr("dynamic_brush_wizard_title"))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        self.lbl_intro = QLabel(tr("dynamic_brush_wizard_intro"), self)
        self.lbl_intro.setWordWrap(True)
        layout.addWidget(self.lbl_intro)

        mode_row = QHBoxLayout()
        mode_row.setContentsMargins(0, 0, 0, 0)
        mode_row.setSpacing(10)
        self.rb_text = QRadioButton(tr("settings_dynamic_brush_mode_text"), self)
        self.rb_slider = QRadioButton(tr("settings_dynamic_brush_mode_slider"), self)
        self.rb_points = QRadioButton(tr("settings_dynamic_brush_mode_points"), self)
        self.mode_group = QButtonGroup(self)
        self.mode_group.addButton(self.rb_text)
        self.mode_group.addButton(self.rb_slider)
        self.mode_group.addButton(self.rb_points)
        mode_row.addWidget(self.rb_text)
        mode_row.addWidget(self.rb_slider)
        mode_row.addWidget(self.rb_points)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        self.lbl_control = QLabel("", self)
        self.lbl_control.setWordWrap(True)
        layout.addWidget(self.lbl_control)

        self.btn_capture_control = QPushButton("", self)
        layout.addWidget(self.btn_capture_control)

        self.btn_clear_points = QPushButton(tr("dynamic_brush_wizard_clear_points_button"), self)
        self.btn_clear_points.setVisible(False)
        layout.addWidget(self.btn_clear_points)

        self.lbl_scratch = QLabel("", self)
        self.lbl_scratch.setWordWrap(True)
        layout.addWidget(self.lbl_scratch)

        self.btn_capture_scratch = QPushButton(tr("settings_dynamic_brush_scratch_button"), self)
        layout.addWidget(self.btn_capture_scratch)

        self.lbl_status = QLabel("", self)
        self.lbl_status.setWordWrap(True)
        self.lbl_status.setObjectName("DimLabel")
        layout.addWidget(self.lbl_status)

        actions = QDialogButtonBox(QDialogButtonBox.Cancel, self)
        self.btn_learn = actions.addButton(tr("dynamic_brush_wizard_learn_button"), QDialogButtonBox.AcceptRole)
        layout.addWidget(actions)

        self.btn_capture_control.clicked.connect(self._on_capture_control)
        self.btn_clear_points.clicked.connect(self._on_clear_points)
        self.btn_capture_scratch.clicked.connect(self._on_capture_scratch)
        self.btn_learn.clicked.connect(self._on_learn_clicked)
        actions.rejected.connect(self.reject)
        self.rb_text.toggled.connect(self._on_mode_toggled)
        self.rb_slider.toggled.connect(self._on_mode_toggled)
        self.rb_points.toggled.connect(self._on_mode_toggled)

    def _bind(self):
        service = getattr(self, "service", None)
        signal = getattr(service, "dynamicBrushSettingsChanged", None)
        if signal is not None:
            try:
                signal.connect(self._on_dynamic_brush_settings_changed)
            except Exception:
                log.debug('ignored exception in signal.connect(self._on_dynamic_brush_settings_changed)', exc_info=True)

    def done(self, result):
        self.service.brushLearningFinished.disconnect(self._learning_finished)
        self.service.brushLearningChanged.disconnect(self._learning_changed)
        # Disconnect the service signal on close so this short-lived dialog isn't kept
        # alive by the long-lived service and never fires on a deleted C++ object.
        service = getattr(self, "service", None)
        signal = getattr(service, "dynamicBrushSettingsChanged", None)
        if signal is not None:
            try:
                signal.disconnect(self._on_dynamic_brush_settings_changed)
            except Exception:
                log.debug("ignored exception disconnecting dynamicBrushSettingsChanged", exc_info=True)
        super().done(result)

    def _refresh_from_service(self):
        service = getattr(self, "service", None)
        getter = getattr(service, "get_dynamic_brush_settings", None) if service else None
        if callable(getter):
            try:
                self._state = dict(getter() or {})
            except Exception:
                self._state = {}
        self._update_ui()

    def _control_ready(self) -> bool:
        mode = str(self._state.get("control_mode") or "text").strip().lower()
        if mode == "slider":
            params = self._state.get("slider_params")
            return isinstance(params, (tuple, list)) and len(params) == 4
        if mode == "points":
            points = self._state.get("points")
            return isinstance(points, (list, tuple)) and len(points) >= 2
        coord = self._state.get("coord")
        return isinstance(coord, (tuple, list)) and len(coord) >= 2

    def _scratch_ready(self) -> bool:
        rect = self._state.get("scratch_zone")
        return isinstance(rect, (tuple, list)) and len(rect) == 4

    @staticmethod
    def _sample_count(calibration) -> int:
        if not isinstance(calibration, dict):
            return 0
        raw_samples = calibration.get("samples")
        if isinstance(raw_samples, (list, tuple)):
            return len(raw_samples)
        try:
            return max(0, int(calibration.get("sample_count", raw_samples or 0) or 0))
        except Exception:
            return 0

    def _update_ui(self):
        mode = str(self._state.get("control_mode") or "text").strip().lower()
        control_ready = self._control_ready()
        scratch_ready = self._scratch_ready()
        profile_state = str(self._state.get("profile_state") or "needs_learning").strip().lower()
        cached = self._state.get("cached_calibration")

        self.rb_text.blockSignals(True)
        self.rb_slider.blockSignals(True)
        self.rb_points.blockSignals(True)
        try:
            self.rb_text.setChecked(mode not in ("slider", "points"))
            self.rb_slider.setChecked(mode == "slider")
            self.rb_points.setChecked(mode == "points")
        finally:
            self.rb_text.blockSignals(False)
            self.rb_slider.blockSignals(False)
            self.rb_points.blockSignals(False)

        if mode == "slider":
            self.btn_capture_control.setText(tr("settings_dynamic_brush_capture_slider_button"))
        elif mode == "points":
            self.btn_capture_control.setText(tr("dynamic_brush_wizard_add_point_button"))
        else:
            self.btn_capture_control.setText(tr("settings_dynamic_brush_capture_text_button"))
        self.btn_clear_points.setVisible(mode == "points")

        if mode == "points":
            points = self._state.get("points") or []
            self.lbl_control.setText(
                tr("settings_dynamic_brush_points_count").format(count=len(points))
                + (" " + tr("dynamic_brush_wizard_control_ready") if control_ready else " " + tr("dynamic_brush_wizard_points_pending"))
            )
        else:
            self.lbl_control.setText(
                tr("dynamic_brush_wizard_control_ready")
                if control_ready
                else tr("dynamic_brush_wizard_control_pending")
            )
        self.lbl_scratch.setText(
            tr("dynamic_brush_wizard_scratch_ready")
            if scratch_ready
            else tr("dynamic_brush_wizard_scratch_pending")
        )

        if profile_state == "ready" and isinstance(cached, dict):
            status_text = tr("dynamic_brush_wizard_ready_status").format(samples=self._sample_count(cached))
            validation = cached.get("validation")
            if isinstance(validation, dict):
                status_text += " " + tr(
                    "dynamic_brush_wizard_validation_passed"
                    if bool(validation.get("passed"))
                    else "dynamic_brush_wizard_validation_failed"
                )
            self.lbl_status.setText(status_text)
        else:
            self.lbl_status.setText(self._state.get("profile_message") or tr("dynamic_brush_wizard_wait_status"))

        self.btn_learn.setEnabled(control_ready and scratch_ready)

    def _on_dynamic_brush_settings_changed(self, settings=None):
        try:
            self._state = dict(settings or {})
        except Exception:
            self._state = {}
        self._update_ui()

    def _on_mode_toggled(self, *_):
        service = getattr(self, "service", None)
        if service is None:
            return
        if self.rb_slider.isChecked():
            mode = "slider"
        elif self.rb_points.isChecked():
            mode = "points"
        else:
            mode = "text"
        setter = getattr(service, "set_dynamic_brush_control_mode", None)
        if callable(setter):
            try:
                setter(mode)
            except Exception:
                log.debug('ignored exception in setter(mode)', exc_info=True)

    def _on_capture_control(self):
        service = getattr(self, "service", None)
        if service is None:
            return
        if self.rb_points.isChecked():
            value, accepted = QInputDialog.getDouble(
                self,
                tr("dynamic_brush_wizard_point_value_title"),
                tr("dynamic_brush_wizard_point_value_prompt"),
                1.0,
                0.0,
                100000.0,
                4,
            )
            if not accepted:
                return
            starter = getattr(service, "start_dynamic_brush_point_capture", None)
            if callable(starter):
                try:
                    starter(float(value))
                except Exception:
                    log.debug('ignored exception in start_dynamic_brush_point_capture', exc_info=True)
            return
        if self.rb_slider.isChecked():
            starter = getattr(service, "start_dynamic_brush_slider_capture", None)
        else:
            starter = getattr(service, "start_dynamic_brush_coord_capture", None)
        if callable(starter):
            try:
                starter()
            except Exception:
                log.debug('ignored exception in starter()', exc_info=True)

    def _on_clear_points(self):
        service = getattr(self, "service", None)
        clearer = getattr(service, "clear_dynamic_brush_points", None) if service else None
        if callable(clearer):
            try:
                clearer()
            except Exception:
                log.debug('ignored exception in clear_dynamic_brush_points', exc_info=True)

    def _on_capture_scratch(self):
        service = getattr(self, "service", None)
        starter = getattr(service, "start_dynamic_brush_scratch_zone_capture", None) if service else None
        if callable(starter):
            try:
                starter()
            except Exception:
                log.debug('ignored exception in starter()', exc_info=True)

    def _on_learn_clicked(self):
        service = getattr(self, "service", None)
        learner = getattr(service, "learn_dynamic_brush_profile", None) if service else None
        if callable(learner):
            try:
                if learner():
                    self.btn_learn.setEnabled(False)
                    self.lbl_status.setText("Обучаем кисть…")
            except Exception as error:
                self.lbl_status.setText(str(error))

    def _learning_changed(self):
        state = self.service.brush_learning_snapshot()
        for widget in (self.rb_text, self.rb_slider, self.rb_points, self.btn_capture_control,
                       self.btn_capture_scratch, self.btn_clear_points, self.btn_learn):
            widget.setEnabled(not state["active"])
        self.lbl_status.setText(state["message"])

    def _learning_finished(self, success):
        if success:
            self.accept()
        else:
            self._refresh_from_service()
            self.lbl_status.setText(self.service.brush_learning_snapshot()["message"])

    def reject(self):
        self.service.cancel_brush_learning()
        service = getattr(self, "service", None)
        canceller = getattr(service, "cancel_active_captures", None) if service else None
        if callable(canceller):
            try:
                canceller()
            except Exception:
                log.debug('ignored exception in canceller()', exc_info=True)
        super().reject()
