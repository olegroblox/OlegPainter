import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Card {
    id: editor
    required property var backend
    required property var values
    readonly property var groups: backend.settingGroups
    readonly property bool advanced: backend.advancedSettings
    // SETTINGS-004: without «Расширенные» only the first group («Основное») is shown.
    readonly property int shownGroup: advanced ? groupSelector.currentIndex : 0
    ColumnLayout {
        anchors.fill: parent
        spacing: 16
        // PRESETS-001: one choice for newcomers instead of a dozen switches.
        Label { text: qsTr("Качество рисунка"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true }
        GridLayout {
            objectName: "qualityPresets"
            Layout.fillWidth: true
            columns: editor.width < 520 ? 1 : 3
            columnSpacing: 10; rowSpacing: 10
            Repeater {
                model: editor.backend.qualityPresets
                delegate: ChoiceTile {
                    required property var modelData
                    objectName: "qualityPreset_" + modelData.id
                    Layout.fillWidth: true; Layout.preferredWidth: 1
                    title: modelData.label
                    detail: modelData.detail
                    selected: editor.backend.view.quality_preset === modelData.id
                    enabled: editor.backend.view.can_edit
                    onClicked: editor.backend.setQualityPreset(modelData.id)
                }
            }
        }
        Label {
            visible: editor.backend.view.quality_preset === "custom"
            text: qsTr("Сейчас выбраны свои настройки. Нажмите пресет, чтобы вернуться к проверенному набору.")
            Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
        }
        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.border }
        RowLayout {
            Layout.fillWidth: true
            Label {
                text: editor.advanced ? qsTr("Все настройки") : qsTr("Основное")
                font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true
            }
            ToggleSwitch {
                objectName: "advancedSettings"
                text: qsTr("Расширенные")
                checked: editor.advanced
                onClicked: editor.backend.setAdvancedSettings(checked)
            }
        }
        Label {
            text: editor.advanced ? qsTr("Подсказки к параметрам — при наведении или по кнопке «?».")
                                  : qsTr("Здесь то, что меняют чаще всего. Остальное — в «Расширенных»: подготовка картинки, маршрут, совместимость с программой и проверка результата.")
            Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
        }
        Label {
            objectName: "timingPauseHint"
            visible: editor.backend.view.phase === "paused" || editor.backend.view.phase === "started"
            text: editor.backend.view.phase === "paused"
                  ? qsTr("На паузе можно изменить паузы рисования: «Паузу между движениями» здесь, остальные — в «Скорость и ввод» (расширенные). После продолжения будут использованы новые значения.")
                  : qsTr("Чтобы изменить скорость, поставьте рисунок на паузу. Кисть, палитру и режим можно менять после остановки.")
            Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.accentText
        }
        Flow {
            id: groupSelector
            objectName: "settingsGroup"
            visible: editor.advanced
            property int currentIndex: 0
            readonly property int count: editor.groups.length
            Layout.fillWidth: true
            spacing: 8
            Repeater {
                model: editor.groups
                ActionButton {
                    required property var modelData
                    required property int index
                    objectName: "settingsCategory_" + modelData.id
                    text: modelData.label
                    selected: groupSelector.currentIndex === index
                    width: Math.min(implicitWidth, groupSelector.width)
                    onClicked: groupSelector.currentIndex = index
                }
            }
        }
        Repeater {
            model: editor.shownGroup >= 0 && editor.shownGroup < editor.groups.length ? editor.groups[editor.shownGroup].settings : []
            delegate: SettingControl {
                required property var modelData
                descriptor: modelData
                currentValue: editor.values[modelData.key]
                backend: editor.backend
                enabled: editor.backend.view.setting_editability[modelData.key] === true
                visible: editor.backend.view.setting_visibility[modelData.key] !== false
            }
        }
    }
}
