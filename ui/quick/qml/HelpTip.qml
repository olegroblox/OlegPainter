import QtQuick
import QtQuick.Controls

ToolTip {
    id: tip
    objectName: "settingTooltip"
    delay: 350
    timeout: 15000
    width: Math.min(420, parent.Window.window ? parent.Window.window.width - 24 : 420)
    padding: 12
    contentItem: Text {
        text: tip.text
        font.pixelSize: 13
        color: Theme.text
        wrapMode: Text.WordWrap
    }
    background: Rectangle { color: Theme.raised; radius: 8; border.color: Theme.borderHover }
}
