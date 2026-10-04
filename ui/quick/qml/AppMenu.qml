import QtQuick
import QtQuick.Controls

// Dropdown menu in the app theme; width fits the longest item (no clipped text).
Menu {
    id: menu
    padding: 6
    implicitWidth: {
        let widest = 220
        for (let i = 0; i < count; i++) {
            const item = itemAt(i)
            if (item && item.implicitWidth > widest)
                widest = item.implicitWidth
        }
        return widest + leftPadding + rightPadding
    }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.fast } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast } }
    background: Rectangle {
        color: Theme.surface; radius: 12
        border.color: Theme.borderHover; border.width: 1
    }
    delegate: AppMenuItem { }
}
