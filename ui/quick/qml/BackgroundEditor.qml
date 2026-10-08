import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// «Обработка картинки» (IMAGE-EDIT-001/002): the background, the object, colour
// and filters, and the drawing order of the picture in one place, with undo. The presenter keeps one
// background state; this file only lays it out.
Popup {
    id: editor
    required property var backend
    readonly property var values: backend.view.background
    readonly property var ai: backend.view.ai || ({models: [], tools: {}})
    readonly property var tools: ai.tools || ({})
    readonly property bool canEdit: !!backend.view.can_edit
    readonly property bool busy: !!values.busy || !!ai.busy || !!(backend.view.edits && backend.view.edits.busy)
    readonly property bool idle: canEdit && !busy
    readonly property bool narrow: editor.width < 560
    property bool picking: false
    property bool selecting: false
    property bool subtract: false
    readonly property bool colorShown: values.method === "color" || picking
    readonly property string referenceHex: values.reference_rgb
        ? "#" + values.reference_rgb.map(v => ("0" + v.toString(16)).slice(-2)).join("").toUpperCase() : ""
    readonly property var backgroundModels: (ai.models || []).filter(m => m.task === "background" && m.installed)
    readonly property bool aiReady: !!ai.enabled && !!tools.background
    // A grey button shows no hint on hover: the missing models are named in a visible line.
    readonly property var missingTools: !ai.enabled ? [] : [["segment", qsTr("выбор объекта")], ["inpaint", qsTr("стирание")],
        ["upscale", qsTr("увеличение")], ["lineart", qsTr("контуры")], ["depth", qsTr("глубина")]].filter(p => !tools[p[0]]).map(p => p[1])
    readonly property var edits: backend.view.edits || ({})
    readonly property var filters: backend.view.filters || []
    property var adjust: ({brightness: 0, contrast: 0, saturation: 0, sharpness: 0})
    readonly property bool adjusting: Object.keys(adjust).some(k => adjust[k] !== 0)
    function setAdjust(key, value) {
        let next = Object.assign({}, adjust); next[key] = Math.round(value); adjust = next
        adjustTimer.restart()
    }
    function resetAdjust() { adjust = {brightness: 0, contrast: 0, saturation: 0, sharpness: 0}; backend.previewAdjust({}) }
    Timer { id: adjustTimer; interval: 90; onTriggered: editor.backend.previewAdjust(editor.adjust) }
    Shortcut { sequences: [StandardKey.Undo]; enabled: editor.opened && editor.idle && !!editor.edits.can_undo; onActivated: editor.backend.undoEdit() }
    Shortcut { sequences: [StandardKey.Redo, "Ctrl+Y"]; enabled: editor.opened && editor.idle && !!editor.edits.can_redo; onActivated: editor.backend.redoEdit() }
    signal pageRequested(int page)
    signal viewRequested(bool original)
    objectName: "backgroundEditor"
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(980, parent.width - 40)
    height: Math.min(780, parent.height - 40)
    padding: narrow ? 12 : 24
    modal: true
    closePolicy: Popup.CloseOnEscape
    onOpened: { picking = false; selecting = false; hexField.text = referenceHex }
    onClosed: if (adjusting) resetAdjust()
    background: Rectangle { color: Theme.surface; radius: 16; border.color: Theme.border }
    enter: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.normal; easing.type: Easing.OutCubic } }
    exit: Transition { NumberAnimation { property: "opacity"; from: 1; to: 0; duration: Theme.fast } }
    Overlay.modal: Rectangle { color: Theme.scrim }

    function indexOfId(list, id) { for (let i = 0; i < list.length; ++i) if (list[i].id === id) return i; return -1 }
    function statusText() {
        if (values.busy) return qsTr("Убираем фон…")
        switch (values.method) {
        case "auto": return qsTr("Фон убран автоматически. «Оставить» вернёт его.")
        case "ai": return qsTr("Фон убран нейросетью. «Оставить» вернёт его.")
        case "color": return values.mode === "alpha" ? qsTr("Прозрачные места картинки не рисуются.")
                                                    : qsTr("Цвета, похожие на фон, не рисуются.")
        }
        return values.transparent ? qsTr("Прозрачные места картинки не рисуются.") : qsTr("Фон рисуется вместе с картинкой.")
    }

    contentItem: ColumnLayout {
        spacing: 14
        RowLayout {
            Layout.fillWidth: true; spacing: 8
            Label { text: qsTr("Обработка картинки"); font.pixelSize: 18; font.weight: Font.DemiBold; Layout.fillWidth: true; elide: Text.ElideRight }
            BusyIndicator { running: editor.busy; visible: running; implicitWidth: 24; implicitHeight: 24 }
            // Stops the network; «Отменить» beside it is the edit history.
            ActionButton {
                objectName: "abortAi"
                visible: !!editor.ai.busy; text: editor.narrow ? "" : qsTr("Прервать"); iconName: "stop"; hint: qsTr("Прервать нейросеть"); subtle: true
                onClicked: editor.backend.aiCommand("cancel")
            }
            ActionButton {
                objectName: "undoEdit"
                text: editor.narrow ? "" : qsTr("Отменить"); iconName: "undo"
                hint: editor.edits.can_undo ? qsTr("Отменить: ") + editor.edits.undo_label + " (Ctrl+Z)" : qsTr("Отменять пока нечего")
                enabled: editor.idle && !!editor.edits.can_undo
                onClicked: editor.backend.undoEdit()
            }
            ActionButton {
                objectName: "redoEdit"
                text: editor.narrow ? "" : qsTr("Повторить"); iconName: "redo"
                hint: editor.edits.can_redo ? qsTr("Повторить: ") + editor.edits.redo_label + " (Ctrl+Y)" : qsTr("Повторять нечего")
                enabled: editor.idle && !!editor.edits.can_redo
                onClicked: editor.backend.redoEdit()
            }
            ActionButton {
                objectName: "restoreOriginal"
                visible: !!editor.values.has_original
                text: editor.narrow ? "" : qsTr("Вернуть исходник"); iconName: "image"; subtle: true
                hint: qsTr("Картинка такой, какой её вставили. «Отменить» вернёт правки.")
                enabled: editor.idle
                onClicked: editor.backend.restoreOriginal()
            }
            // «Готово» keeps what the colour sliders show: closing used to drop it silently.
            ActionButton {
                objectName: "closeBackground"; text: qsTr("Готово")
                onClicked: {
                    if (editor.adjusting && editor.idle && editor.backend.applyAdjust(editor.adjust))
                        editor.adjust = {brightness: 0, contrast: 0, saturation: 0, sharpness: 0}
                    editor.close()
                }
            }
        }
        ScrollView {
            id: bodyScroll; objectName: "processingScroll"
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: bodyScroll.availableWidth; spacing: 14

                // ----- the picture before and after -----------------------------
                GridLayout {
                    columns: editor.narrow ? 1 : 2
                    Layout.fillWidth: true
                    Layout.preferredHeight: editor.narrow ? 460 : Math.max(200, Math.min(320, editor.height - 470))
                    columnSpacing: 10; rowSpacing: 10
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1
                        Label {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 12
                            color: editor.picking || editor.selecting ? Theme.accentText : Theme.muted
                            text: editor.picking ? qsTr("Нажмите на цвет фона")
                                : editor.selecting ? (editor.subtract ? qsTr("Щёлкните по тому, что убрать из выделения") : qsTr("Щёлкните по объекту"))
                                : editor.adjusting ? qsTr("Так будет после «Применить»")
                                : qsTr("Картинка — нажмите, чтобы рассмотреть")
                        }
                        ImageSurface {
                            objectName: "backgroundSource"
                            Layout.fillWidth: true; Layout.fillHeight: true
                            source: editor.selecting && editor.ai.has_selection && editor.backend.selectionUrl
                                    ? editor.backend.selectionUrl
                                    : editor.adjusting && editor.backend.adjustUrl ? editor.backend.adjustUrl
                                    : editor.backend.sourceUrl
                            pickEnabled: editor.canEdit && editor.backend.view.image_loaded
                            pickCursor: editor.picking || editor.selecting ? Qt.CrossCursor : Qt.PointingHandCursor
                            onPicked: function(x, y) {
                                if (editor.picking) {
                                    if (editor.backend.pickBackground(x, y, editor.backend.sourceUrl)) {
                                        editor.picking = false
                                        hexField.text = editor.referenceHex
                                    }
                                } else if (editor.selecting) {
                                    editor.backend.aiSelect(x, y, !editor.subtract)
                                } else {
                                    editor.viewRequested(true)
                                }
                            }
                        }
                    }
                    ColumnLayout {
                        Layout.fillWidth: true; Layout.fillHeight: true; Layout.preferredWidth: 1
                        Label { text: qsTr("Результат — нажмите, чтобы рассмотреть"); color: Theme.muted; font.pixelSize: 12; Layout.fillWidth: true; elide: Text.ElideRight }
                        ImageSurface {
                            objectName: "backgroundResult"
                            Layout.fillWidth: true; Layout.fillHeight: true
                            source: editor.backend.previewUrl
                            pickEnabled: !!editor.backend.previewUrl
                            onPicked: editor.viewRequested(false)
                            Label {
                                anchors.centerIn: parent; width: parent.width - 24; wrapMode: Text.WordWrap
                                horizontalAlignment: Text.AlignHCenter; color: Theme.muted
                                visible: !editor.backend.view.area_selected || editor.backend.view.preview_busy || !editor.backend.previewUrl
                                text: !editor.backend.view.area_selected ? qsTr("Задайте область рисования для предпросмотра")
                                    : editor.backend.view.preview_busy ? qsTr("Подготавливаем результат…") : qsTr("Предпросмотр пока недоступен")
                            }
                        }
                    }
                }

                // ----- background ---------------------------------------------------
                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.border }
                RowLayout {
                    Layout.fillWidth: true
                    Label { text: qsTr("Фон"); font.pixelSize: 15; font.weight: Font.DemiBold }
                    Label {
                        objectName: "backgroundStatus"
                        Layout.fillWidth: true; Layout.leftMargin: 8; wrapMode: Text.WordWrap; font.pixelSize: 12
                        color: editor.values.method === "none" ? Theme.muted : Theme.accentText
                        text: editor.statusText()
                    }
                }
                Flow {
                    objectName: "backgroundMethods"
                    Layout.fillWidth: true; spacing: 8
                    Repeater {
                        model: [
                            {id: "none", label: qsTr("Оставить"), hint: qsTr("Рисовать картинку целиком, с фоном")},
                            {id: "auto", label: qsTr("Автоматически"), hint: qsTr("Без нейросети: объект в центре отделяется от неба, травы, стен. Лучше всего для снимков персонажей.")},
                            {id: "ai", label: qsTr("Нейросетью"), hint: qsTr("Точнее по краям и волосам, нужна модель со страницы «AI»")},
                            {id: "color", label: qsTr("По цвету"), hint: qsTr("Не рисовать цвета, похожие на фон: однотонный фон, белый лист")}
                        ]
                        delegate: ActionButton {
                            required property var modelData
                            objectName: "backgroundMethod_" + modelData.id
                            text: modelData.label; hint: modelData.hint
                            selected: editor.values.method === modelData.id || (modelData.id === "color" && editor.picking)
                            enabled: editor.idle && editor.backend.view.image_loaded && (modelData.id !== "ai" || editor.aiReady)
                            onClicked: {
                                editor.picking = false
                                if (editor.values.method !== modelData.id) editor.backend.setBackgroundMethod(modelData.id)
                            }
                        }
                    }
                }
                RowLayout {
                    objectName: "backgroundAiNeeded"
                    visible: !editor.aiReady
                    Layout.fillWidth: true; spacing: 8
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
                        text: !editor.ai.enabled ? qsTr("«Нейросетью», выбор объекта и улучшения картинки работают с нейросетями: включите их на странице «AI».")
                                                 : qsTr("Для «Нейросетью» установите модель удаления фона на странице «AI».")
                    }
                    ActionButton { objectName: "openAiPage"; text: qsTr("Открыть AI"); iconName: "sparkles"; subtle: true; onClicked: { editor.close(); editor.pageRequested(8) } }
                }
                RowLayout {
                    visible: editor.values.method === "ai" && editor.backgroundModels.length > 1
                    Layout.fillWidth: true; spacing: 8
                    Label { text: qsTr("Модель"); color: Theme.muted; font.pixelSize: 12 }
                    ChoiceBox {
                        objectName: "aiBackgroundModel"
                        Layout.fillWidth: true
                        model: editor.backgroundModels
                        textRole: "title"; valueRole: "id"
                        currentIndex: editor.indexOfId(model, editor.ai.background_model)
                        enabled: editor.idle
                        onActivated: {
                            editor.backend.aiSet("background_model", currentValue)
                            editor.backend.setBackgroundMethod("none")
                            editor.backend.setBackgroundMethod("ai")
                        }
                    }
                }

                // «По цвету»: which colour is the background and how close counts.
                GridLayout {
                    visible: editor.colorShown
                    columns: editor.narrow ? 1 : 2
                    Layout.fillWidth: true
                    ChoiceBox {
                        objectName: "backgroundMode"; Layout.fillWidth: true; enabled: editor.canEdit
                        model: [{id:"corner",label:qsTr("Цвет верхнего левого угла")}, {id:"alpha",label:qsTr("Прозрачность изображения")}, {id:"picked",label:qsTr("Выбранный цвет")}]
                        textRole: "label"; valueRole: "id"
                        currentIndex: editor.picking ? 2 : editor.values.mode === "alpha" ? 1 : editor.values.mode === "picked" ? 2 : 0
                        onActivated: {
                            if (currentValue === "picked" && !editor.values.reference_rgb) editor.picking = true
                            else { editor.picking = false; editor.backend.setBackground({mode: currentValue}) }
                        }
                    }
                    ActionButton {
                        objectName: "pickBackgroundButton"
                        text: editor.picking ? qsTr("Отменить выбор") : qsTr("Выбрать на исходнике"); primary: editor.picking
                        enabled: editor.canEdit && editor.backend.view.image_loaded
                        onClicked: { editor.selecting = false; editor.picking = !editor.picking }
                    }
                }
                GridLayout {
                    visible: editor.colorShown
                    columns: editor.narrow ? 2 : 3
                    Layout.fillWidth: true
                    Label { text: editor.values.mode === "alpha" && !editor.picking ? qsTr("Порог прозрачности") : qsTr("Допуск цвета"); Layout.preferredWidth: 175; Layout.columnSpan: editor.narrow ? 2 : 1 }
                    ValueSlider {
                        id: threshold
                        objectName: "backgroundThreshold"; Layout.fillWidth: true; from: 0; to: 255; stepSize: 1
                        enabled: editor.canEdit
                        readonly property string key: editor.values.mode === "alpha" && !editor.picking ? "alpha_threshold" : "color_tolerance"
                        committedValue: editor.values[key]
                        onCommitRequested: function(nextValue) { let patch = {}; patch[key] = nextValue; editor.backend.setBackground(patch) }
                    }
                    InputField {
                        objectName: "backgroundThresholdValue"; Layout.preferredWidth: 75; enabled: editor.canEdit
                        text: String(threshold.draftValue); horizontalAlignment: Text.AlignRight
                        onEditingFinished: {
                            let patch = {}; patch[threshold.key] = text.trim() === "" ? NaN : Number(text.replace(",", "."))
                            editor.backend.setBackground(patch)
                            text = Qt.binding(function() { return String(threshold.draftValue) })
                        }
                    }
                }
                RowLayout {
                    visible: editor.colorShown
                    Layout.fillWidth: true; spacing: 8
                    Rectangle { width: 26; height: 26; radius: 5; color: editor.referenceHex || "transparent"; border.color: Theme.muted }
                    InputField { id: hexField; objectName: "backgroundHex"; Layout.fillWidth: true; placeholderText: "#FFFFFF"; maximumLength: 7; enabled: editor.canEdit }
                    ActionButton {
                        objectName: "applyBackgroundHex"; text: qsTr("Применить цвет"); enabled: editor.canEdit
                        onClicked: if (editor.backend.setBackgroundColor(hexField.text)) { editor.picking = false; hexField.text = editor.referenceHex }
                    }
                }
                Label {
                    visible: editor.colorShown
                    Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
                    text: editor.values.mode === "alpha" && !editor.picking
                        ? qsTr("Прозрачные пиксели ниже порога не рисуются. Для непрозрачного изображения выберите удаление по цвету.")
                        : qsTr("Удаляются похожие цвета по всему изображению, включая внутренние детали. 0 — без удаления по цвету.")
                }

                RowLayout {
                    Layout.fillWidth: true; spacing: 8
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
                        text: qsTr("Вручную: на трафарете закрасьте кистью то, что нужно рисовать, — остальное не рисуется.")
                    }
                    ActionButton {
                        objectName: "editStencilZone"
                        text: qsTr("Нарисовать зону"); iconName: "brush"; subtle: true
                        enabled: editor.idle && editor.backend.view.image_loaded
                        hint: qsTr("Откроет трафарет с кистью «Зона»")
                        onClicked: { editor.close(); editor.backend.editStencilZone() }
                    }
                }

                // New pictures: the user decides, nothing happens behind their back.
                RowLayout {
                    Layout.fillWidth: true; spacing: 10
                    ToggleSwitch {
                        objectName: "autoBackground"
                        text: qsTr("Убирать фон у новых картинок")
                        checked: editor.values.auto_new !== "off"
                        enabled: editor.canEdit
                        onClicked: {
                            editor.backend.setAutoBackground(checked ? (editor.aiReady && editor.values.method === "ai" ? "ai" : "auto") : "off")
                            checked = Qt.binding(function() { return editor.values.auto_new !== "off" })
                        }
                    }
                    ChoiceBox {
                        objectName: "autoBackgroundMethod"
                        visible: editor.values.auto_new !== "off"
                        Layout.preferredWidth: 210
                        model: [{id: "auto", label: qsTr("Автоматически")}, {id: "ai", label: qsTr("Нейросетью")}]
                        textRole: "label"; valueRole: "id"
                        currentIndex: editor.values.auto_new === "ai" ? 1 : 0
                        enabled: editor.canEdit
                        onActivated: editor.backend.setAutoBackground(currentValue)
                    }
                    Item { Layout.fillWidth: true }
                }
                Label {
                    Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
                    text: qsTr("Касается открытых файлов, вставки из буфера, найденных картинок и снимков экрана.")
                }

                // ----- the object (AI) ------------------------------------------------
                Rectangle { visible: !!editor.ai.enabled; Layout.fillWidth: true; implicitHeight: 1; color: Theme.border }
                Label { visible: !!editor.ai.enabled; text: qsTr("Объект"); font.pixelSize: 15; font.weight: Font.DemiBold }
                Flow {
                    visible: !!editor.ai.enabled
                    Layout.fillWidth: true; spacing: 8
                    ActionButton {
                        objectName: "aiSelectMode"
                        text: editor.selecting ? qsTr("Выбор включён") : qsTr("Выбрать кликом")
                        iconName: "crop"; selected: editor.selecting
                        enabled: editor.idle && !!editor.tools.segment
                        hint: editor.tools.segment ? qsTr("Щёлкните по объекту на исходнике") : qsTr("Установите модель выбора объекта на странице «AI»")
                        onClicked: { editor.picking = false; editor.selecting = !editor.selecting }
                    }
                    ActionButton {
                        visible: editor.selecting
                        text: editor.subtract ? qsTr("Режим: убрать") : qsTr("Режим: добавить"); subtle: true
                        onClicked: editor.subtract = !editor.subtract
                    }
                    ActionButton {
                        visible: !!editor.ai.has_selection
                        text: qsTr("Сбросить"); subtle: true
                        onClicked: editor.backend.aiCommand("clear_selection")
                    }
                    ActionButton {
                        objectName: "aiKeepSelection"
                        visible: !!editor.ai.has_selection
                        text: qsTr("Рисовать только его"); enabled: editor.idle
                        onClicked: { editor.selecting = false; editor.backend.aiRun("keep_selection", {}) }
                    }
                    ActionButton {
                        objectName: "aiEraseSelection"
                        visible: !!editor.ai.has_selection
                        text: qsTr("Стереть его"); iconName: "trash"
                        enabled: editor.idle && !!editor.tools.inpaint
                        hint: editor.tools.inpaint ? qsTr("Объект исчезнет, фон дорисуется") : qsTr("Установите модель стирания на странице «AI»")
                        onClicked: { editor.selecting = false; editor.backend.aiRun("erase_selection", {}) }
                    }
                }

                // ----- editing (IMAGE-EDIT-002) --------------------------------------
                Rectangle { Layout.fillWidth: true; implicitHeight: 1; color: Theme.border }
                Label { text: qsTr("Редактирование"); font.pixelSize: 15; font.weight: Font.DemiBold }
                Label { text: qsTr("Цвет и свет"); color: Theme.muted; font.pixelSize: 12 }
                GridLayout {
                    objectName: "adjustSliders"
                    Layout.fillWidth: true
                    columns: editor.narrow ? 1 : 2
                    columnSpacing: 18; rowSpacing: 2
                    Repeater {
                        model: [{id: "brightness", label: qsTr("Яркость")}, {id: "contrast", label: qsTr("Контраст")},
                                {id: "saturation", label: qsTr("Насыщенность")}, {id: "sharpness", label: qsTr("Резкость")}]
                        delegate: RowLayout {
                            required property var modelData
                            Layout.fillWidth: true; spacing: 8
                            Label { text: modelData.label; Layout.preferredWidth: 110; elide: Text.ElideRight }
                            ValueSlider {
                                objectName: "adjust_" + modelData.id
                                Layout.fillWidth: true; from: -100; to: 100; stepSize: 1
                                enabled: editor.idle && editor.backend.view.image_loaded
                                committedValue: editor.adjust[modelData.id]
                                onMoved: editor.setAdjust(modelData.id, draftValue)
                                onCommitRequested: function(nextValue) { editor.setAdjust(modelData.id, nextValue) }
                            }
                            Label {
                                text: (editor.adjust[modelData.id] > 0 ? "+" : "") + editor.adjust[modelData.id]
                                Layout.preferredWidth: 36; horizontalAlignment: Text.AlignRight; color: Theme.muted
                            }
                        }
                    }
                }
                RowLayout {
                    visible: editor.adjusting
                    Layout.fillWidth: true; spacing: 8
                    ActionButton {
                        objectName: "applyAdjust"; primary: true
                        text: qsTr("Применить"); enabled: editor.idle
                        onClicked: if (editor.backend.applyAdjust(editor.adjust)) editor.adjust = {brightness: 0, contrast: 0, saturation: 0, sharpness: 0}
                    }
                    ActionButton { objectName: "resetAdjust"; text: qsTr("Сбросить"); onClicked: editor.resetAdjust() }
                    Label { text: qsTr("На картинке — как будет. «Готово» тоже применит, «Отменить» вернёт."); color: Theme.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                }
                Label { text: qsTr("Фильтры"); color: Theme.muted; font.pixelSize: 12; Layout.topMargin: 4 }
                Flow {
                    objectName: "filterButtons"
                    Layout.fillWidth: true; spacing: 8
                    Repeater {
                        model: editor.filters.filter(f => f.group === "filters")
                        delegate: ActionButton {
                            required property var modelData
                            objectName: "filter_" + modelData.id
                            text: modelData.label; hint: modelData.hint
                            enabled: editor.idle && editor.backend.view.image_loaded
                            onClicked: editor.backend.applyFilter(modelData.id)
                        }
                    }
                }
                Label { text: qsTr("Форма"); color: Theme.muted; font.pixelSize: 12; Layout.topMargin: 4 }
                Flow {
                    Layout.fillWidth: true; spacing: 8
                    Repeater {
                        model: editor.filters.filter(f => f.group === "shape")
                        delegate: ActionButton {
                            required property var modelData
                            objectName: "filter_" + modelData.id
                            text: modelData.label; hint: modelData.hint
                            enabled: editor.idle && editor.backend.view.image_loaded
                            onClicked: editor.backend.applyFilter(modelData.id)
                        }
                    }
                }
                Label { visible: !!editor.ai.enabled; text: qsTr("Нейросеть"); color: Theme.muted; font.pixelSize: 12; Layout.topMargin: 4 }
                Flow {
                    visible: !!editor.ai.enabled
                    Layout.fillWidth: true; spacing: 8
                    ActionButton {
                        objectName: "aiUpscale2"
                        text: qsTr("Увеличить ×2 — фото"); enabled: editor.idle && !!editor.tools.upscale
                        hint: editor.tools.upscale ? qsTr("Мелкая картинка станет чётче на большой области") : qsTr("Установите модель увеличения на странице «AI»")
                        onClicked: editor.backend.aiRun("upscale", {scale: 2, style: "photo"})
                    }
                    ActionButton {
                        objectName: "aiUpscale2Art"
                        text: qsTr("Увеличить ×2 — рисунок"); enabled: editor.idle && !!editor.tools.upscale
                        hint: editor.tools.upscale ? qsTr("Режим для аниме и нарисованных картинок") : qsTr("Установите модель увеличения на странице «AI»")
                        onClicked: editor.backend.aiRun("upscale", {scale: 2, style: "anime"})
                    }
                    ActionButton {
                        objectName: "aiLineart"
                        text: qsTr("Контуры"); enabled: editor.idle && !!editor.tools.lineart
                        hint: editor.tools.lineart ? qsTr("Фото станет рисунком линиями") : qsTr("Установите модель контуров на странице «AI»")
                        onClicked: editor.backend.aiRun("lineart", {})
                    }
                }
                ToggleSwitch {
                    objectName: "aiDepthOrder"
                    visible: !!editor.ai.enabled
                    text: qsTr("Рисовать от дальнего плана к ближнему")
                    enabled: editor.canEdit && !!editor.tools.depth
                    checked: !!editor.ai.depth_order
                    onToggled: editor.backend.aiSet("depth_order", checked)
                }
                RowLayout {
                    objectName: "aiModelsMissing"
                    visible: editor.missingTools.length > 0
                    Layout.fillWidth: true; spacing: 8
                    Label {
                        Layout.fillWidth: true; Layout.preferredWidth: 1; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
                        text: qsTr("Серые кнопки ждут моделей: ") + editor.missingTools.join(", ") + qsTr(". Их можно установить на странице «AI».")
                    }
                    ActionButton { text: qsTr("Открыть AI"); iconName: "sparkles"; subtle: true; onClicked: { editor.close(); editor.pageRequested(8) } }
                }
                Label {
                    visible: !!editor.ai.message
                    Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 12
                    color: editor.ai.error ? Theme.danger : Theme.accentText
                    text: editor.ai.message || ""
                }
                Label {
                    objectName: "backgroundError"; visible: editor.backend.messageError
                    text: editor.backend.message; color: Theme.danger; wrapMode: Text.WordWrap
                    Layout.fillWidth: true; maximumLineCount: 2; elide: Text.ElideRight
                }
            }
        }
    }
}
