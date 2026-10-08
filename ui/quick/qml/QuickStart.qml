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
    // The speed probe takes the mouse: Main.qml asks first (learnConfirm).
    signal learnRequested(string command)
    spacing: 14

    Card {
        objectName: "quickStartTargets"
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 12
            Label { text: qsTr("Где будете рисовать?"); font.pixelSize: 17; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 13
                text: qsTr("Выберите игру или программу — от этого зависит, как OlegPainter будет выбирать цвета.")
            }
            // Equal tiles: sized by their own text the columns came out uneven and the
            // rows ragged. Three columns keep the six places in two even rows.
            GridLayout {
                Layout.fillWidth: true
                columns: quick.width < 400 ? 1 : quick.width < 620 ? 2 : 3
                columnSpacing: 10; rowSpacing: 10
                Repeater {
                    model: quick.info.targets || []
                    delegate: ChoiceTile {
                        required property var modelData
                        objectName: "quickTarget_" + modelData.id
                        Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.fillHeight: true
                        overline: modelData.group
                        title: modelData.title
                        detail: modelData.detail
                        selected: quick.info.target === modelData.id
                        enabled: quick.canEdit
                        onClicked: quick.backend.chooseTarget(modelData.id)
                    }
                }
            }
            // On a narrow window the button goes under the name: in one row the two
            // needed 518 px and pushed the whole card past a 360 px window.
            GridLayout {
                objectName: "saveQuickPlaceRow"
                visible: quick.info.target_chosen
                Layout.fillWidth: true; columns: quick.narrow ? 1 : 2; columnSpacing: 8; rowSpacing: 8
                InputField {
                    id: quickPlaceName; objectName: "quickPlaceName"
                    Layout.fillWidth: true; placeholderText: qsTr("Название своей игры или программы")
                    enabled: quick.canEdit
                }
                ActionButton {
                    objectName: "saveQuickPlace"
                    Layout.fillWidth: quick.narrow
                    text: qsTr("Сохранить как своё место"); hint: qsTr("Настройки и калибровки появятся отдельной плиткой в «Моих местах»")
                    enabled: quick.canEdit && quickPlaceName.text.trim() !== ""
                    onClicked: if (quick.backend.saveQuickPlace(quickPlaceName.text)) quickPlaceName.clear()
                }
            }
            ColumnLayout {
                visible: quick.info.target === "other"
                Layout.fillWidth: true; spacing: 10
                Label { text: qsTr("Как в этой программе выбирается цвет?"); font.pixelSize: 14; font.weight: Font.DemiBold; Layout.topMargin: 6; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                GridLayout {
                    Layout.fillWidth: true
                    columns: quick.narrow ? 1 : 2
                    columnSpacing: 10; rowSpacing: 10
                    Repeater {
                        model: quick.info.methods || []
                        delegate: ChoiceTile {
                            required property var modelData
                            objectName: "quickMethod_" + modelData.id
                            Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.fillHeight: true
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
                            id: stepTitle
                            // Natural width, but it may shrink and wrap on a narrow window. The cap
                            // comes from TextMetrics: a wrapped label reports its wrapped width as
                            // implicitWidth and stayed two lines on a wide window («Где рисовать на / экране»).
                            TextMetrics { id: titleMetrics; font: stepTitle.font; text: stepTitle.text }
                            Layout.fillWidth: true; Layout.maximumWidth: Math.ceil(titleMetrics.advanceWidth) + 2; wrapMode: Text.WordWrap
                            text: stepCard.modelData.title
                            font.pixelSize: stepCard.modelData.current ? 16 : 14
                            font.weight: Font.DemiBold
                            color: stepCard.modelData.current || stepCard.modelData.done ? Theme.text : Theme.muted
                        }
                        Label { visible: !!stepCard.modelData.optional; text: qsTr("по желанию"); color: Theme.muted; font.pixelSize: 11 }
                        // A few seconds of this step from the place's video guide: shown while
                        // the pointer is over the chip, kept open by a click.
                        Rectangle {
                            id: hintChip
                            objectName: "quickHint_" + stepCard.modelData.id
                            property bool pinned: false
                            visible: !!stepCard.modelData.hint
                            implicitWidth: hintText.implicitWidth + 20; implicitHeight: 24; radius: 12
                            color: hintArea.containsMouse || pinned ? Theme.accentSoft : Theme.input
                            border.color: hintArea.containsMouse || pinned ? Theme.accent : Theme.border
                            Label {
                                id: hintText; anchors.centerIn: parent
                                text: "▶  " + qsTr("Показать"); font.pixelSize: 12; color: Theme.accentText
                            }
                            MouseArea {
                                id: hintArea; anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                onEntered: hintPopup.open()
                                onExited: hintClose.restart()
                                onClicked: {
                                    hintChip.pinned = !hintChip.pinned
                                    if (hintChip.pinned) hintPopup.open(); else hintPopup.close()
                                }
                            }
                            // Leaving the chip for the popup (its «Смотреть в гайде» button) keeps it open.
                            Timer {
                                id: hintClose; interval: 300
                                onTriggered: if (!hintChip.pinned && !hintArea.containsMouse && !hintHover.hovered) hintPopup.close()
                            }
                            Popup {
                                id: hintPopup
                                objectName: "quickHintPopup_" + stepCard.modelData.id
                                // Over the chip it would take the hover away and blink: below the
                                // chip when it fits, else above it, always inside the window.
                                parent: Overlay.overlay
                                onAboutToShow: {
                                    const at = hintChip.mapToItem(parent, 0, 0)
                                    const below = at.y + hintChip.height + 6
                                    y = below + height <= parent.height - 8 ? below : Math.max(8, at.y - height - 6)
                                    x = Math.max(8, Math.min(at.x, parent.width - width - 8))
                                }
                                readonly property real shown: Math.max(280, Math.min(640, quick.width - 40))
                                width: shown + 12; height: shown * 9 / 16 + 12
                                margins: 8; padding: 6
                                closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutsideParent
                                onClosed: hintChip.pinned = false
                                background: Rectangle { radius: 12; color: Theme.surface; border.color: Theme.border }
                                contentItem: Item {
                                    HoverHandler { id: hintHover; onHoveredChanged: if (!hovered) hintClose.restart() }
                                    AnimatedImage {
                                        objectName: "quickHintClip"
                                        anchors.fill: parent
                                        source: hintPopup.visible ? stepCard.modelData.hint : ""
                                        playing: hintPopup.visible
                                        fillMode: Image.PreserveAspectFit; smooth: true; cache: false
                                    }
                                    // The whole guide from this step, with the voice.
                                    ActionButton {
                                        objectName: "quickHintGuide_" + stepCard.modelData.id
                                        visible: !!stepCard.modelData.guide_url
                                        anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 8
                                        text: qsTr("Смотреть в гайде"); iconName: "play"; primary: true
                                        onClicked: { Qt.openUrlExternally(stepCard.modelData.guide_url); hintPopup.close() }
                                    }
                                }
                            }
                        }
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
                                enabled: (quick.canEdit || modelData.action === "start_pause") && !modelData.blocked
                                onClicked: modelData.action === "learn_speed" ? quick.learnRequested(modelData.action)
                                                                              : quick.backend.quickStartAction(modelData.action)
                            }
                        }
                    }
                    // A grey button says why: hovering a disabled button shows no hint.
                    Label {
                        readonly property string reason: ((stepCard.modelData.actions || []).find(a => !!a.blocked) || {}).blocked || ""
                        objectName: "quickBlocked_" + stepCard.modelData.id
                        visible: reason !== "" && quick.canEdit
                        Layout.fillWidth: true; wrapMode: Text.WordWrap
                        text: reason; color: Theme.muted; font.pixelSize: 12
                    }
                    // An optional question inside a step: extra clicks around every colour change.
                    ColumnLayout {
                        objectName: "quickExtra_" + stepCard.modelData.id
                        visible: !!stepCard.modelData.extra
                        Layout.fillWidth: true; Layout.topMargin: 4; spacing: 6
                        Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.border }
                        Label {
                            Layout.fillWidth: true; Layout.topMargin: 4; wrapMode: Text.WordWrap
                            text: (stepCard.modelData.extra || {}).question || ""
                            font.pixelSize: 13; font.weight: Font.DemiBold
                        }
                        Label {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            text: (stepCard.modelData.extra || {}).detail || ""
                            color: Theme.muted; font.pixelSize: 12
                        }
                        Flow {
                            Layout.fillWidth: true; spacing: 8
                            Repeater {
                                model: (stepCard.modelData.extra || {}).actions || []
                                delegate: ActionButton {
                                    required property var modelData
                                    objectName: "quickExtra_" + modelData.action
                                    text: modelData.label
                                    // Real buttons: subtle ones read as plain text and were not noticed.
                                    subtle: modelData.action === "open_sequences"
                                    enabled: quick.canEdit
                                    onClicked: quick.backend.quickStartAction(modelData.action)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}
