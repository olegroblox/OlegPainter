import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// The picture next to the settings that change it (SETTINGS-PREVIEW-001): colours,
// cleanup and cell size can be tuned while watching the result. A click opens it
// in the viewer to zoom in and compare.
Card {
    id: panel
    required property var backend
    property bool showOriginal: false
    readonly property var state: backend.view
    signal viewRequested(bool original)
    objectName: "settingsPreview"
    padding: 14
    ColumnLayout {
        anchors.fill: parent; spacing: 10
        RowLayout {
            Layout.fillWidth: true; spacing: 6
            Label { text: qsTr("Предпросмотр"); font.pixelSize: 14; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
            ActionButton { objectName: "settingsPreviewOriginal"; text: qsTr("Оригинал"); implicitHeight: 30; selected: panel.showOriginal; onClicked: panel.showOriginal = true }
            ActionButton { objectName: "settingsPreviewResult"; text: qsTr("Результат"); implicitHeight: 30; selected: !panel.showOriginal; onClicked: panel.showOriginal = false }
        }
        ImageSurface {
            id: surface
            objectName: "settingsPreviewImage"
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 160
            source: panel.showOriginal ? panel.backend.sourceUrl : panel.backend.previewUrl
            pickEnabled: surface.source.toString() !== ""
            onPicked: panel.viewRequested(panel.showOriginal)
            Label {
                anchors.centerIn: parent; width: parent.width - 24; wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter; color: Theme.muted
                visible: !panel.showOriginal && (!panel.state.image_loaded || !panel.state.area_selected || panel.state.preview_busy || !panel.backend.previewUrl)
                text: !panel.state.image_loaded ? qsTr("Откройте картинку на странице «Рисование»")
                    : !panel.state.area_selected ? qsTr("Выберите область рисования — тогда появится результат")
                    : panel.state.preview_busy ? qsTr("Подготавливаем результат…") : qsTr("Предпросмотр пока недоступен")
            }
        }
        Label {
            objectName: "settingsPreviewInfo"
            Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
            text: (panel.state.palette_count ? panel.state.palette_count + " " + qsTr("цв.") + " · " : "")
                  + qsTr("клетка") + " " + (panel.state.settings ? panel.state.settings.brush_size : "?") + " px"
                  + " · " + qsTr("нажмите на картинку, чтобы рассмотреть")
        }
    }
}
