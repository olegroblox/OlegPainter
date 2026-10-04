import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Switch {
    id: control
    Layout.fillWidth: true
    Layout.minimumWidth: 0
    implicitHeight: Math.max(32, contentItem.implicitHeight + 8)
    font.pixelSize: 13
    spacing: 12
    padding: 4
    opacity: enabled ? 1 : 0.45
    indicator: Rectangle {
        x: control.leftPadding
        y: (control.height - height) / 2
        width: 36; height: 20; radius: 10
        color: control.checked ? Theme.accent : control.hovered ? Theme.raised : Theme.input
        border.color: control.visualFocus ? Theme.accent : control.checked ? "transparent" : Theme.borderHover
        border.width: control.visualFocus ? 2 : 1
        Behavior on color { ColorAnimation { duration: Theme.normal } }
        Rectangle {
            x: control.checked ? 18 : 2; y: 2
            width: 16; height: 16; radius: 8
            color: control.checked ? Theme.background : Theme.knob
            border.color: control.checked ? "transparent" : Theme.knobBorder
            Behavior on x { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
            Behavior on color { ColorAnimation { duration: Theme.normal } }
        }
    }
    contentItem: Text {
        text: control.text
        font: control.font
        color: Theme.text
        leftPadding: control.indicator.width + control.spacing
        verticalAlignment: Text.AlignVCenter
        wrapMode: Text.WordWrap
    }
}
