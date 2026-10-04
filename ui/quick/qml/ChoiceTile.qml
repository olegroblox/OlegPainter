import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A large selectable card: a short overline, a title and one line of help.
Button {
    id: tile
    property string overline: ""
    property string detail: ""
    property bool selected: false
    implicitHeight: Math.max(76, content.implicitHeight + 24)
    Layout.minimumWidth: 0
    hoverEnabled: true
    padding: 14
    Accessible.name: title
    property string title: ""
    opacity: enabled ? 1 : 0.45
    background: Rectangle {
        radius: 12
        color: tile.selected ? Theme.accentSoft : tile.hovered ? Theme.raised : Theme.input
        border.color: tile.selected ? Theme.accent : tile.visualFocus ? Theme.accent : Theme.border
        border.width: tile.selected ? 1.5 : 1
        Behavior on color { ColorAnimation { duration: Theme.fast } }
    }
    contentItem: ColumnLayout {
        id: content
        spacing: 3
        Label {
            visible: tile.overline !== ""
            text: tile.overline.toUpperCase(); color: tile.selected ? Theme.accentText : Theme.muted
            font.pixelSize: 10; font.weight: Font.DemiBold; font.letterSpacing: 0.8
        }
        Label {
            Layout.fillWidth: true; wrapMode: Text.WordWrap
            text: tile.title; color: Theme.text; font.pixelSize: 14; font.weight: Font.DemiBold
        }
        Label {
            visible: tile.detail !== ""
            Layout.fillWidth: true; wrapMode: Text.WordWrap
            text: tile.detail; color: Theme.muted; font.pixelSize: 12
        }
    }
}
