import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: editor
    required property var backend
    readonly property var groups: backend.view.input_sequences || []
    property int selected: 0
    readonly property var group: groups[selected] || ({entries: []})
    readonly property bool canEdit: !!backend.view.can_edit
    readonly property bool capturing: !!group.capturing
    readonly property string hotkeyCode: selected === 0 ? "define_app_layers" : selected === 1 ? "record_pre_color_actions" : "record_post_color_actions"
    readonly property string binding: (backend.view.bindings || ({}))[hotkeyCode] || ""
    readonly property string stopBinding: (backend.view.bindings || ({})).stop || ""
    spacing: 16
    onGroupsChanged: {
        for (let i = 0; i < groups.length; ++i)
            if (groups[i].capturing) { selected = i; break }
    }
    function command(operation) { backend.sequenceCommand(group.id, operation, group.revision) }
    GridLayout {
        columns: editor.width < 560 ? 1 : 3
        Layout.fillWidth: true
        Repeater {
            model: editor.groups
            delegate: ActionButton {
                required property var modelData
                required property int index
                text: modelData.title
                primary: editor.selected === index
                Layout.fillWidth: true
                enabled: editor.canEdit || modelData.capturing
                onClicked: editor.selected = index
            }
        }
    }
    Card {
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent
            spacing: 14
            Label { text: editor.group.title || ""; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted
                text: editor.selected === 0
                    ? qsTr("Запишите кнопки слоёв в целевой программе в нужном порядке. Рисунок будет распределяться между этими слоями.")
                    : qsTr("Запишите нажатия мыши в целевой программе. Они будут повторяться ") + (editor.selected === 1 ? qsTr("до") : qsTr("после")) + qsTr(" каждого выбора цвета.")
            }
            ToggleSwitch {
                objectName: "sequenceEnabled"
                text: editor.selected === 0 ? qsTr("Рисовать по слоям") : qsTr("Воспроизводить при выборе цвета")
                checked: !!editor.group.enabled
                enabled: editor.canEdit && (editor.group.saved_count > 0 || checked)
                onClicked: {
                    editor.backend.setSequenceEnabled(editor.group.id, checked, editor.group.revision)
                    checked = Qt.binding(function() { return !!editor.group.enabled })
                }
            }
            Flow {
                Layout.fillWidth: true
                spacing: 8
                ActionButton {
                    objectName: "sequenceRecord"
                    text: editor.capturing ? qsTr("Сохранить запись") : editor.group.saved_count > 0 ? qsTr("Записать заново") : qsTr("Записать")
                    shortcutText: editor.binding
                    primary: true
                    enabled: editor.canEdit || editor.capturing
                    onClicked: editor.command(editor.capturing ? "finish" : "start")
                }
                ActionButton {
                    objectName: "sequenceCancel"
                    text: qsTr("Отменить запись")
                    visible: editor.capturing
                    onClicked: editor.command("cancel")
                }
                ActionButton {
                    objectName: "sequenceClear"
                    text: qsTr("Удалить запись")
                    enabled: editor.canEdit && editor.group.saved_count > 0
                    onClicked: {
                        removeDialog.sequenceId = editor.group.id
                        removeDialog.revision = editor.group.revision
                        removeDialog.open()
                    }
                }
            }
            Label {
                objectName: "sequenceStatus"
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                color: editor.capturing ? Theme.accent : Theme.muted
                text: editor.capturing
                    ? qsTr("Новая запись: ") + editor.group.entries.length + qsTr(" из ") + editor.group.maximum + qsTr(". Прежняя запись сохранится при отмене. Завершите запись кнопкой или назначенной клавишей. Отмена — кнопкой") + (editor.stopBinding ? qsTr(" или ") + editor.stopBinding : "") + "."
                    : qsTr("Сохранено: ") + (editor.group.saved_count || 0) + qsTr(". Новая запись заменит её только после завершения.")
            }
            Label {
                visible: editor.group.entries.length === 0
                text: editor.capturing ? qsTr("Ожидаю нажатия в целевой программе…") : qsTr("Записи пока нет.")
                color: Theme.muted
            }
            ListView {
                objectName: "sequenceEntries"
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(280, contentHeight)
                model: editor.group.entries
                clip: true
                reuseItems: true
                ScrollBar.vertical: ScrollBar {}
                delegate: Rectangle {
                    required property var modelData
                    width: ListView.view.width; height: 42
                    color: modelData.index % 2 ? Theme.input : "transparent"
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 12
                        Label { text: modelData.index + ". " + modelData.label; Layout.fillWidth: true }
                        Label { text: modelData.x + ", " + modelData.y; color: Theme.muted }
                        Label { visible: editor.selected !== 0; text: Number(modelData.delay).toFixed(2) + qsTr(" с"); color: Theme.muted }
                    }
                }
            }
        }
    }
    SurfaceDialog {
        id: removeDialog
        objectName: "sequenceRemoveDialog"
        property string sequenceId: ""
        property string revision: ""
        parent: Overlay.overlay
        anchors.centerIn: parent
        modal: true
        title: qsTr("Удалить сохранённую запись?")
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: editor.backend.sequenceCommand(sequenceId, "clear", revision)
        Label { text: qsTr("Её использование будет выключено.") }
    }
}
