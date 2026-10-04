import QtQuick
import QtQuick.Controls

Slider {
    id: control
    property real committedValue: 0
    readonly property real draftValue: Number(value.toFixed(8))
    signal commitRequested(real nextValue)
    value: committedValue
    live: true
    snapMode: Slider.SnapAlways
    function commit() {
        if (Math.abs(draftValue - committedValue) > 0.000000001)
            commitRequested(draftValue)
        value = Qt.binding(function() { return control.committedValue })
    }
    onMoved: if (!pressed) commit()
    onPressedChanged: if (!pressed) commit()
    implicitHeight: 36
    hoverEnabled: true
    opacity: enabled ? 1 : 0.45
    background: Rectangle {
        x: control.leftPadding
        y: control.topPadding + (control.availableHeight - height) / 2
        width: control.availableWidth
        height: 5
        radius: 2.5
        color: Theme.raised
        Rectangle { width: control.position * parent.width; height: parent.height; radius: 2; color: Theme.accent }
    }
    handle: Rectangle {
        x: control.leftPadding + control.visualPosition * (control.availableWidth - width)
        y: control.topPadding + (control.availableHeight - height) / 2
        width: 18; height: 18; radius: 9
        color: control.pressed ? Theme.accent : Theme.knob
        border.color: control.activeFocus ? Theme.accent : Theme.knobBorder
        border.width: control.activeFocus ? 2 : 1
        scale: control.pressed ? 1.15 : control.hovered ? 1.08 : 1
        Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
        Behavior on color { ColorAnimation { duration: Theme.fast } }
        Rectangle {
            anchors.centerIn: parent; width: 30; height: 30; radius: 15; z: -1
            color: Theme.accentSoft; opacity: control.pressed || control.visualFocus ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        }
    }
}
