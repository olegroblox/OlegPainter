import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Quick start: where we draw, then five steps. All completion flags come from
// the presenter (application/onboarding.py); this file only lays them out.
ColumnLayout {
    id: quick
    required property var backend
    property bool narrow: false
    readonly property var info: backend.view.quick_start || ({})
    // The steps depend on the target: a newcomer first picks where they draw.
    readonly property bool targetChosen: info.target_chosen !== false
    readonly property var steps: targetChosen ? (info.steps || []) : []
    readonly property bool canEdit: !!backend.view.can_edit
    spacing: 14

    Card {
        objectName: "quickStartTargets"
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 12
            Label { text: qsTr("Где будете рисовать?"); font.pixelSize: 17; font.weight: Font.DemiBold }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 13
                text: qsTr("Выберите игру или программу — от этого зависит, как OlegPainter будет выбирать цвета.")
            }
            GridLayout {
                Layout.fillWidth: true
                columns: quick.narrow ? 1 : 4
                columnSpacing: 10; rowSpacing: 10
                Repeater {
                    model: quick.info.targets || []
                    delegate: ChoiceTile {
                        required property var modelData
                        objectName: "quickTarget_" + modelData.id
                        Layout.fillWidth: true
                        overline: modelData.group
                        title: modelData.title
                        detail: modelData.detail
                        selected: quick.info.target === modelData.id
                        enabled: quick.canEdit
                        onClicked: quick.backend.chooseTarget(modelData.id)
                    }
                }
            }
            RowLayout {
                objectName: "saveQuickPlaceRow"
                visible: quick.info.target_chosen
                Layout.fillWidth: true; spacing: 8
                InputField {
                    id: quickPlaceName; objectName: "quickPlaceName"
                    Layout.fillWidth: true; placeholderText: qsTr("Название своей игры или программы")
                    enabled: quick.canEdit
                }
                ActionButton {
                    objectName: "saveQuickPlace"
                    text: qsTr("Сохранить как своё место"); hint: qsTr("Настройки и калибровки появятся отдельной плиткой в «Моих местах»")
                    enabled: quick.canEdit && quickPlaceName.text.trim() !== ""
                    onClicked: if (quick.backend.saveQuickPlace(quickPlaceName.text)) quickPlaceName.clear()
                }
            }
            ColumnLayout {
                visible: quick.info.target === "other"
                Layout.fillWidth: true; spacing: 10
                Label { text: qsTr("Как в этой программе выбирается цвет?"); font.pixelSize: 14; font.weight: Font.DemiBold; Layout.topMargin: 6 }
                GridLayout {
                    Layout.fillWidth: true
                    columns: quick.narrow ? 1 : 2
                    columnSpacing: 10; rowSpacing: 10
                    Repeater {
                        model: quick.info.methods || []
                        delegate: ChoiceTile {
                            required property var modelData
                            objectName: "quickMethod_" + modelData.id
                            Layout.fillWidth: true
                            title: modelData.title
                            detail: modelData.detail
                            selected: quick.info.method_choice === modelData.id
                            enabled: quick.canEdit
                            onClicked: quick.backend.setChoice("color_picking_method", modelData.id)
                        }
                    }
                }
            }
        }
    }

    Label {
        objectName: "quickStartChooseHint"
        visible: !quick.targetChosen
        Layout.fillWidth: true; Layout.leftMargin: 4; wrapMode: Text.WordWrap
        text: qsTr("Выберите вариант выше — от него зависят следующие шаги.")
        color: Theme.muted; font.pixelSize: 13
    }

    Repeater {
        objectName: "quickStartSteps"
        model: quick.steps
        delegate: Card {
            id: stepCard
            required property var modelData
            required property int index
            objectName: "quickStep_" + modelData.id
            Layout.fillWidth: true
            elevated: modelData.current
            padding: modelData.current ? 18 : 14
            background: Rectangle {
                radius: Theme.cardRadius
                color: stepCard.modelData.current ? Theme.surface : Theme.sidebar
                border.color: stepCard.modelData.current ? Theme.accent : Theme.border
                border.width: stepCard.modelData.current ? 1.5 : 1
                Behavior on border.color { ColorAnimation { duration: Theme.normal } }
            }
            RowLayout {
                anchors.fill: parent; spacing: 14
                Rectangle {
                    Layout.alignment: Qt.AlignTop
                    width: 30; height: 30; radius: 15
                    color: stepCard.modelData.done ? Theme.accentSoft : stepCard.modelData.current ? Theme.accent : Theme.input
                    border.color: stepCard.modelData.done ? Theme.accent : "transparent"
                    Label {
                        anchors.centerIn: parent
                        text: stepCard.modelData.done ? "✓" : String(stepCard.index + 1)
                        color: stepCard.modelData.done ? Theme.accentText : stepCard.modelData.current ? Theme.accentInk : Theme.muted
                        font.pixelSize: 13; font.weight: Font.DemiBold
                    }
                }
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 6
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        Label {
                            text: stepCard.modelData.title
                            font.pixelSize: stepCard.modelData.current ? 16 : 14
                            font.weight: Font.DemiBold
                            color: stepCard.modelData.current || stepCard.modelData.done ? Theme.text : Theme.muted
                        }
                        Label { visible: !!stepCard.modelData.optional; text: qsTr("по желанию"); color: Theme.muted; font.pixelSize: 11 }
                        Item { Layout.fillWidth: true }
                        Label { visible: stepCard.modelData.done; text: qsTr("Готово"); color: Theme.accentText; font.pixelSize: 12 }
                    }
                    Label {
                        visible: stepCard.modelData.current || !stepCard.modelData.done
                        Layout.fillWidth: true; wrapMode: Text.WordWrap
                        text: stepCard.modelData.detail; color: Theme.muted; font.pixelSize: 13
                    }
                    Flow {
                        Layout.fillWidth: true; spacing: 8
                        visible: (stepCard.modelData.actions || []).length > 0
                        Repeater {
                            model: stepCard.modelData.actions || []
                            delegate: ActionButton {
                                required property var modelData
                                required property int index
                                objectName: "quickAction_" + modelData.action
                                text: modelData.label
                                primary: stepCard.modelData.current && index === 0
                                iconName: modelData.action === "start_pause" ? "play" : ""
                                enabled: quick.canEdit || modelData.action === "start_pause"
                                onClicked: quick.backend.quickStartAction(modelData.action)
                            }
                        }
                    }
                }
            }
        }
    }
}
