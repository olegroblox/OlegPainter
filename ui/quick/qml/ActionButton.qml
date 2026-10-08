import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Button {
    id: control
    property bool primary: false
    property bool subtle: false
    property bool danger: false
    property bool selected: false
    property string iconName: ""
    property string shortcutText: ""
    property string hint: ""
    readonly property color foreground: primary ? Theme.accentInk : danger ? Theme.danger : selected ? Theme.accentText : Theme.text
    property color glyphColor: foreground
    implicitHeight: 38
    Layout.minimumWidth: 0
    // A Flow (a positioner, not a layout) keeps a child's natural width: on a narrow
    // window or with enlarged Windows text a long button ran past the edge. Never
    // wider than the row there — the caption elides and the hint shows it in full.
    Binding on width {
        when: !!control.parent && control.parent.flow !== undefined && control.parent.columns === undefined
        // A Flow not sized yet has width 0: the full button then, or the row never grows.
        value: control.parent && control.parent.width > 0 ? Math.min(control.implicitWidth, control.parent.width) : control.implicitWidth
    }
    leftPadding: text === "" ? 8 : 14
    rightPadding: text === "" ? 8 : 14
    font.pixelSize: 13
    font.weight: primary ? Font.DemiBold : Font.Medium
    hoverEnabled: true
    Accessible.name: text || hint
    ToolTip.visible: hovered && hint !== ""
    ToolTip.text: hint
    ToolTip.delay: 500
    opacity: enabled ? 1 : 0.4
    Behavior on opacity { NumberAnimation { duration: Theme.fast } }
    background: Rectangle {
        radius: 10
        scale: control.down ? 0.98 : 1
        Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
        color: control.primary ? (control.down ? Theme.accentPressed : control.hovered ? Theme.accentHover : Theme.accent) :
               control.danger ? (control.hovered ? Theme.dangerHover : Theme.dangerSurface) : control.selected ? Theme.accentSoft : control.down ? Theme.input : control.hovered ? Theme.raised : control.subtle ? "transparent" : Theme.surface
        border.color: control.visualFocus ? Theme.accent : control.selected ? Theme.accentBorder : control.subtle || control.primary ? "transparent" : control.hovered ? Theme.borderHover : Theme.border
        border.width: control.visualFocus ? 2 : 1
        Behavior on color { ColorAnimation { duration: Theme.fast } }
        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
    }
    contentItem: Item {
        readonly property int partCount: (control.iconName ? 1 : 0) + (control.text ? 1 : 0) + (control.shortcutText ? 1 : 0)
        // Measure natural content independently of the button's assigned width.
        // Layouts round child widths to logical pixels. Round each text part up
        // so fractional native font metrics do not elide a fitting caption.
        implicitWidth: (control.iconName ? 20 : 0) + (control.text ? Math.ceil(caption.implicitWidth) : 0)
                       + (control.shortcutText ? keycap.implicitWidth : 0) + Math.max(0, partCount - 1) * 7
        implicitHeight: contents.implicitHeight
        RowLayout {
            id: contents
            anchors.centerIn: parent
            width: Math.min(control.contentItem.implicitWidth, parent.width)
            spacing: 7
            Glyph { visible: control.iconName !== ""; name: control.iconName; color: control.glyphColor; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
            Text {
                id: caption; objectName: "buttonCaption"
                visible: control.text !== ""
                text: control.text; font: control.font; color: control.foreground
                verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight
                Layout.fillWidth: true; Layout.minimumWidth: 0; Layout.preferredWidth: Math.ceil(implicitWidth)
            }
            Rectangle {
                id: keycap
                visible: control.shortcutText !== ""
                implicitWidth: Math.ceil(keyLabel.implicitWidth) + 10; implicitHeight: 20
                radius: 4; color: control.primary ? "#18000000" : Theme.keycap
                Text { id: keyLabel; anchors.centerIn: parent; text: control.shortcutText; font.pixelSize: 10; color: control.primary ? "#63500b" : Theme.muted }
            }
        }
    }
}
