import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

SurfaceDialog {
    id: dialog
    objectName: "calibrationDialog"
    required property var backend
    readonly property var state: backend.view
    readonly property var parts: state.colour_parts || ({})
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(560, parent.width - 40)
    height: Math.min(implicitHeight, parent.height - 24)
    title: qsTr("Выбор цвета в программе рисования")
    modal: true
    standardButtons: Dialog.Close
    function run(command) { close(); backend.action(command) }
    function methodDetail() {
        const methods = dialog.backend.colorMethods || []
        for (let i = 0; i < methods.length; ++i) if (methods[i].id === dialog.state.current_method_id) return methods[i].detail
        return ""
    }
    contentItem: ScrollView {
        id: dialogScroll
        implicitHeight: dialogContent.implicitHeight
        clip: true; contentWidth: availableWidth
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
    ColumnLayout {
        id: dialogContent
        width: dialogScroll.availableWidth
        enabled: dialog.state.can_edit
        spacing: 14
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; text: qsTr("Выберите, как программа задаёт цвет. Калибровка сохраняется в настройках выбранной программы."); color: Theme.muted }
        ChoiceBox {
            objectName: "calibrationMethod"
            Layout.fillWidth: true
            model: dialog.backend.colorMethods
            textRole: "label"; valueRole: "id"
            currentIndex: {
                for (let i = 0; i < model.length; ++i) if (model[i].id === dialog.state.settings.color_picking_method) return i
                return -1
            }
            enabled: dialog.state.can_edit
            onActivated: dialog.backend.setChoice("color_picking_method", currentValue)
        }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; text: dialog.methodDetail(); color: Theme.muted; font.pixelSize: 12; visible: text !== "" }
        Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; text: qsTr("Это окно спрячется — щёлкните нужное место в своей программе. Сначала поставьте её окно так, как оно будет стоять во время рисунка. ✓ — уже указано."); color: Theme.muted }
        // Gartic Phone and the like: the colour box opens only after the recorded clicks.
        Label {
            visible: dialog.state.current_method_id === "hex_field" && !!dialog.state.hex_opened_by_actions
            Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.accentText; font.pixelSize: 12
            text: qsTr("Перед этим программа сама нажмёт записанные щелчки «до цвета», чтобы открыть окно цвета.")
        }
        ActionButton { visible: dialog.state.current_method_id === "hex_field"; text: qsTr("Указать поле HEX") + (dialog.parts.hex ? " ✓" : ""); Layout.fillWidth: true; onClicked: dialog.run("capture_hex_palette") }
        RowLayout {
            visible: dialog.state.current_method_id === "hsv_palette"
            Layout.fillWidth: true
            ActionButton { text: qsTr("Указать круг") + (dialog.parts.circle ? " ✓" : ""); Layout.fillWidth: true; onClicked: dialog.run("calibrate_color_circle") }
            ActionButton { text: qsTr("Указать яркость") + (dialog.parts.slider ? " ✓" : ""); Layout.fillWidth: true; onClicked: dialog.run("calibrate_brightness_slider") }
        }
        // The hue may go round the wheel either way; the wrong way picks wrong colours.
        ColumnLayout {
            visible: dialog.state.current_method_id === "hsv_palette"
            Layout.fillWidth: true; spacing: 6
            Label { text: qsTr("Цвета на круге идут"); color: Theme.muted; font.pixelSize: 12 }
            ChoiceBox {
                objectName: "hsvDirection"
                Layout.fillWidth: true
                model: [{id: "ccw", label: qsTr("Против часовой стрелки (как в Speed Draw!)")}, {id: "cw", label: qsTr("По часовой стрелке")}]
                textRole: "label"; valueRole: "id"
                currentIndex: dialog.state.hsv_direction === "cw" ? 1 : 0
                onActivated: dialog.backend.setHsvDirection(currentValue)
            }
            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12; text: qsTr("Если после красного по кругу идёт жёлтый против часовой стрелки — первый вариант, если по часовой — второй.") }
        }
        ActionButton { objectName: "openManualPalette"; visible: dialog.state.current_method_id === "manual_palette"; text: qsTr("Открыть редактор палитры"); Layout.fillWidth: true; onClicked: { dialog.close(); dialog.backend.pageRequested(4) } }
        ActionButton { visible: dialog.state.current_method_id === "screen_palette"; text: qsTr("Выделить палитру рамкой") + (dialog.parts.screen ? " ✓" : ""); Layout.fillWidth: true; onClicked: dialog.run("calibrate_screen_palette") }
        ActionButton { objectName: "calibrateWheel"; visible: dialog.state.current_method_id === "wheel_square"; text: qsTr("Обвести колесо") + (dialog.parts.wheel ? " ✓" : ""); Layout.fillWidth: true; onClicked: dialog.run("calibrate_wheel_square") }
        ActionButton { objectName: "dialogShowCalibration"; text: qsTr("Показать калибровку"); iconName: "crop"; subtle: true; Layout.fillWidth: true; onClicked: { dialog.close(); dialog.backend.showCalibration() } }
    }
    }
}
