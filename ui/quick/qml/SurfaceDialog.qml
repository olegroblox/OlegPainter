import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Dialog {
    id: dialog
    // A fixed default width: sizing from the title looped when the language changed.
    width: Math.min(460, (parent ? parent.width : 500) - 40)
    padding: 20
    topPadding: 8
    spacing: 16
    background: Rectangle { color: Theme.sidebar; radius: Theme.cardRadius; border.color: Theme.borderHover }
    header: Label {
        text: dialog.title
        visible: text !== ""
        padding: 20; bottomPadding: 10
        font.pixelSize: 20; font.weight: Font.DemiBold; font.letterSpacing: -0.3
        color: Theme.text; wrapMode: Text.WordWrap
    }
    footer: DialogButtonBox {
        objectName: "dialogFooter"
        visible: dialog.standardButtons !== Dialog.NoButton
        standardButtons: dialog.standardButtons
        padding: 16; spacing: 8
        background: Item { }
        delegate: ActionButton { objectName: "dialogButton" }
        onAccepted: dialog.accept()
        onRejected: dialog.reject()
    }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.normal; easing.type: Easing.OutCubic } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast } }
    Overlay.modal: Rectangle { color: Theme.scrim }
}
