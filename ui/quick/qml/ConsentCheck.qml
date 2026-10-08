import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// A box the user ticks to confirm one warning; the text wraps beside it and toggles it too.
CheckBox {
    id: control
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    font.pixelSize: 13
    spacing: 10
    padding: 4
    opacity: enabled ? 1 : 0.45
    indicator: Rectangle {
        x: control.leftPadding
        y: control.topPadding + 1
        width: 20; height: 20; radius: 6
        color: control.checked ? Theme.accent : control.hovered ? Theme.raised : Theme.input
        border.color: control.visualFocus ? Theme.accent : control.checked ? "transparent" : Theme.borderHover
        border.width: control.visualFocus ? 2 : 1
        Behavior on color { ColorAnimation { duration: Theme.fast } }
        Glyph { anchors.centerIn: parent; width: 14; height: 14; name: "check"; color: Theme.accentInk; visible: control.checked }
    }
    contentItem: Text {
        text: control.text
        font: control.font
        color: Theme.text
        leftPadding: control.indicator.width + control.spacing
        wrapMode: Text.WordWrap
    }
}
