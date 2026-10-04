import QtQuick
import QtQuick.Controls

SpinBox {
    id: control
    implicitHeight: 36
    font.pixelSize: 13
    leftPadding: 32
    rightPadding: 32
    opacity: enabled ? 1 : 0.45
    palette.text: Theme.text
    background: Rectangle {
        radius: 9; color: Theme.input
        border.color: control.activeFocus ? Theme.accent : Theme.border
        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
    }
    up.indicator: Rectangle {
        x: control.width - width; height: control.height; width: 30; radius: 9
        color: control.up.pressed ? Theme.raised : control.up.hovered ? Theme.surface : "transparent"
        Behavior on color { ColorAnimation { duration: Theme.fast } }
        Glyph { anchors.centerIn: parent; width: 16; height: 16; name: "plus"; color: Theme.text }
    }
    // A typed number applies by itself after a short pause or when the field
    // loses focus: newcomers never press Enter (owner, 2026-09-30).
    function commitTyped() {
        if (!editable)
            return
        var v = valueFromText(contentItem.text, locale)
        if (isNaN(v))
            return
        v = Math.max(from, Math.min(to, v))
        if (v !== value) {
            value = v
            valueModified()
        }
    }
    Timer { id: typedCommit; interval: 700; onTriggered: control.commitTyped() }
    Connections {
        target: control.contentItem
        ignoreUnknownSignals: true
        function onTextEdited() { typedCommit.restart() }
    }
    onActiveFocusChanged: if (!activeFocus) { typedCommit.stop(); commitTyped() }
    down.indicator: Rectangle {
        height: control.height; width: 30; radius: 9
        color: control.down.pressed ? Theme.raised : control.down.hovered ? Theme.surface : "transparent"
        Behavior on color { ColorAnimation { duration: Theme.fast } }
        Glyph { anchors.centerIn: parent; width: 16; height: 16; name: "minus"; color: Theme.text }
    }
}
