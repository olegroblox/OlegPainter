import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// IMAGE-SOURCE-001: search in the user's browser, bring the picture back in one step.
SurfaceDialog {
    id: dialog
    objectName: "imageSearchDialog"
    required property var backend
    readonly property var search: backend.imageSearch
    property string service: search.service
    property string kind: search.kind
    readonly property bool narrow: width < 460
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(600, parent.width - 40)
    height: Math.min(implicitHeight, parent.height - 24)
    title: qsTr("Найти картинку")
    modal: true
    standardButtons: Dialog.Close
    onOpened: { service = search.service; kind = search.kind; queryField.forceActiveFocus() }
    function runSearch() {
        if (queryField.text.trim() !== "" && dialog.backend.searchImages(dialog.service, queryField.text, dialog.kind)) dialog.close()
    }
    contentItem: ScrollView {
        id: searchScroll
        implicitHeight: searchContent.implicitHeight
        clip: true; contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ColumnLayout {
            id: searchContent
            width: searchScroll.availableWidth
            spacing: 14
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                InputField {
                    id: queryField
                    objectName: "imageSearchQuery"
                    Layout.fillWidth: true
                    placeholderText: qsTr("Что нарисовать? Например: котик, Спанч Боб, машина")
                    onAccepted: dialog.runSearch()
                }
                ActionButton {
                    objectName: "imageSearchRun"
                    iconName: "search"; text: dialog.narrow ? "" : qsTr("Искать"); hint: qsTr("Искать"); primary: true
                    enabled: queryField.text.trim() !== ""
                    onClicked: dialog.runSearch()
                }
            }
            Label { text: qsTr("Где искать"); color: Theme.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Flow {
                Layout.fillWidth: true; spacing: 8
                Repeater {
                    model: dialog.search.services
                    ActionButton {
                        required property var modelData
                        objectName: "imageService_" + modelData.id
                        text: modelData.label
                        selected: dialog.service === modelData.id
                        onClicked: dialog.service = modelData.id
                    }
                }
            }
            Label { text: qsTr("Какие картинки"); color: Theme.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Flow {
                Layout.fillWidth: true; spacing: 8
                Repeater {
                    model: dialog.search.kinds
                    ActionButton {
                        required property var modelData
                        objectName: "imageKind_" + modelData.id
                        text: modelData.label
                        selected: dialog.kind === modelData.id
                        onClicked: dialog.kind = modelData.id
                    }
                }
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
                text: qsTr("Поиск откроется в браузере, сразу с большими картинками. Понравившуюся картинку перетащите в окно OlegPainter или нажмите на ней правой кнопкой → «Копировать картинку»: программа сама предложит её вставить. Дальше можно убрать фон в «Обработке», обрезать края в трафарете (Alt+F2) и выбрать качество.")
            }
            Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.border }
            Label { text: qsTr("Уже есть ссылка на картинку?"); font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                InputField {
                    id: linkField
                    objectName: "imageLink"
                    Layout.fillWidth: true
                    placeholderText: "https://…"
                    onAccepted: if (dialog.backend.openImageUrl(text)) { text = ""; dialog.close() }
                }
                ActionButton {
                    objectName: "imageLinkOpen"
                    text: qsTr("Загрузить")
                    enabled: linkField.text.trim() !== "" && dialog.backend.view.can_edit
                    onClicked: if (dialog.backend.openImageUrl(linkField.text)) { linkField.text = ""; dialog.close() }
                }
            }
            // The status line is under the dimmed backdrop: a refused link says why right here.
            Label {
                objectName: "imageSearchError"
                visible: dialog.opened && dialog.backend.messageError && dialog.backend.message !== ""
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.danger; font.pixelSize: 12
                text: dialog.backend.message
            }
        }
    }
}
