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
    signal driverInstallRequested()
    signal driverRemoveRequested()
    spacing: 14

    Card {
        objectName: "helpStart"
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 10
            Label { text: qsTr("Как нарисовать первую картинку"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 13; lineHeight: 1.25
                text: qsTr("1. Выберите, где будете рисовать: игру Roblox, Gartic Phone или другую программу.\n2. Откройте картинку или вставьте её из буфера.\n3. Обведите на экране холст — место, где появится рисунок.\n4. Покажите программе, как выбирается цвет: поле HEX, круг или готовые цвета.\n5. Нажмите «Начать рисование» и не трогайте мышь. Остановить — клавишей «Стоп».")
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
                        Layout.fillWidth: true; Layout.preferredWidth: 1
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

    // The third-party input driver: state, install or removal after an informed yes, known conflicts.
    Card {
        objectName: "helpDriver"
        Layout.fillWidth: true
        ColumnLayout {
            anchors.fill: parent; spacing: 10
            RowLayout {
                Layout.fillWidth: true; spacing: 8
                Label { text: qsTr("Драйвер мыши Interception"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Label {
                    objectName: "driverStatus"
                    readonly property string driverState: page.backend.inputDriver.state
                    font.pixelSize: 13; font.weight: Font.DemiBold
                    color: driverState === "ready" ? Theme.accentText : Theme.danger
                    text: driverState === "ready" ? qsTr("работает") : (driverState === "reboot" ? qsTr("нужна перезагрузка") : qsTr("не установлен"))
                }
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 13
                text: qsTr("Нужен, чтобы рисовать в играх. Это сторонний драйвер: мы его не разрабатываем и не отвечаем за его работу и ошибки.")
            }
            Label {
                objectName: "driverConflicts"
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.danger; font.pixelSize: 13
                text: qsTr("Пока драйвер установлен, не запускаются игры с античитами EasyAntiCheat (Fortnite, Rust, Apex Legends), Riot Vanguard (Valorant), EA (Battlefield) и FACEIT. Перед такими играми удалите драйвер, а после — установите снова.")
            }
            Flow {
                Layout.fillWidth: true; spacing: 8
                ActionButton {
                    objectName: "helpInstallDriver"
                    visible: page.backend.inputDriver.state === "missing"
                    text: page.backend.inputDriver.can_install ? qsTr("Установить драйвер") : qsTr("Как установить")
                    primary: true
                    onClicked: page.driverInstallRequested()
                }
                ActionButton {
                    objectName: "removeDriver"
                    visible: page.backend.inputDriver.state === "ready" || page.backend.inputDriver.state === "reboot"
                    text: qsTr("Удалить драйвер")
                    onClicked: page.driverRemoveRequested()
                }
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
            Label { text: qsTr("Версия ") + page.backend.appVersion; color: Theme.muted; font.pixelSize: 12 }
        }
    }
}
