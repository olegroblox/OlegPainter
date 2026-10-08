import QtQuick
import QtQuick.Controls

ToolTip {
    id: tip
    objectName: "settingTooltip"
    delay: 350
    timeout: 15000
    width: Math.min(420, parent.Window.window ? parent.Window.window.width - 24 : 420)
    // Right under what is hovered: a label starts the tip at its left edge, a small
    // ⓘ button ends it at its right edge. Centred by default, the 420 px tip of a
    // wide label and of its ⓘ appeared over each other's places (2026-10-05).
    x: parent && parent.width >= width ? 0 : (parent ? parent.width - width : 0)
    y: parent ? parent.height + 4 : 0
    margins: 8
    padding: 12
    contentItem: Text {
        text: tip.text
        font.pixelSize: 13
        color: Theme.text
        wrapMode: Text.WordWrap
    }
    background: Rectangle { color: Theme.raised; radius: 8; border.color: Theme.borderHover }
}
