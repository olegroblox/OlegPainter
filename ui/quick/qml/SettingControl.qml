import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: control
    required property var descriptor
    required property var currentValue
    required property var backend
    spacing: 6
    Layout.fillWidth: true

    RowLayout {
        Layout.fillWidth: true
        Label {
            text: control.descriptor.label
            font.pixelSize: 13; font.weight: Font.Medium
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
            HelpTip { visible: labelHover.hovered && control.descriptor.help !== ""; text: control.descriptor.help }
            HoverHandler { id: labelHover }
        }
        AbstractButton {
            id: explainButton
            objectName: "explain_" + control.descriptor.key
            visible: control.descriptor.help !== ""
            Accessible.name: qsTr("Пояснение: ") + control.descriptor.label
            hoverEnabled: true; focusPolicy: Qt.TabFocus
            implicitWidth: 24; implicitHeight: 24
            Layout.preferredWidth: 24
            background: null
            contentItem: Glyph {
                name: "info"; width: 16; height: 16
                color: explainButton.hovered || explanation.visible ? Theme.text : Theme.muted
                opacity: explainButton.hovered || explanation.visible ? 1 : 0.7
            }
            HelpTip { visible: parent.hovered; text: control.descriptor.help }
            onClicked: explanation.visible = !explanation.visible
        }
    }
    Label {
        id: explanation; objectName: "help_" + control.descriptor.key
        visible: false; text: control.descriptor.help
        color: Theme.muted; font.pixelSize: 12; wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
    Label {
        // The same numbers as the hint: the effect starts about 15–20, 50 is already strong.
        visible: control.descriptor.key === "color_merge_threshold" && Number(control.currentValue) > 0.5
        text: qsTr("Сильное объединение может оставить один-два цвета. Для мягкого эффекта попробуйте 15–30, для отключения — 0.")
        color: Theme.accentText; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 12
    }
    Loader {
        Layout.fillWidth: true
        sourceComponent: control.descriptor.kind === "bool" ? booleanEditor :
                         control.descriptor.kind === "choice" ? choiceEditor : numberEditor
    }
    Component {
        id: booleanEditor
        ToggleSwitch {
            objectName: "setting_" + control.descriptor.key
            checked: control.currentValue
            text: checked ? qsTr("Включено") : qsTr("Выключено")
            onClicked: {
                control.backend.setSetting(control.descriptor.key, checked)
                checked = Qt.binding(function() { return control.currentValue })
            }
        }
    }
    Component {
        id: choiceEditor
        ChoiceBox {
            objectName: "setting_" + control.descriptor.key
            model: control.descriptor.options
            textRole: "label"
            valueRole: "id"
            currentIndex: {
                for (let i = 0; i < model.length; i++)
                    if (model[i].id === control.currentValue) return i
                return -1
            }
            onActivated: control.backend.setSetting(control.descriptor.key, currentValue)
        }
    }
    Component {
        id: numberEditor
        RowLayout {
            spacing: 14
            ValueSlider {
                id: slider
                objectName: "setting_" + control.descriptor.key
                Layout.fillWidth: true
                from: control.descriptor.minimum
                to: Math.max(control.descriptor.sliderMaximum, committedValue)
                stepSize: control.descriptor.step
                committedValue: control.currentValue * control.descriptor.scale
                onCommitRequested: function(nextValue) { control.backend.setDisplayedSetting(control.descriptor.key, nextValue) }
            }
            InputField {
                id: numberField
                objectName: "value_" + control.descriptor.key
                Layout.preferredWidth: 110
                selectByMouse: true
                horizontalAlignment: Text.AlignRight
                text: String(slider.draftValue)
                onEditingFinished: {
                    const cleaned = text.trim().replace(",", ".")
                    control.backend.setDisplayedSetting(control.descriptor.key, cleaned === "" ? NaN : Number(cleaned))
                    text = Qt.binding(function() { return String(slider.draftValue) })
                }
            }
            // The same «Авто» as at «Цветов» on the drawing page: one switch, both places.
            ActionButton {
                objectName: "settingsAutoColors"
                visible: control.descriptor.key === "k_clusters"
                text: qsTr("Авто")
                selected: !!control.backend.view.auto_colors
                enabled: !!control.backend.view.can_edit
                hint: control.backend.view.auto_colors
                      ? qsTr("Включено: режим и число цветов подбираются по каждой новой картинке, после удаления фона. Нажмите, чтобы задавать число самому")
                      : qsTr("Подбирать цветной или чёрно-белый режим и число цветов по картинке — сейчас и для каждой новой")
                onClicked: control.backend.setAutoColors(!control.backend.view.auto_colors)
            }
        }
    }
}
