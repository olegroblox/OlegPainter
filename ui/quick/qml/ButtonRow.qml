import QtQuick
import QtQuick.Layouts

// Buttons that wrap onto more lines when the row is narrow. Beside other items in a
// layout it asks for the whole row on one line: a plain Flow reports only its widest
// wrapped line, so it stayed wrapped after the window grew, and with buttons sized
// to it the row collapsed to nothing (saved profiles, 2026-10-05).
Flow {
    id: row
    spacing: 8
    readonly property real lineWidth: {
        let total = 0, shown = 0
        for (let i = 0; i < children.length; ++i) {
            const child = children[i]
            const width = child.visible ? Math.max(child.implicitWidth, child.width) : 0
            if (width <= 0) continue
            total += width; ++shown
        }
        return total + Math.max(0, shown - 1) * spacing
    }
    Layout.preferredWidth: lineWidth
}
