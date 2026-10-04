import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Card {
    id: editor
    required property var backend
    readonly property var values: backend.view.manual_mix
    readonly property bool canEdit: backend.view.can_edit && !backend.view.preview_busy
    ColumnLayout {
        anchors.fill: parent
        spacing: 12
        Label { text: qsTr("Смешивание цветов"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        Label {
            Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted
            text: qsTr("Дополнительные оттенки получаются несколькими проходами с разной непрозрачностью. Нужны готовые цвета, ползунок непрозрачности и однотонный холст. Рисование займёт больше времени.")
        }
        ToggleSwitch {
            objectName: "mixEnabled"
            text: qsTr("Смешивать цвета ручной палитры")
            checked: editor.values.enabled
            enabled: editor.canEdit
            onClicked: editor.backend.setManualMix({enabled: checked})
        }
        Label {
            visible: editor.backend.view.current_method_id !== "manual_palette"
            Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.accentText
            text: qsTr("Смешивание работает только со способом «Готовые цвета по одному».")
        }
        ActionButton {
            visible: editor.backend.view.current_method_id !== "manual_palette"
            text: qsTr("Выбрать способ «Готовые цвета по одному»"); enabled: editor.canEdit
            onClicked: editor.backend.setChoice("color_picking_method", "manual_palette")
        }
        ColumnLayout {
            Layout.fillWidth: true
            visible: editor.values.enabled
            spacing: 12
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                color: editor.values.calibrated ? Theme.muted : Theme.accent
                text: editor.values.calibrated ? qsTr("Ползунок настроен. Повторите калибровку, если его положение изменилось.")
                      : qsTr("Перед запуском укажите на ползунке точки 0% и 100% непрозрачности.")
            }
            ActionButton {
                objectName: "calibrateAlphaButton"
                Layout.fillWidth: true
                text: editor.values.calibrated ? qsTr("Настроить ползунок заново") : qsTr("Настроить ползунок непрозрачности")
                enabled: editor.canEdit
                onClicked: editor.backend.action("calibrate_alpha_slider")
            }
            Label { text: qsTr("Цвет чистого холста (HEX)") }
            RowLayout {
                Layout.fillWidth: true
                Rectangle { width: 28; height: 28; radius: 6; color: editor.values.canvas_hex; border.color: Theme.muted }
                InputField {
                    id: canvasColor; objectName: "mixCanvasColor"
                    Layout.fillWidth: true; maximumLength: 7
                    enabled: editor.canEdit; placeholderText: "#FFFFFF"
                    text: editor.values.canvas_hex
                    onAccepted: editor.backend.setManualMix({canvas_hex: text})
                }
                ActionButton {
                    objectName: "saveMixCanvas"
                    text: qsTr("Применить"); enabled: editor.canEdit
                    onClicked: editor.backend.setManualMix({canvas_hex: canvasColor.text})
                }
                ActionButton {
                    objectName: "pickMixCanvas"
                    iconName: "screenshot"; text: qsTr("Взять с экрана"); subtle: true
                    hint: qsTr("Щёлкните по чистому месту холста в программе рисования")
                    enabled: editor.canEdit
                    onClicked: editor.backend.pickMixCanvasColor()
                }
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted
                text: qsTr("Укажите точный цвет фона в программе рисования. Он влияет на расчёт оттенков; белый холст — #FFFFFF. Непрозрачность проходов подбирается автоматически.")
            }
        }
    }
}
