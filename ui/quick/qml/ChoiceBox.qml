import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ComboBox {
    id: control
    implicitHeight: 36
    font.pixelSize: 13
    Layout.minimumWidth: 0
    leftPadding: 14
    rightPadding: 36
    opacity: enabled ? 1 : 0.45
    hoverEnabled: true
    background: Rectangle {
        radius: 9
        color: control.hovered ? Theme.raised : Theme.input
        border.color: control.activeFocus ? Theme.accent : control.hovered ? Theme.borderHover : Theme.border
        border.width: control.activeFocus ? 2 : 1
        Behavior on color { ColorAnimation { duration: Theme.fast } }
        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
    }
    indicator: Glyph {
        x: control.width - width - 14
        y: (control.height - height) / 2
        width: 16; height: 16
        name: "chevron-down"; color: Theme.muted
        rotation: control.popup.visible ? 180 : 0
        Behavior on rotation { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
    }
    // Options read whole: a list only as wide as its field cut «Прямые отрезки (меньше
    // всего штрихов)» in a narrow column (audit 2026-10-05).
    TextMetrics { id: optionMetrics; font: control.font }
    function widestOption() {
        let widest = 0
        for (let i = 0; i < control.count; ++i) {
            optionMetrics.text = control.textAt(i)
            widest = Math.max(widest, optionMetrics.advanceWidth)
        }
        return Math.ceil(widest)
    }
    contentItem: Text {
        text: control.displayText
        font: control.font
        color: Theme.text
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }
    delegate: ItemDelegate {
        id: option
        required property int index
        width: control.popup.availableWidth
        implicitHeight: 38
        highlighted: control.highlightedIndex === index
        text: control.textAt(index)
        contentItem: RowLayout {
            spacing: 10
            Text { text: option.text; font: control.font; color: Theme.text; elide: Text.ElideRight; Layout.fillWidth: true }
            Glyph { name: "check"; color: Theme.accentText; opacity: option.index === control.currentIndex ? 1 : 0; Layout.preferredWidth: 16; Layout.preferredHeight: 16 }
        }
        background: Rectangle { radius: 6; color: option.highlighted ? Theme.raised : "transparent" }
    }
    popup: Popup {
        objectName: "choicePopup"
        y: control.height + 5
        width: control.width
        onAboutToShow: {
            const window = control.Window.window
            width = Math.min(Math.max(control.width, control.widestOption() + 64), window ? window.width - 16 : control.width)
        }
        padding: 5
        implicitHeight: Math.min(contentItem.implicitHeight + 10, 280)
        margins: 8
        enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.fast } }
        exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast } }
        background: Rectangle { color: Theme.input; radius: 10; border.color: Theme.borderHover }
        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            highlightMoveDuration: 0
            ScrollIndicator.vertical: ScrollIndicator { }
        }
    }
}
