import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Local AI (AI-001): switch, device and the model manager. The picture tools
// live in «Рисование» → «Обработка» (IMAGE-EDIT-001), next to the other ways to
// remove a background. Everything is computed by application/ai.py.
ColumnLayout {
    id: page
    required property var backend
    property bool narrow: false
    readonly property var ai: backend.view.ai || ({models: [], tools: {}})
    readonly property var tools: ai.tools || ({})
    readonly property bool canEdit: !!backend.view.can_edit
    readonly property bool idle: canEdit && !ai.busy
    signal processingRequested()
    spacing: 14

    function modelsOf(task) { return (ai.models || []).filter(m => m.task === task) }
    // «Установить рекомендуемые» downloads about a gigabyte: the button says how much.
    readonly property var recommendedMissing: (ai.models || []).filter(m => m.recommended && !m.installed && !m.installing)
    function sizeText(mb) { return mb >= 1024 ? (mb / 1024).toLocaleString(Qt.locale(), "f", 1) + " " + qsTr("ГБ") : Math.round(mb) + " " + qsTr("МБ") }
    function installedOf(task) { return modelsOf(task).filter(m => m.installed) }
    function indexOfId(list, id) { for (let i = 0; i < list.length; ++i) if (list[i].id === id) return i; return -1 }
    readonly property var taskNames: ({
        background: qsTr("Удаление фона"), segment: qsTr("Выбор объекта"), depth: qsTr("Глубина"),
        lineart: qsTr("Контуры"), inpaint: qsTr("Стирание"), upscale: qsTr("Увеличение") })

    // ----- switch and device ---------------------------------------------------
    Card {
        objectName: "aiSwitchCard"
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 10
            ToggleSwitch {
                objectName: "aiEnabled"
                text: qsTr("Использовать AI")
                font.pixelSize: 15; font.weight: Font.DemiBold
                checked: !!page.ai.enabled
                onToggled: page.backend.aiSet("enabled", checked)
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 13
                text: qsTr("Всё работает на этом компьютере, без интернета и без отправки картинок. Скачиваются только те модели, которые вы выберете ниже; остальное программа не трогает.")
            }
            // A row item that is not fillWidth keeps its natural width: on a narrow
            // window everything stacks, or the page grows past the window edge.
            GridLayout {
                Layout.fillWidth: true; columns: page.narrow ? 1 : 3; columnSpacing: 10; rowSpacing: 6
                visible: !!page.ai.enabled
                Label { text: qsTr("Где считать"); color: Theme.muted; font.pixelSize: 12 }
                ChoiceBox {
                    objectName: "aiDevice"
                    Layout.preferredWidth: 220; Layout.fillWidth: page.narrow
                    model: [{id: "auto", label: qsTr("Автоматически")}, {id: "gpu", label: qsTr("Видеокарта")}, {id: "cpu", label: qsTr("Процессор")}]
                    textRole: "label"; valueRole: "id"
                    currentIndex: page.indexOfId(model, page.ai.device)
                    onActivated: page.backend.aiSet("device", currentValue)
                }
                Label {
                    Layout.fillWidth: true; font.pixelSize: 12; color: Theme.muted
                    wrapMode: page.narrow ? Text.WordWrap : Text.NoWrap; elide: page.narrow ? Text.ElideNone : Text.ElideRight
                    text: page.ai.device_used === "gpu"
                          ? qsTr("Сейчас: видеокарта ") + page.ai.gpu_name
                            + (page.ai.gpu_memory_gb >= 1 ? " " + qsTr("(%1 ГБ)").arg(Math.round(page.ai.gpu_memory_gb)) : "")
                          : qsTr("Сейчас: процессор (медленнее, но работает везде)")
                }
            }
            Label {
                visible: !!page.ai.message
                Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 13
                color: page.ai.error ? Theme.danger : Theme.accentText
                text: page.ai.message || ""
            }
            GridLayout {
                objectName: "aiToolsHint"
                visible: !!page.ai.enabled
                Layout.fillWidth: true; columns: page.narrow ? 1 : 2; columnSpacing: 10; rowSpacing: 8
                Label {
                    Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 13
                    text: qsTr("Убрать фон, выбрать объект, увеличить или упростить картинку — в «Рисование» → «Обработка», рядом с остальными способами.")
                }
                ActionButton {
                    objectName: "aiOpenProcessing"
                    text: qsTr("Открыть обработку"); iconName: "sparkles"
                    enabled: !!page.backend.view.image_loaded
                    hint: page.backend.view.image_loaded ? "" : qsTr("Сначала откройте картинку")
                    onClicked: page.processingRequested()
                }
            }
        }
    }

    // ----- model manager ----------------------------------------------------------------
    Card {
        objectName: "aiModelsCard"
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 10
            GridLayout {
                Layout.fillWidth: true; columns: page.narrow ? 1 : 2; columnSpacing: 10; rowSpacing: 8
                Label { text: qsTr("Модели"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true }
                ButtonRow {
                    Layout.fillWidth: page.narrow
                    ActionButton {
                        objectName: "aiInstallRecommended"
                        text: qsTr("Установить рекомендуемые") + " · " + page.sizeText(page.recommendedMissing.reduce((sum, m) => sum + (m.size_mb || 0), 0)); iconName: "plus"
                        visible: page.recommendedMissing.length > 0
                        onClicked: page.recommendedMissing.forEach(m => page.backend.aiModel("install", m.id))
                    }
                    ActionButton { text: qsTr("Папка"); iconName: "folder-open"; subtle: true; onClicked: page.backend.aiCommand("open_folder") }
                }
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
                text: qsTr("Модели не входят в программу. Ставьте только нужные: скачивание проверяется по контрольной сумме, прерванное продолжится с того же места. Все лицензии разрешают коммерческое использование.")
            }
            Repeater {
                objectName: "aiModelRows"
                model: page.ai.models || []
                delegate: ColumnLayout {
                    id: row
                    required property var modelData
                    required property int index
                    Layout.fillWidth: true; spacing: 6
                    Rectangle { visible: row.index > 0; Layout.fillWidth: true; implicitHeight: 1; color: Theme.border }
                    GridLayout {
                        Layout.fillWidth: true; Layout.topMargin: 4
                        columns: page.narrow ? 1 : 2; columnSpacing: 10; rowSpacing: 8
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 2
                            // Title and badges wrap: a long title in one row pushed the page 224 px
                            // past a narrow window (owner, 2026-10-05).
                            Flow {
                                Layout.fillWidth: true; spacing: 8
                                Label { text: row.modelData.title; font.weight: Font.DemiBold; width: Math.min(implicitWidth, parent.width); wrapMode: Text.WordWrap }
                                Label {
                                    visible: !!row.modelData.recommended; topPadding: 2
                                    text: qsTr("рекомендуем"); color: Theme.accentText; font.pixelSize: 11
                                }
                                Label { text: page.taskNames[row.modelData.task] || ""; color: Theme.muted; font.pixelSize: 11; topPadding: 2 }
                            }
                            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12; text: row.modelData.description }
                            Label {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 11; color: Theme.muted
                                text: row.modelData.size_mb + qsTr(" МБ · лицензия ") + row.modelData.license
                            }
                            ProgressBar {
                                visible: !!row.modelData.installing
                                Layout.fillWidth: true; from: 0; to: 1; value: row.modelData.progress || 0
                                palette.dark: Theme.accent; palette.midlight: Theme.input
                            }
                        }
                        RowLayout {
                            spacing: 10
                            Layout.alignment: page.narrow ? Qt.AlignLeft | Qt.AlignVCenter : Qt.AlignRight | Qt.AlignVCenter
                            Label {
                                visible: !!row.modelData.installed
                                text: qsTr("Установлена"); color: Theme.accentText; font.pixelSize: 12
                            }
                            ActionButton {
                                objectName: "aiInstall_" + row.modelData.id
                                visible: !row.modelData.installed && !row.modelData.installing
                                text: qsTr("Установить"); iconName: "plus"
                                onClicked: page.backend.aiModel("install", row.modelData.id)
                            }
                            ActionButton {
                                visible: !!row.modelData.installing
                                text: Math.round((row.modelData.progress || 0) * 100) + "% · " + qsTr("Отмена"); subtle: true
                                onClicked: page.backend.aiModel("cancel", row.modelData.id)
                            }
                            ActionButton {
                                visible: !!row.modelData.installed
                                text: qsTr("Удалить"); subtle: true; danger: true; enabled: !page.ai.busy
                                onClicked: { removeModel.modelId = row.modelData.id; removeModel.modelTitle = row.modelData.title; removeModel.size = row.modelData.size_mb || 0; removeModel.open() }
                            }
                        }
                    }
                }
            }
        }
    }

    SurfaceDialog {
        id: removeModel
        objectName: "removeModelDialog"
        property string modelId: ""
        property string modelTitle: ""
        property real size: 0
        parent: Overlay.overlay
        anchors.centerIn: parent
        modal: true
        title: qsTr("Удалить модель?")
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: page.backend.aiModel("remove", modelId)
        ColumnLayout {
            width: parent.width
            Label {
                text: qsTr("«%1» удалится с компьютера. Чтобы пользоваться ею снова, её придётся скачать заново (%2).").arg(removeModel.modelTitle).arg(page.sizeText(removeModel.size))
                Layout.fillWidth: true; wrapMode: Text.WordWrap
            }
        }
    }
}
