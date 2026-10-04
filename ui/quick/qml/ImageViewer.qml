import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Close look at the picture (VIEWER-001): zoom to the cursor with the wheel, drag
// to move, double click for «fit» / 1:1, and the original against the result
// with a split you drag across. Pixels stay sharp: the cells of the result show.
Popup {
    id: viewer
    required property var backend
    property string mode: "result"              // original / result / compare
    property real zoom: 1                       // 1 = the whole picture fits
    property real split: 0.5                    // compare: share of the width showing the original
    readonly property url originalSource: backend.originalUrl || backend.sourceUrl
    readonly property url shownSource: mode === "original" ? originalSource : backend.previewUrl
    readonly property real fitScale: base.sourceSize.width > 0 && base.sourceSize.height > 0
        ? Math.min(flick.width / base.sourceSize.width, flick.height / base.sourceSize.height) : 1
    readonly property real pixelZoom: fitScale > 0 ? 1 / fitScale : 1     // zoom that shows 1:1
    objectName: "imageViewer"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: parent.width - 32
    height: parent.height - 32
    padding: 16
    modal: true
    closePolicy: Popup.CloseOnEscape
    background: Rectangle { color: Theme.surface; radius: 16; border.color: Theme.border }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.normal; easing.type: Easing.OutCubic } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast } }
    Overlay.modal: Rectangle { color: Theme.scrim }

    function openWith(what) {
        mode = what === "original" ? "original" : what === "compare" ? "compare" : "result"
        zoom = 1; split = 0.5
        open()
    }
    function setZoom(next, cx, cy) {
        // keep the picture point under (cx, cy) in place
        const z = Math.max(1, Math.min(Math.max(16, pixelZoom * 8), next))
        const px = (flick.contentX + cx) / flick.contentWidth, py = (flick.contentY + cy) / flick.contentHeight
        zoom = z
        flick.contentX = Math.max(0, Math.min(flick.contentWidth - flick.width, px * flick.contentWidth - cx))
        flick.contentY = Math.max(0, Math.min(flick.contentHeight - flick.height, py * flick.contentHeight - cy))
    }

    contentItem: ColumnLayout {
        spacing: 12
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            Label { text: qsTr("Просмотр"); font.pixelSize: 18; font.weight: Font.DemiBold }
            Item { Layout.fillWidth: true }
            Repeater {
                model: [{id: "original", label: qsTr("Оригинал")}, {id: "result", label: qsTr("Результат")}, {id: "compare", label: qsTr("Сравнить")}]
                delegate: ActionButton {
                    required property var modelData
                    objectName: "viewerMode_" + modelData.id
                    text: modelData.label; selected: viewer.mode === modelData.id
                    enabled: modelData.id === "original" ? !!viewer.originalSource.toString() : !!viewer.backend.previewUrl
                    onClicked: viewer.mode = modelData.id
                }
            }
            Rectangle { width: 1; height: 26; color: Theme.border }
            ActionButton { iconName: "minus"; hint: qsTr("Отдалить"); onClicked: viewer.setZoom(viewer.zoom / 1.5, flick.width / 2, flick.height / 2) }
            Label {
                objectName: "viewerZoom"
                Layout.preferredWidth: 52; horizontalAlignment: Text.AlignHCenter; color: Theme.muted; font.pixelSize: 12
                text: Math.round(viewer.zoom * viewer.fitScale * 100) + "%"
            }
            ActionButton { iconName: "plus"; hint: qsTr("Приблизить"); onClicked: viewer.setZoom(viewer.zoom * 1.5, flick.width / 2, flick.height / 2) }
            ActionButton { objectName: "viewerFit"; text: qsTr("Вписать"); subtle: true; selected: viewer.zoom === 1; onClicked: viewer.zoom = 1 }
            ActionButton { text: "1:1"; subtle: true; hint: qsTr("Пиксель в пиксель"); onClicked: viewer.setZoom(viewer.pixelZoom, flick.width / 2, flick.height / 2) }
            ActionButton { objectName: "closeViewer"; text: qsTr("Закрыть"); onClicked: viewer.close() }
        }
        Rectangle {
            Layout.fillWidth: true; Layout.fillHeight: true
            color: Theme.background; radius: 12; clip: true
            Flickable {
                id: flick
                anchors.fill: parent; anchors.margins: 8
                contentWidth: Math.max(width, page.width); contentHeight: Math.max(height, page.height)
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar { policy: viewer.zoom > 1 ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }
                ScrollBar.horizontal: ScrollBar { policy: viewer.zoom > 1 ? ScrollBar.AsNeeded : ScrollBar.AlwaysOff }
                Item {
                    id: page
                    width: base.sourceSize.width * viewer.fitScale * viewer.zoom
                    height: base.sourceSize.height * viewer.fitScale * viewer.zoom
                    x: Math.max(0, (flick.width - width) / 2); y: Math.max(0, (flick.height - height) / 2)
                    Image {
                        anchors.fill: parent
                        source: Theme.dark ? "checker.svg" : "checker-light.svg"
                        fillMode: Image.Tile
                    }
                    Image {
                        id: base
                        objectName: "viewerImage"
                        anchors.fill: parent
                        source: viewer.mode === "compare" ? viewer.backend.previewUrl : viewer.shownSource
                        cache: false; smooth: viewer.zoom * viewer.fitScale < 1.5; mipmap: false
                    }
                    // compare: the original covers the left part, up to the split
                    Item {
                        visible: viewer.mode === "compare"
                        width: parent.width * viewer.split; height: parent.height
                        clip: true
                        Image {
                            width: page.width; height: page.height
                            source: viewer.originalSource
                            fillMode: Image.Stretch; cache: false; smooth: viewer.zoom * viewer.fitScale < 1.5
                        }
                    }
                    Rectangle {
                        objectName: "viewerSplit"
                        visible: viewer.mode === "compare"
                        x: parent.width * viewer.split - 1; width: 2; height: parent.height
                        color: Theme.accent
                        Rectangle {
                            anchors.centerIn: parent; width: 26; height: 26; radius: 13
                            color: Theme.accent; border.color: Theme.accentInk
                            Glyph { anchors.centerIn: parent; name: "sliders"; width: 14; height: 14; color: Theme.accentInk }
                        }
                        MouseArea {
                            anchors.centerIn: parent; width: 30; height: parent.height
                            cursorShape: Qt.SplitHCursor
                            preventStealing: true
                            onPositionChanged: function(mouse) {
                                if (!pressed) return
                                const p = mapToItem(page, mouse.x, mouse.y)
                                viewer.split = Math.max(0, Math.min(1, p.x / page.width))
                            }
                        }
                    }
                }
                WheelHandler {
                    acceptedModifiers: Qt.NoModifier
                    onWheel: function(event) {
                        const factor = event.angleDelta.y > 0 ? 1.25 : 1 / 1.25
                        const at = flick.mapFromItem(null, point.scenePosition.x, point.scenePosition.y)
                        viewer.setZoom(viewer.zoom * factor, at.x, at.y)
                    }
                }
                TapHandler {
                    onDoubleTapped: function(eventPoint) {
                        const at = flick.mapFromItem(null, eventPoint.scenePosition.x, eventPoint.scenePosition.y)
                        if (viewer.zoom !== 1) viewer.zoom = 1
                        else viewer.setZoom(Math.max(2, viewer.pixelZoom), at.x, at.y)
                    }
                }
            }
            Label {
                anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; anchors.bottomMargin: 12
                padding: 6; color: Theme.muted; font.pixelSize: 12
                background: Rectangle { color: Theme.surface; radius: 8; opacity: 0.85 }
                text: viewer.mode === "compare" ? qsTr("Слева оригинал, справа результат — двигайте разделитель. Колесо — масштаб, двойной щелчок — вписать или крупно.")
                                                : qsTr("Колесо — масштаб к курсору, перетаскивание — сдвиг, двойной щелчок — вписать или крупно.")
            }
        }
    }
}
