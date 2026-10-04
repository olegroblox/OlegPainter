import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

TextField {
    id: control
    implicitHeight: 36
    font.pixelSize: 13
    Layout.minimumWidth: 0
    leftPadding: 12
    rightPadding: 12
    color: Theme.text
    placeholderTextColor: Theme.muted
    selectionColor: Theme.accent
    selectedTextColor: "#211c0b"
    selectByMouse: true
    opacity: enabled ? 1 : 0.45
    background: Rectangle {
        radius: 9
        color: Theme.input
        border.color: control.activeFocus ? Theme.accent : control.hovered ? Theme.borderHover : Theme.border
        border.width: control.activeFocus ? 2 : 1
        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
    }
}
