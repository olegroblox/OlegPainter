import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Menu row: caption on the left, its hotkey as a keycap on the right.
MenuItem {
    id: item
    objectName: "appMenuItem"
    property string shortcutText: ""
    implicitHeight: 38
    leftPadding: 12
    rightPadding: 12
    font.pixelSize: 13
    implicitWidth: row.implicitWidth + leftPadding + rightPadding
    contentItem: RowLayout {
        id: row
        spacing: 24
        opacity: item.enabled ? 1 : 0.4
        Text {
            text: item.text; font: item.font; color: Theme.text
            verticalAlignment: Text.AlignVCenter
            Layout.fillWidth: true
        }
        Rectangle {
            visible: item.shortcutText !== ""
            radius: 6; color: Theme.keycap
            implicitWidth: keyLabel.implicitWidth + 12; implicitHeight: 22
            Text { id: keyLabel; anchors.centerIn: parent; text: item.shortcutText; font.pixelSize: 11; color: Theme.muted }
        }
    }
    background: Rectangle {
        radius: 8
        color: item.down ? Theme.input : item.highlighted ? Theme.raised : "transparent"
        Behavior on color { ColorAnimation { duration: Theme.fast } }
    }
}
