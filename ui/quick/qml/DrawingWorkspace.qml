import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ColumnLayout {
    id: workspace
    required property var backend
    property bool showOriginal: false
    property real availableHeight: 500
    readonly property bool narrow: width < 600
    readonly property var state: backend.view
    signal originalSelected(bool original)
    signal calibrationRequested()
    signal backgroundRequested()
    signal viewRequested(bool original)
    signal settingsRequested()
    signal searchRequested()
    spacing: 12
    RowLayout {
        id: steps
        Layout.fillWidth: true; spacing: 8
        Repeater {
            model: [ {title:qsTr("Изображение"), detail:qsTr("Открыть файл"), icon:"image", done:workspace.state.image_loaded},
                     {title:qsTr("Область"), detail:qsTr("Выделить на экране"), icon:"crop", done:workspace.state.area_selected && !workspace.state.area_stale, size: workspace.state.area_rect ? workspace.state.area_rect[2] + " × " + workspace.state.area_rect[3] + " px" : ""},
                     {title:qsTr("Цвет"), detail:qsTr("Настроить метод"), icon:"palette", done:workspace.state.required_calibrations.length === 0} ]
            delegate: Button {
                id: step
                required property var modelData
                required property int index
                Layout.fillWidth: true; Layout.preferredWidth: 1; implicitHeight: workspace.narrow ? 40 : 56
                enabled: workspace.state.can_edit
                hoverEnabled: true
                Accessible.name: modelData.title + (modelData.done ? qsTr(": готово") : ": " + modelData.detail)
                leftPadding: workspace.narrow ? 8 : 12; rightPadding: 8
                onClicked: index === 0 ? workspace.backend.action("open_file") : index === 1 ? workspace.backend.action("select_area") : workspace.calibrationRequested()
                background: Rectangle {
                    radius: 10; color: step.hovered ? Theme.surface : Theme.sidebar
                    border.color: step.visualFocus ? Theme.accent : Theme.border
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                }
                contentItem: RowLayout {
                    spacing: workspace.narrow ? 6 : 10
                    Rectangle {
                        Layout.preferredWidth: workspace.narrow ? 22 : 30; Layout.preferredHeight: width
                        radius: width / 2; color: step.modelData.done ? Theme.accentSoft : Theme.input
                        Text { anchors.centerIn: parent; visible: !step.modelData.done; text: step.index + 1; color: Theme.muted; font.pixelSize: 12 }
                        Glyph { anchors.centerIn: parent; visible: step.modelData.done; name: "check"; color: Theme.accentText; width: 16; height: 16 }
                        Behavior on color { ColorAnimation { duration: Theme.normal } }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; spacing: 2
                        Label { text: workspace.narrow && step.index === 0 ? qsTr("Файл") : step.modelData.title; font.pixelSize: 12; font.weight: Font.Medium; color: Theme.text; Layout.fillWidth: true; elide: Text.ElideRight }
                        Label { visible: !workspace.narrow; text: step.modelData.done ? (step.modelData.size || qsTr("Готово")) : step.modelData.detail; font.pixelSize: 11; color: Theme.muted; Layout.fillWidth: true; elide: Text.ElideRight }
                    }
                }
            }
        }
    }
    Card {
        id: canvasCard
        Layout.fillWidth: true
        // Wide windows fit the preview above the fixed controls; tiny windows scroll.
        Layout.preferredHeight: Math.max(workspace.narrow ? 230 : 200, workspace.availableHeight - steps.implicitHeight - setup.implicitHeight - 24)
        padding: workspace.narrow ? 10 : 16
        ColumnLayout {
            anchors.fill: parent; spacing: 10
            RowLayout {
                Layout.fillWidth: true
                Label { visible: !workspace.narrow; text: qsTr("Холст"); font.pixelSize: 14; font.weight: Font.DemiBold; color: Theme.text; Layout.fillWidth: true }
                Rectangle {
                    implicitWidth: tabs.implicitWidth + 6; implicitHeight: 34; radius: 8; color: Theme.input
                    Rectangle {
                        x: workspace.showOriginal ? 3 : 95; y: 3; width: 89; height: 28
                        radius: 6; color: Theme.raised; border.color: Theme.border
                        Behavior on x { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
                    }
                    RowLayout {
                        id: tabs; anchors.centerIn: parent; spacing: 3
                        Repeater {
                            model: [qsTr("Оригинал"), qsTr("Результат")]
                            delegate: Button {
                                id: tab
                                required property string modelData
                                required property int index
                                readonly property bool selected: workspace.showOriginal === (index === 0)
                                implicitHeight: 28; implicitWidth: 89; text: modelData
                                onClicked: workspace.originalSelected(index === 0)
                                background: Rectangle { radius: 6; color: "transparent"; border.color: tab.visualFocus ? Theme.accent : "transparent" }
                                contentItem: Text { text: tab.text; font.pixelSize: 12; color: tab.selected ? Theme.text : Theme.muted; horizontalAlignment: Text.AlignHCenter; verticalAlignment: Text.AlignVCenter }
                            }
                        }
                    }
                }
                Item { visible: workspace.narrow; Layout.fillWidth: true }
                Label {
                    objectName: "backgroundBadge"
                    visible: !workspace.narrow && workspace.state.background.method !== "none"
                    text: workspace.state.background.busy ? qsTr("Убираем фон…")
                        : workspace.state.background.method === "color" ? qsTr("Фон: по цвету") : qsTr("Фон убран")
                    color: Theme.accentText; font.pixelSize: 12
                    leftPadding: 10; rightPadding: 10; topPadding: 5; bottomPadding: 5
                    background: Rectangle { radius: 8; color: Theme.accentSoft; border.color: Theme.accentBorder }
                    MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: workspace.backgroundRequested() }
                }
                ActionButton { objectName: "backgroundButton"; iconName: "sparkles"; text: workspace.narrow ? "" : qsTr("Обработка"); hint: qsTr("Фон, объект и вид картинки"); subtle: true; enabled: workspace.state.can_edit; onClicked: workspace.backgroundRequested() }
            }
            ImageSurface {
                id: previewSurface; objectName: "previewSurface"
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 100
                imageObjectName: "previewImage"
                source: workspace.showOriginal ? workspace.backend.sourceUrl : workspace.backend.previewUrl
                // VIEWER-001: a click opens the picture to zoom in and compare
                pickEnabled: previewSurface.source.toString() !== ""
                onPicked: workspace.viewRequested(workspace.showOriginal)
                ColumnLayout {
                    anchors.centerIn: parent; width: Math.min(320, parent.width - 24); spacing: 10
                    visible: previewSurface.source.toString() === ""
                    Rectangle {
                        Layout.alignment: Qt.AlignHCenter; width: 56; height: 56; radius: 16
                        color: Theme.sidebar; border.color: Theme.border
                        Glyph { anchors.centerIn: parent; name: "image"; width: 26; height: 26; color: Theme.muted }
                    }
                    Label { Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; text: workspace.state.image_loaded ? qsTr("Выберите область для подготовки") : qsTr("Здесь будет ваш рисунок"); color: Theme.text; font.pixelSize: 16; font.weight: Font.Medium }
                    Label { visible: !workspace.state.image_loaded; Layout.fillWidth: true; horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap; text: qsTr("Откройте файл, вставьте из буфера, найдите в интернете или перетащите картинку сюда"); color: Theme.muted; font.pixelSize: 12 }
                }
            }
            Flow {
                Layout.fillWidth: true; spacing: 6
                ActionButton { objectName: "openButton"; iconName: "image"; text: qsTr("Открыть"); shortcutText: workspace.width > 850 ? (workspace.state.bindings.open_file || "") : ""; enabled: workspace.state.can_edit; onClicked: workspace.backend.action("open_file") }
                ActionButton { iconName: "paste"; text: workspace.narrow ? "" : qsTr("Из буфера"); shortcutText: workspace.width > 850 ? (workspace.state.bindings.paste_clipboard || "") : ""; hint: qsTr("Вставить картинку, файл или ссылку из буфера"); enabled: workspace.state.can_edit; onClicked: workspace.backend.action("paste_clipboard") }
                ActionButton { objectName: "searchImageButton"; iconName: "search"; text: workspace.narrow ? "" : qsTr("Найти"); hint: qsTr("Найти картинку в интернете"); enabled: workspace.state.can_edit && !workspace.state.image_downloading; onClicked: workspace.searchRequested() }
                ActionButton { objectName: "screenSnapshotButton"; iconName: "screenshot"; text: workspace.narrow ? "" : qsTr("Снимок"); hint: qsTr("Взять картинку с экрана: выделите рамкой игру, видео или сайт"); enabled: workspace.state.can_edit; onClicked: workspace.backend.captureScreenImage() }
                ActionButton { iconName: "crop"; text: qsTr("Область"); shortcutText: workspace.width > 850 ? (workspace.state.bindings.select_area || "") : ""; enabled: workspace.state.can_edit; onClicked: workspace.backend.action("select_area") }
                ActionButton {
                    objectName: "flipImageButton"
                    text: workspace.narrow ? "⇆" : qsTr("Отразить"); hint: qsTr("Отразить картинку зеркально")
                    enabled: workspace.state.can_edit && workspace.state.image_loaded
                    onClicked: flipMenu.open()
                    AppMenu {
                        id: flipMenu; y: parent.height + 4
                        AppMenuItem { objectName: "flipHorizontal"; text: qsTr("По горизонтали (слева направо)"); onTriggered: workspace.backend.flipImage("horizontal") }
                        AppMenuItem { objectName: "flipVertical"; text: qsTr("По вертикали (сверху вниз)"); onTriggered: workspace.backend.flipImage("vertical") }
                    }
                }
                ActionButton { iconName: "stencil"; text: workspace.narrow ? "" : qsTr("Трафарет"); shortcutText: workspace.width > 850 ? (workspace.state.bindings.toggle_stencil || "") : ""; hint: qsTr("Показать или скрыть трафарет"); enabled: workspace.state.can_edit; onClicked: workspace.backend.action("toggle_stencil") }
            }
        }
    }
    DrawingSetup {
        id: setup
        Layout.fillWidth: true; backend: workspace.backend; compact: workspace.narrow
        onCalibrationRequested: workspace.calibrationRequested()
        onSettingsRequested: workspace.settingsRequested()
    }
}
