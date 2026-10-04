import QtQuick

Rectangle {
    id: surface
    property alias source: image.source
    property alias imageObjectName: image.objectName
    property bool pickEnabled: false
    signal picked(real x, real y)
    color: Theme.background
    radius: 12
    clip: true
    Image {
        anchors.fill: parent; anchors.margins: 8; clip: true
        source: Theme.dark ? "checker.svg" : "checker-light.svg"
        fillMode: Image.Tile
    }
    Image {
        id: image
        anchors.fill: parent; anchors.margins: 8
        fillMode: Image.PreserveAspectFit; cache: false; smooth: false
        MouseArea {
            anchors.centerIn: parent
            width: image.paintedWidth; height: image.paintedHeight
            enabled: surface.pickEnabled && image.status === Image.Ready
            cursorShape: enabled ? Qt.CrossCursor : Qt.ArrowCursor
            onClicked: function(mouse) { surface.picked(mouse.x / width, mouse.y / height) }
        }
    }
}
