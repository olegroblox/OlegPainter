import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// HELP-001: first steps, support and community links, what to send when
// something breaks. Links live in application/support.py.
ColumnLayout {
    id: page
    required property var backend
    property bool narrow: false
    signal pageRequested(int page)
    signal driverConsentRequested(string command)
    signal driverRestartRequested()
    spacing: 14

    // UPDATE-001: a new version on top of the page while there is one; the version,
    // the check and its switch live in the last card.
    readonly property var updateInfo: backend.updateInfo || ({})
    readonly property string updateState: updateInfo.state || "idle"
    Card {
        id: updatesCard
        objectName: "helpUpdates"
        readonly property bool working: page.updateState === "downloading" || page.updateState === "unpacking"
        visible: ["available", "downloading", "unpacking", "ready", "installing"].indexOf(page.updateState) >= 0
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 10
            Label {
                text: qsTr("Новая версия %1").arg(page.updateInfo.latest || "")
                font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap
            }
            Label {
                objectName: "updateMessage"
                visible: !!page.updateInfo.message
                Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 13
                color: page.updateInfo.error ? Theme.danger : Theme.accentText
                text: page.updateInfo.message || ""
            }
            ProgressBar {
                objectName: "updateProgress"
                visible: updatesCard.working
                Layout.fillWidth: true; from: 0; to: 1; value: page.updateInfo.progress || 0
                palette.dark: Theme.accent; palette.midlight: Theme.input       // the theme's colours, not the grey default
            }
            // «Что нового» of the release, as written on GitHub.
            ScrollView {
                id: notesScroll
                objectName: "updateNotes"
                visible: !!page.updateInfo.notes
                Layout.fillWidth: true; Layout.preferredHeight: Math.min(notesText.implicitHeight, 220)
                clip: true; contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                Label {
                    id: notesText
                    width: notesScroll.availableWidth
                    textFormat: Text.MarkdownText; wrapMode: Text.WordWrap; font.pixelSize: 13
                    text: page.updateInfo.notes || ""
                    onLinkActivated: function(link) { Qt.openUrlExternally(link) }
                }
            }
            ButtonRow {
                Layout.fillWidth: true
                ActionButton {
                    objectName: "downloadUpdate"
                    visible: page.updateState === "available"
                    primary: true; iconName: "plus"
                    text: (page.updateInfo.can_install ? qsTr("Обновить до %1") : qsTr("Скачать версию %1")).arg(page.updateInfo.latest)
                          + (page.updateInfo.can_install && page.updateInfo.size_mb ? " · " + page.updateInfo.size_mb + " " + qsTr("МБ") : "")
                    onClicked: page.backend.downloadUpdate()
                }
                ActionButton {
                    objectName: "cancelUpdate"
                    visible: updatesCard.working
                    text: qsTr("Остановить"); subtle: true
                    onClicked: page.backend.cancelUpdate()
                }
                ActionButton {
                    objectName: "installUpdate"
                    visible: page.updateState === "ready"
                    primary: true; iconName: "play"
                    text: qsTr("Перезапустить и обновить")
                    onClicked: page.backend.installUpdate()
                }
                ActionButton {
                    objectName: "openUpdatePage"
                    visible: page.updateState === "available" || page.updateState === "ready"
                    text: qsTr("Страница загрузки"); subtle: true
                    onClicked: page.backend.openUpdatePage()
                }
            }
        }
    }

    Card {
        objectName: "helpStart"
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 10
            Label { text: qsTr("Как нарисовать первую картинку"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 13; lineHeight: 1.25
                readonly property string stopKey: (page.backend.view.bindings || {}).stop || ""
                text: qsTr("1. Выберите, где будете рисовать: игру Roblox, Gartic Phone или другую программу.\n2. Откройте картинку или вставьте её из буфера.\n3. Обведите на экране холст — место, где появится рисунок.\n4. Покажите программе, как выбирается цвет: поле HEX, круг или готовые цвета.\n5. Нажмите «Начать рисование» и не трогайте мышь.")
                      + (stopKey ? " " + qsTr("Остановить — клавишей %1.").arg(stopKey) : "")
            }
            Flow {
                Layout.fillWidth: true; spacing: 8
                ActionButton { objectName: "helpQuickStart"; text: qsTr("Открыть «Быстрый старт»"); iconName: "play"; primary: true; onClicked: page.pageRequested(7) }
                ActionButton { text: qsTr("Горячие клавиши"); iconName: "keyboard"; subtle: true; onClicked: page.pageRequested(3) }
            }
        }
    }

    Card {
        objectName: "helpWhatsNew"
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 8
            Label { text: qsTr("Что нового в версии %1").arg(page.backend.appVersion); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Repeater {
                model: page.backend.whatsNew
                Label { required property string modelData; text: "• " + modelData; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 13 }
            }
        }
    }

    Card {
        objectName: "helpLinks"
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 12
            Label { text: qsTr("Поддержка и сообщество"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 13
                text: qsTr("Видео покажут настройку по шагам. Если что-то не получается — напишите автору или спросите в чате.")
            }
            GridLayout {
                Layout.fillWidth: true
                columns: page.narrow ? 1 : 3
                columnSpacing: 10; rowSpacing: 10
                Repeater {
                    model: page.backend.supportLinks
                    delegate: ChoiceTile {
                        required property var modelData
                        objectName: "supportLink_" + modelData.id
                        Layout.fillWidth: true; Layout.preferredWidth: 1; Layout.fillHeight: true
                        title: modelData.title
                        detail: modelData.detail
                        overline: modelData.enabled ? "" : qsTr("скоро")
                        enabled: modelData.enabled
                        onClicked: page.backend.openLink(modelData.id)
                    }
                }
            }
        }
    }

    // DRIVER-001/002: the third-party input driver — where Windows has it, its state, install,
    // repair or removal after an informed yes, the restart it needs, and known conflicts.
    Card {
        id: driverCard
        objectName: "helpDriver"
        readonly property var driver: page.backend.inputDriver || ({})
        readonly property string driverState: driver.state || ""
        readonly property bool working: !!driver.busy
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 10
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                Label { text: qsTr("Драйвер мыши Interception"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Label {
                    objectName: "driverStatus"
                    font.pixelSize: 13; font.weight: Font.DemiBold
                    color: driverCard.driverState === "ready" ? Theme.accentText
                           : driverCard.driverState === "pending_removal" || (driverCard.driverState === "unsupported" && !driverCard.driver.unsupported_arch) ? Theme.muted : Theme.danger
                    text: ({
                        "ready": qsTr("работает"),
                        "no_mouse": qsTr("не видит мышь"),
                        "pending_removal": qsTr("удалён, работает до перезагрузки"),
                        "reboot": qsTr("нужна перезагрузка"),
                        "blocked": qsTr("Windows его не запустила"),
                        "unreachable": qsTr("нет связи"),
                        "incomplete": qsTr("установлен не полностью"),
                        "broken": qsTr("повреждён!"),
                        "missing": qsTr("не установлен"),
                        "unsupported": qsTr("не поддерживается")
                    })[driverCard.driverState] || ""
                }
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 13
                text: qsTr("Через него OlegPainter водит мышью: без драйвера рисование не начнётся ни в играх, ни в Paint. Это сторонний драйвер: мы его не разрабатываем и не отвечаем за его работу и ошибки. Ставить его или нет — решаете вы.")
            }
            Label {
                objectName: "driverExplanation"
                visible: text !== ""
                Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 13
                color: ["broken", "blocked", "incomplete", "unreachable", "no_mouse"].indexOf(driverCard.driverState) >= 0 ? Theme.danger : Theme.text
                font.weight: driverCard.driverState === "broken" ? Font.DemiBold : Font.Normal
                text: ({
                    "no_mouse": qsTr("Драйвер работает, но через него не идёт ни одна мышь — так бывает после переподключения устройств. Переподключите мышь; если не поможет — перезагрузите компьютер."),
                    "pending_removal": qsTr("Драйвер удалён из Windows, но работает до перезагрузки. После неё рисовать будет нельзя, пока вы не установите его снова."),
                    "reboot": qsTr("Драйвер установлен, но Windows подключит его только после перезагрузки."),
                    "blocked": qsTr("Компьютер перезагружался, но Windows не запустила драйвер. Частые причины: включена «Целостность памяти», драйвер заблокировал антивирус или не подключены клавиатура либо мышь. Если не знаете, что выбрать, — удалите драйвер."),
                    "unreachable": qsTr("Windows запустила драйвер, но OlegPainter не может к нему подключиться. Перезапустите программу; если не поможет — перезагрузите компьютер."),
                    "incomplete": qsTr("Установлена только часть драйвера — для клавиатуры или для мыши. Нажмите «Починить»: установщик автора поставит его заново."),
                    "broken": qsTr("Драйвер повреждён: Windows ждёт его при запуске, но файла или записи службы нет. После перезагрузки могут перестать работать мышь и клавиатура. Не перезагружайте компьютер — сначала нажмите «Починить» или «Удалить драйвер». Обычно так бывает, если файлы удалили вручную или их забрал антивирус."),
                    "unsupported": driverCard.driver.unsupported_arch ? qsTr("Драйвер Interception есть только для процессоров x86 и x64. На этом компьютере OlegPainter не сможет управлять мышью.") : ""
                })[driverCard.driverState] || (driverCard.driverState === "missing" && driverCard.driver.leftovers
                                                ? qsTr("От прежней установки остались файлы или записи драйвера. Они безвредны; установка их перезапишет.") : "")
            }
            Label {
                objectName: "driverLocation"
                visible: !!driverCard.driver.location
                Layout.fillWidth: true; wrapMode: Text.Wrap; font.pixelSize: 13; color: Theme.muted
                text: qsTr("Где: %1").arg(driverCard.driver.location || "")
                      + (driverCard.driver.version ? "\n" + qsTr("Версия %1, файлы записаны %2.").arg(driverCard.driver.version).arg(driverCard.driver.installed_at || "—") : "")
            }
            Label {
                objectName: "driverOrigin"
                visible: !!driverCard.driver.installed
                         && (!!driverCard.driver.custom_location || !!driverCard.driver.renamed || (driverCard.driver.users || []).length > 0)
                Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 13; color: Theme.muted
                text: (driverCard.driver.users || []).length
                      ? qsTr("Похоже, драйвер поставила программа %1 — без него она тоже не будет работать.").arg(driverCard.driver.users.join(", "))
                      : qsTr("Драйвер установлен не по инструкции автора — вероятно, другой программой. OlegPainter работает с ним так же.")
            }
            Label {
                objectName: "driverDevices"
                visible: driverCard.driver.mice !== undefined && driverCard.driver.mice >= 0
                Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 13; color: Theme.muted
                text: qsTr("Через драйвер идут мыши: %1, клавиатуры: %2.").arg(driverCard.driver.mice).arg(driverCard.driver.keyboards)
            }
            Label {
                objectName: "driverHvci"
                visible: !!driverCard.driver.hvci
                Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 13; color: Theme.danger
                text: driverCard.driver.installed
                      ? qsTr("Включена «Целостность памяти» (изоляция ядра) — с ней Windows не загружает Interception. Удалите драйвер или выключите эту защиту; выключать её ради драйвера мы не советуем.")
                      : qsTr("Включена «Целостность памяти» (Безопасность Windows → Безопасность устройства → Изоляция ядра). Interception с ней несовместим: Windows не загрузит драйвер, а мышь и клавиатура могут перестать работать. Поэтому установка выключена. Выключать эту защиту ради драйвера мы не советуем.")
            }
            Label {
                objectName: "driverConflicts"
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.text; font.pixelSize: 13
                text: (driverCard.driver.anticheats || []).length
                      ? qsTr("На этом компьютере есть античиты: %1. Многие игры с ними работают с драйвером без проблем, но гарантий нет: античит может не пустить в игру или счесть драйвер нарушением правил. Решение и риск — на вас. Если игра не запускается — удалите драйвер перед ней, а после — установите снова.").arg(driverCard.driver.anticheats.join(", "))
                      : qsTr("Игры с античитами (Easy Anti-Cheat, Riot Vanguard, EA Javelin, FACEIT) часто работают с драйвером без проблем, но гарантий нет: античит может не пустить в игру или счесть драйвер нарушением правил. Решение и риск — на вас. Если игра не запускается — удалите драйвер перед ней, а после — установите снова.")
            }
            Label {
                objectName: "driverMessage"
                visible: !!driverCard.driver.message
                Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 13
                color: driverCard.driver.error ? Theme.danger : Theme.accentText
                text: driverCard.driver.message || ""
            }
            ProgressBar {
                objectName: "driverProgress"
                visible: driverCard.working
                Layout.fillWidth: true; from: 0; to: 1; value: driverCard.driver.progress || 0
                palette.dark: Theme.accent; palette.midlight: Theme.input
            }
            ButtonRow {
                Layout.fillWidth: true
                enabled: !driverCard.working
                ActionButton {
                    objectName: "helpInstallDriver"
                    visible: driverCard.driverState === "missing" && !driverCard.driver.hvci
                    text: qsTr("Установить драйвер"); primary: true
                    onClicked: page.driverConsentRequested("install")
                }
                ActionButton {
                    objectName: "helpRepairDriver"
                    visible: (driverCard.driverState === "broken" || driverCard.driverState === "incomplete") && !driverCard.driver.hvci
                    text: qsTr("Починить"); primary: true
                    onClicked: page.driverConsentRequested("repair")
                }
                ActionButton {
                    objectName: "helpRestart"
                    visible: ["reboot", "pending_removal", "unreachable", "no_mouse"].indexOf(driverCard.driverState) >= 0 && !driverCard.driver.restart_scheduled
                    text: qsTr("Перезагрузить компьютер")
                    primary: driverCard.driverState === "reboot" || driverCard.driverState === "pending_removal"
                    onClicked: page.driverRestartRequested()
                }
                ActionButton {
                    objectName: "cancelRestart"
                    visible: !!driverCard.driver.restart_scheduled
                    text: qsTr("Отменить перезагрузку")
                    onClicked: page.backend.driverAction("cancel_restart")
                }
                ActionButton {
                    objectName: "removeDriver"
                    visible: !!driverCard.driver.installed
                    text: qsTr("Удалить драйвер"); danger: driverCard.driverState === "broken"
                    onClicked: page.driverConsentRequested("uninstall")
                }
                ActionButton {
                    objectName: "coreIsolation"
                    visible: !!driverCard.driver.hvci || driverCard.driverState === "blocked"
                    text: qsTr("Открыть «Изоляцию ядра»"); subtle: true
                    onClicked: page.backend.driverAction("core_isolation")
                }
                ActionButton {
                    objectName: "showDriverFiles"
                    visible: !!driverCard.driver.has_files
                    text: qsTr("Показать файлы"); subtle: true
                    onClicked: page.backend.driverAction("show_files")
                }
                ActionButton { objectName: "driverAuthorPage"; text: qsTr("Страница автора"); subtle: true; onClicked: page.backend.driverAction("page") }
                ActionButton { objectName: "recheckDriverHelp"; text: qsTr("Проверить драйвер"); subtle: true; onClicked: page.backend.driverAction("recheck") }
            }
        }
    }

    Card {
        objectName: "helpTrouble"
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 10
            Label { text: qsTr("Если что-то не работает"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 13
                text: qsTr("Опишите, что делали и что пошло не так, и приложите последний файл журнала: так проблему найдут быстрее.")
            }
            Flow {
                Layout.fillWidth: true; spacing: 8
                ActionButton { objectName: "openLogs"; text: qsTr("Открыть папку журналов"); iconName: "folder-open"; onClicked: page.backend.openLogsFolder() }
            }
        }
    }

    Card {
        objectName: "helpVersion"
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 10
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                Label { text: qsTr("Версия и обновления"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Label { objectName: "updateVersion"; text: qsTr("Версия ") + page.backend.appVersion; color: Theme.muted; font.pixelSize: 12 }
            }
            Label {
                objectName: "updateStatus"
                visible: !updatesCard.visible && !!page.updateInfo.message
                Layout.fillWidth: true; wrapMode: Text.WordWrap; font.pixelSize: 13
                color: page.updateInfo.error ? Theme.danger : Theme.muted
                text: page.updateInfo.message || ""
            }
            ButtonRow {
                Layout.fillWidth: true
                ActionButton {
                    objectName: "checkUpdates"
                    text: page.updateState === "checking" ? qsTr("Проверяем…") : qsTr("Проверить обновления")
                    enabled: ["idle", "latest", "available"].indexOf(page.updateState) >= 0
                    onClicked: page.backend.checkUpdates()
                }
                ActionButton {
                    visible: !!page.updateInfo.error
                    text: qsTr("Страница загрузки"); subtle: true
                    onClicked: page.backend.openUpdatePage()
                }
            }
            ToggleSwitch {
                objectName: "updateAutoCheck"
                text: qsTr("Проверять обновления раз в день")
                checked: !!page.updateInfo.auto_check
                onClicked: page.backend.setUpdateAutoCheck(checked)
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
                text: qsTr("Программа спрашивает у GitHub только номер последней версии — о вас ничего не отправляется. Новая версия скачивается целиком, а настройки, профили и модели остаются на месте.")
            }
        }
    }
}
