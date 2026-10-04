import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Frame {
    id: card
    property bool elevated: false
    Layout.minimumWidth: 0
    padding: 16
    background: Rectangle {
        radius: Theme.cardRadius
        color: card.elevated ? Theme.surface : Theme.sidebar
        border.color: Theme.border
    }
}
