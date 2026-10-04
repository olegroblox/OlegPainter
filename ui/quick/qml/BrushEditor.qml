import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Commands and measurements belong to the Python service. Manual range
// controls remain available for programs that use fractional brush sizes.
ColumnLayout {
    id: editor
    required property var backend
    readonly property var brush: backend.view.brush || ({})
    readonly property var learning: backend.view.brush_learning || ({})
    readonly property bool canEdit: !!backend.view.can_edit
    readonly property string mode: brush.control_mode || "text"
    readonly property bool controlReady: mode === "slider" ? !!brush.slider_params
                                        : mode === "points" ? (brush.points || []).length >= 2
                                        : !!brush.coord
    readonly property var modeHints: ({
        slider: qsTr("Обведите дорожку ползунка размера от одного конца до другого. Направление увеличения программа определит по пробным мазкам."),
        text: brush.text_auto === false
              ? qsTr("Кликните по полю размера кисти. Программа проверит размеры от %1 до %2 с шагом %3.")
                    .arg(Number(brush.min_value).toLocaleString(Qt.locale(), "f", 1))
                    .arg(Number(brush.max_value).toLocaleString(Qt.locale(), "f", 1))
                    .arg(Number(brush.step_value).toLocaleString(Qt.locale(), "f", 1))
              : qsTr("Кликните по полю размера кисти. Программа проверит целые размеры, начиная с 1. Если в вашей программе нужны дробные числа — задайте диапазон в «Дополнительно»."),
        points: qsTr("Укажите на экране кнопки готовых размеров (минимум две) и подпишите размер каждой.")
    })
    spacing: 16

    Card {
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent
            spacing: 12
            Label { text: qsTr("Автоматический размер кисти"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted
                text: qsTr("Большие области программа закрашивает крупной кистью, края и мелочь — самой маленькой. Для этого она один раз пробует кисть в вашей программе: три шага ниже.")
            }
            ToggleSwitch { objectName: "brushEnabled"; text: qsTr("Использовать обученную кисть"); checked: !!editor.brush.enabled; enabled: editor.canEdit; onClicked: backend.setBrush({enabled: checked}) }
        }
    }

    Card {
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent
            spacing: 12
            Label { text: qsTr("1. Чем в программе меняется размер кисти?"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            ChoiceBox {
                objectName: "brushMode"; Layout.fillWidth: true; enabled: editor.canEdit
                model: [{id:"slider",label:qsTr("Ползунок")},{id:"text",label:qsTr("Поле с числом")},{id:"points",label:qsTr("Кнопки готовых размеров")}]
                textRole: "label"; valueRole: "id"
                currentIndex: editor.mode === "slider" ? 0 : editor.mode === "points" ? 2 : 1
                onActivated: backend.setBrush({control_mode: currentValue})
            }
            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; text: editor.modeHints[editor.mode] || "" }

            GridLayout {
                columns: editor.width < 520 ? 1 : 3
                Layout.fillWidth: true
                InputField { id: pointSize; objectName: "brushPointSize"; visible: editor.mode === "points"; Layout.preferredWidth: 120; placeholderText: qsTr("Размер"); text: "1"; enabled: editor.canEdit }
                ActionButton {
                    objectName: "brushCaptureControl"; enabled: editor.canEdit; primary: !editor.controlReady
                    text: editor.mode === "points" ? qsTr("Добавить кнопку") : editor.mode === "slider" ? qsTr("Указать ползунок") : qsTr("Указать поле")
                    onClicked: backend.captureBrush(editor.mode === "points" ? "point" : editor.mode, Number(pointSize.text.replace(",", ".")))
                }
                Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: editor.controlReady ? Theme.accentText : Theme.muted; text: editor.controlReady ? qsTr("Готово") : editor.mode === "points" ? qsTr("Нужно минимум два размера") : qsTr("Ещё не указано") }
            }
            Repeater {
                model: editor.mode === "points" ? editor.brush.points || [] : []
                Label { required property var modelData; text: qsTr("Размер ") + modelData.value + "  ·  X " + modelData.x + "  Y " + modelData.y; color: Theme.muted }
            }
            ActionButton { visible: editor.mode === "points" && (editor.brush.points || []).length > 0; text: qsTr("Очистить кнопки размеров"); enabled: editor.canEdit; onClicked: backend.brushCommand("clear_points") }

        }
    }

    Card {
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 12
            Label { text: qsTr("2. Место для пробных мазков"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted
                text: qsTr("Выделите свободный кусок холста рядом с рисунком. Здесь останутся пробные мазки: перед повторным обучением очистите это место или выберите другое.")
            }
            Flow {
                Layout.fillWidth: true; spacing: 8
                ActionButton { objectName: "brushCaptureScratch"; text: qsTr("Выделить место"); hint: qsTr("Выделить свободное место на холсте для пробных мазков"); primary: !editor.brush.scratch_zone; enabled: editor.canEdit; onClicked: backend.captureBrush("scratch", 0) }
            }
            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: editor.brush.scratch_zone ? Theme.accentText : Theme.muted; text: editor.brush.scratch_zone ? qsTr("Место выбрано: ") + editor.brush.scratch_zone[2] + " × " + editor.brush.scratch_zone[3] + " px" : qsTr("Место ещё не выбрано") }
        }
    }

    Card {
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 12
            Label { text: qsTr("3. Обучение"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted
                text: qsTr("Оставьте программу открытой и не трогайте мышь до конца обучения. Шаг рисунка подстроится под измеренную кисть. Остановить можно кнопкой «Остановить» или клавишей остановки.")
            }
            RowLayout {
                BusyIndicator { running: !!editor.learning.active; visible: running; palette.dark: Theme.accent; Layout.preferredWidth: 32; Layout.preferredHeight: 32 }
                Label { objectName: "brushLearningStatus"; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: editor.learning.error ? Theme.danger : Theme.accentText; text: editor.learning.message || editor.brush.profile_message || (editor.brush.profile_state === "ready" ? qsTr("Кисть обучена и проверена") : qsTr("Кисть ещё не обучена")) }
            }
            Flow {
                Layout.fillWidth: true; spacing: 8
                ActionButton { objectName: "brushLearn"; text: editor.brush.profile_state === "ready" ? qsTr("Обучить заново") : qsTr("Обучить кисть"); primary: true; enabled: editor.canEdit && editor.controlReady && !!editor.brush.scratch_zone && !!backend.view.area_selected && !backend.view.preview_busy; onClicked: backend.brushCommand("learn") }
                ActionButton { objectName: "brushLearnSpeed"; text: qsTr("Подобрать только скорость"); hint: qsTr("Без регулятора размера: паузы, при которых программа не теряет штрихи")
                               enabled: editor.canEdit && !!editor.brush.scratch_zone && !!backend.view.area_selected && !backend.view.preview_busy
                               onClicked: backend.brushCommand("learn_speed") }
                ActionButton { text: qsTr("Сбросить калибровку"); enabled: editor.canEdit; onClicked: reset.open() }
            }
        }
    }

    Card {
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 12
            ToggleSwitch { id: advanced; objectName: "brushAdvanced"; text: qsTr("Дополнительно"); checked: false }
            ToggleSwitch { visible: advanced.checked && editor.mode === "slider"; text: qsTr("Перетаскивать бегунок, а не кликать по дорожке"); checked: !!editor.brush.drag_enabled; enabled: editor.canEdit; onClicked: backend.setBrush({drag_enabled: checked}) }
            ToggleSwitch { visible: advanced.checked; text: qsTr("Проверять размер пробным мазком перед рисунком"); checked: !!editor.brush.verify_at_draw; enabled: editor.canEdit; onClicked: backend.setBrush({verify_at_draw: checked}) }
            ToggleSwitch { objectName: "brushTextAuto"; visible: advanced.checked && editor.mode === "text"; text: qsTr("Подбирать диапазон размеров автоматически"); checked: editor.brush.text_auto !== false; enabled: editor.canEdit; onClicked: backend.setBrush({text_auto: checked}) }
            GridLayout {
                visible: advanced.checked && editor.mode === "text" && editor.brush.text_auto === false; Layout.fillWidth: true; columns: editor.width < 520 ? 1 : 2; columnSpacing: 16
                Label { text: qsTr("Минимум") }
                InputField { id: minimum; objectName: "brushMinimum"; Layout.fillWidth: true; text: String(editor.brush.min_value); enabled: editor.canEdit }
                Label { text: qsTr("Максимум") }
                InputField { id: maximum; objectName: "brushMaximum"; Layout.fillWidth: true; text: String(editor.brush.max_value); enabled: editor.canEdit }
                Label { text: qsTr("Исходный размер") }
                InputField { id: initial; objectName: "brushDefault"; Layout.fillWidth: true; text: String(editor.brush.default_value); enabled: editor.canEdit }
                Label { text: qsTr("Шаг изменения") }
                InputField { id: step; objectName: "brushStep"; Layout.fillWidth: true; text: String(editor.brush.step_value); enabled: editor.canEdit }
                Item { visible: editor.width >= 520 }
                ActionButton {
                    objectName: "brushApplyRange"; text: qsTr("Применить диапазон"); enabled: editor.canEdit
                    Layout.fillWidth: true
                    onClicked: backend.setBrush({min_value: Number(minimum.text.replace(",", ".")), max_value: Number(maximum.text.replace(",", ".")), default_value: Number(initial.text.replace(",", ".")), step_value: Number(step.text.replace(",", "."))})
                }
            }
        }
    }

    SurfaceDialog {
        id: reset; parent: Overlay.overlay; anchors.centerIn: parent; modal: true
        title: qsTr("Сбросить калибровку кисти?"); standardButtons: Dialog.Yes | Dialog.No
        Label { text: qsTr("Координаты и обученный профиль будут удалены.") }
        onAccepted: backend.brushCommand("reset")
    }
}
