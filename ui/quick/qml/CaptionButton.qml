import QtQuick
import QtQuick.Controls

// Minimize / maximize / close of the custom title bar, drawn like Windows 11.
AbstractButton {
    id: control
    property string glyph: ""
    property bool closeButton: false
    property string hint: ""
    implicitWidth: 46
    hoverEnabled: true
    focusPolicy: Qt.NoFocus
    Accessible.name: hint
    ToolTip.visible: hovered && hint !== ""
    ToolTip.text: hint
    ToolTip.delay: 700
    background: Rectangle {
        color: control.closeButton && (control.hovered || control.down) ? (control.down ? "#b3202b" : "#c42b1c")
               : control.down ? Theme.input : control.hovered ? Theme.raised : "transparent"
        Behavior on color { ColorAnimation { duration: Theme.fast } }
    }
    contentItem: Text {
        text: control.glyph
        font.family: Theme.iconFont
        font.pixelSize: 10
        color: control.closeButton && control.hovered ? "white" : Theme.text
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
    }
}
