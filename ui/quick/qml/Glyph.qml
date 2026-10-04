import QtQuick

Item {
    id: glyph
    property string name: ""
    property color color: Theme.muted
    implicitWidth: 20
    implicitHeight: 20
    Image {
        anchors.fill: parent
        sourceSize.width: Math.ceil(width * Screen.devicePixelRatio)
        sourceSize.height: Math.ceil(height * Screen.devicePixelRatio)
        source: glyph.name ? "image://icons/" + glyph.name + "/" + glyph.color.toString().slice(1) : ""
    }
}
