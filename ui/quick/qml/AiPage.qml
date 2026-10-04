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
            RowLayout {
                Layout.fillWidth: true; spacing: 10
                visible: !!page.ai.enabled
                Label { text: qsTr("Где считать"); color: Theme.muted; font.pixelSize: 12 }
                ChoiceBox {
                    objectName: "aiDevice"
                    Layout.preferredWidth: 220
                    model: [{id: "auto", label: qsTr("Автоматически")}, {id: "gpu", label: qsTr("Видеокарта")}, {id: "cpu", label: qsTr("Процессор")}]
                    textRole: "label"; valueRole: "id"
                    currentIndex: page.indexOfId(model, page.ai.device)
                    onActivated: page.backend.aiSet("device", currentValue)
                }
                Label {
                    Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 12; color: Theme.muted
                    text: page.ai.device_used === "gpu"
                          ? qsTr("Сейчас: видеокарта ") + page.ai.gpu_name
                          : qsTr("Сейчас: процессор (медленнее, но работает везде)")
                }
            }
            Label {
                visible: !!page.ai.message
                Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 13
                color: page.ai.error ? Theme.danger : Theme.accentText
                text: page.ai.message || ""
            }
            RowLayout {
                objectName: "aiToolsHint"
                visible: !!page.ai.enabled
                Layout.fillWidth: true; spacing: 10
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
            RowLayout {
                Layout.fillWidth: true
                Label { text: qsTr("Модели"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true }
                ActionButton {
                    objectName: "aiInstallRecommended"
                    text: qsTr("Установить рекомендуемые"); iconName: "plus"
                    visible: (page.ai.models || []).some(m => m.recommended && !m.installed && !m.installing)
                    onClicked: (page.ai.models || []).forEach(m => { if (m.recommended && !m.installed && !m.installing) page.backend.aiModel("install", m.id) })
                }
                ActionButton { text: qsTr("Папка"); iconName: "folder-open"; subtle: true; onClicked: page.backend.aiCommand("open_folder") }
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
                    RowLayout {
                        Layout.fillWidth: true; Layout.topMargin: 4; spacing: 10
                        ColumnLayout {
                            Layout.fillWidth: true; spacing: 2
                            RowLayout {
                                spacing: 8
                                Label { text: row.modelData.title; font.weight: Font.DemiBold }
                                Label {
                                    visible: !!row.modelData.recommended
                                    text: qsTr("рекомендуем"); color: Theme.accentText; font.pixelSize: 11
                                }
                                Label { text: page.taskNames[row.modelData.task] || ""; color: Theme.muted; font.pixelSize: 11 }
                            }
                            Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12; text: row.modelData.description }
                            Label {
                                font.pixelSize: 11; color: Theme.muted
                                text: row.modelData.size_mb + qsTr(" МБ · лицензия ") + row.modelData.license
                            }
                            ProgressBar {
                                visible: !!row.modelData.installing
                                Layout.fillWidth: true; from: 0; to: 1; value: row.modelData.progress || 0
                            }
                        }
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
                            onClicked: page.backend.aiModel("remove", row.modelData.id)
                        }
                    }
                }
            }
        }
    }
}
