import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: editor
    required property var backend
    readonly property var paletteData: backend.view.manual_palette || ({entries: [], revision: 0, maximum: 64})
    readonly property bool capturing: backend.view.capture && backend.view.capture.kind === "palette"
    readonly property bool canEdit: !!backend.view.can_edit
    property int selectedIndex: -1
    property int editRevision: -1
    spacing: 16
    function loadEntry(index) {
        if (index < 0 || index >= paletteData.entries.length) { selectedIndex = -1; return }
        selectedIndex = index
        editRevision = paletteData.revision
        let entry = paletteData.entries[index]
        colorField.text = entry.hex
        xField.text = entry.x
        yField.text = entry.y
    }
    Card {
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent
            spacing: 12
            Label { text: qsTr("Образцы цветов  ·  ") + editor.paletteData.entries.length + " / " + editor.paletteData.maximum; font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            // The samples are used by one colour method only: say so, as «Смешивание» does.
            Label {
                objectName: "paletteMethodNote"
                visible: editor.backend.view.current_method_id !== "manual_palette"
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.accentText
                text: qsTr("Эти образцы использует только способ «Готовые цвета по одному». Сейчас выбран другой способ — образцы при рисовании не нужны.")
            }
            ActionButton {
                visible: editor.backend.view.current_method_id !== "manual_palette"
                text: qsTr("Выбрать способ «Готовые цвета по одному»"); subtle: true; enabled: editor.canEdit
                onClicked: editor.backend.setChoice("color_picking_method", "manual_palette")
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted
                text: editor.capturing ? qsTr("Щёлкайте по цветам в программе рисования. Повторный щелчок по тому же месту обновит образец. Закончите кнопкой ниже или назначенной клавишей.")
                     : qsTr("Каждый образец связывает цвет с точкой на экране. Проверьте HEX после захвата: подсветка кнопок и эффекты курсора могут изменить измеренный цвет. Если известен точный HEX, укажите его вручную.")
            }
            Flow {
                Layout.fillWidth: true; spacing: 8
                ActionButton {
                    objectName: "appendPaletteButton"
                    text: editor.capturing ? qsTr("Завершить захват") : qsTr("Добавить с экрана")
                    shortcutText: (backend.view.bindings || ({})).define_manual_palette || ""
                    primary: true
                    enabled: editor.capturing || editor.canEdit
                    onClicked: editor.capturing ? backend.action("define_manual_palette") : backend.appendPalette()
                }
                ActionButton {
                    text: qsTr("Очистить палитру"); enabled: editor.canEdit && editor.paletteData.entries.length > 0
                    onClicked: { clearPalette.revision = editor.paletteData.revision; clearPalette.open() }
                }
            }
            Label { visible: editor.paletteData.entries.length === 0; text: qsTr("Палитра пока пустая. Добавьте цвета из программы рисования."); wrapMode: Text.WordWrap; Layout.fillWidth: true; color: Theme.muted }
            Flow {
                Layout.fillWidth: true; spacing: 8
                Repeater {
                    model: editor.paletteData.entries
                    delegate: Button {
                        id: sample
                        required property var modelData
                        objectName: "paletteSample_" + modelData.index
                        width: 154; height: 58
                        enabled: editor.canEdit
                        onClicked: editor.loadEntry(modelData.index)
                        background: Rectangle {
                            radius: 9; color: Theme.input
                            border.color: editor.selectedIndex === sample.modelData.index ? Theme.accent : Theme.border
                        }
                        contentItem: RowLayout {
                            spacing: 8
                            Rectangle { width: 26; height: 26; radius: 6; color: sample.modelData.valid ? sample.modelData.hex : "transparent"; border.color: Theme.muted }
                            ColumnLayout {
                                spacing: 2
                                Label { text: sample.modelData.valid ? sample.modelData.hex : qsTr("Нужен цвет"); font.weight: Font.DemiBold }
                                Label { text: sample.modelData.x + ", " + sample.modelData.y; font.pixelSize: 11; color: Theme.muted }
                            }
                        }
                    }
                }
            }
        }
    }
    Card {
        visible: editor.selectedIndex >= 0
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 12
            Label { text: qsTr("Образец ") + (editor.selectedIndex + 1); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Label {
                visible: editor.editRevision !== editor.paletteData.revision
                text: qsTr("Палитра изменилась. Черновик сохранён в полях; загрузите актуальный образец перед применением.")
                wrapMode: Text.WordWrap; Layout.fillWidth: true; color: Theme.accentText
            }
            GridLayout {
                columns: 3; Layout.fillWidth: true
                Label { text: qsTr("Цвет HEX") }
                Label { text: qsTr("Координата X") }
                Label { text: qsTr("Координата Y") }
                InputField { id: colorField; objectName: "paletteColor"; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 180; enabled: editor.canEdit; placeholderText: "#FFFFFF"; maximumLength: 7 }
                InputField { id: xField; objectName: "paletteX"; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 130; enabled: editor.canEdit; maximumLength: 11 }
                InputField { id: yField; objectName: "paletteY"; Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: 130; enabled: editor.canEdit; maximumLength: 11 }
            }
            Flow {
                Layout.fillWidth: true; spacing: 8
                ActionButton {
                    objectName: "savePaletteButton"; text: qsTr("Применить образец"); primary: true
                    enabled: editor.canEdit && editor.editRevision === editor.paletteData.revision
                    onClicked: if (backend.editPalette("update", editor.selectedIndex, editor.editRevision, xField.text, yField.text, colorField.text)) editor.loadEntry(editor.selectedIndex)
                }
                ActionButton { text: qsTr("Загрузить актуальный"); enabled: editor.canEdit; onClicked: editor.loadEntry(editor.selectedIndex) }
                ActionButton {
                    objectName: "removePaletteButton"; text: qsTr("Удалить образец")
                    enabled: editor.canEdit && editor.editRevision === editor.paletteData.revision
                    onClicked: if (backend.editPalette("remove", editor.selectedIndex, editor.editRevision, "", "", "")) editor.selectedIndex = -1
                }
            }
        }
    }
    MixingEditor { backend: editor.backend; Layout.fillWidth: true }
    SurfaceDialog {
        id: clearPalette
        objectName: "clearPaletteDialog"
        property int revision: -1
        parent: Overlay.overlay
        anchors.centerIn: parent
        title: qsTr("Очистить все образцы?")
        modal: true; standardButtons: Dialog.Yes | Dialog.No
        onAccepted: if (backend.editPalette("clear", -1, revision, "", "", "")) editor.selectedIndex = -1
        ColumnLayout {
            width: parent.width
            Label { text: qsTr("Координаты и цвета этой палитры будут удалены."); Layout.fillWidth: true; wrapMode: Text.WordWrap }
        }
    }
}
