import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Card {
    id: setup
    required property var backend
    property bool compact: false
    readonly property var state: backend.view
    signal calibrationRequested()
    signal settingsRequested()
    padding: compact ? 12 : 16
    // One column once the colour-calibration caption no longer fits beside the quality box.
    readonly property int columns: width >= 900 ? 5 : width >= 560 ? 3
                                   : availableWidth >= 110 + 12 + calibrationButton.implicitWidth ? 2 : 1
    function findPlace() {
        const list = state.places || []
        for (let i = 0; i < list.length; ++i) if (list[i].id === state.current_place_id) return i
        return -1
    }
    GridLayout {
        anchors.fill: parent
        columns: setup.columns
        columnSpacing: 12; rowSpacing: 12
        ColumnLayout {
            Layout.fillWidth: true
            Label { text: qsTr("Где рисуем"); color: Theme.muted; font.pixelSize: 12 }
            ChoiceBox {
                objectName: "drawingPlace"
                Layout.fillWidth: true
                Layout.minimumWidth: 90
                model: setup.state.places || []
                textRole: "label"; valueRole: "id"
                currentIndex: setup.findPlace()
                enabled: setup.state.can_edit
                onActivated: setup.backend.selectProfile(currentValue, "")
            }
        }
        ColumnLayout {
            Label { text: qsTr("Цветов"); color: Theme.muted; font.pixelSize: 12 }
            RowLayout {
                spacing: 6
                NumberField {
                    objectName: "drawingColors"
                    from: 1; to: 256; editable: true
                    value: setup.state.settings.k_clusters
                    enabled: setup.state.can_edit
                    onValueModified: setup.backend.setNumber("k_clusters", value)
                    Layout.preferredWidth: 115
                    implicitHeight: 36
                }
                ActionButton {
                    objectName: "drawingAutoColors"
                    text: qsTr("Авто")
                    selected: !!setup.state.auto_colors
                    hint: setup.state.auto_colors
                          ? qsTr("Включено: режим и число цветов подбираются по каждой новой картинке, после удаления фона. Нажмите, чтобы задавать число самому")
                          : qsTr("Подбирать цветной или чёрно-белый режим и число цветов по картинке — сейчас и для каждой новой")
                    enabled: setup.state.can_edit
                    onClicked: setup.backend.setAutoColors(!setup.state.auto_colors)
                }
            }
        }
        // PRESETS-001: «Быстро / Баланс / Точно» right where the picture is prepared.
        ColumnLayout {
            Layout.fillWidth: true
            Label { text: qsTr("Качество"); color: Theme.muted; font.pixelSize: 12 }
            ChoiceBox {
                objectName: "drawingQuality"
                Layout.fillWidth: true
                Layout.minimumWidth: 110
                model: (setup.backend.qualityPresets || []).concat(setup.state.quality_preset === "custom" ? [{id: "custom", label: qsTr("Свои настройки")}] : [])
                textRole: "label"; valueRole: "id"
                currentIndex: {
                    for (let i = 0; i < model.length; ++i) if (model[i].id === setup.state.quality_preset) return i
                    return -1
                }
                enabled: setup.state.can_edit
                onActivated: if (currentValue !== "custom") setup.backend.setQualityPreset(currentValue)
            }
        }
        ColumnLayout {
            Layout.fillWidth: true
            Label { text: qsTr("Выбор цвета"); color: Theme.muted; font.pixelSize: 12 }
            ActionButton {
                id: calibrationButton
                objectName: "drawingCalibration"
                Layout.fillWidth: true
                Layout.minimumWidth: implicitWidth  // the choice boxes elide; this caption must not
                text: setup.state.required_calibrations.length ? qsTr("Настроить цвет") : qsTr("Калибровка ✓")
                enabled: setup.state.can_edit
                onClicked: setup.calibrationRequested()
            }
        }
        ActionButton {
            Layout.alignment: Qt.AlignBottom
            Layout.columnSpan: setup.columns === 2 ? 2 : 1
            Layout.fillWidth: setup.columns <= 2
            iconName: "sliders"
            text: qsTr("Параметры")
            onClicked: setup.settingsRequested()
        }
    }
}
