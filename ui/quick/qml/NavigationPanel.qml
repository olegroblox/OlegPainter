import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

Rectangle {
    id: navigation
    required property var backend
    property int currentPage: 0
    property bool collapsed: false
    signal selected(int page)
    signal helpRequested()
    color: Theme.sidebar
    clip: true
    Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Theme.border }

    // One row style for pages and the utility items at the bottom.
    component NavButton: Button {
        id: nav
        property string iconName: ""
        property string title: ""
        property string detail: ""
        property bool current: false
        Layout.fillWidth: true; Layout.minimumWidth: 0
        implicitHeight: 42
        leftPadding: 10; rightPadding: 8
        hoverEnabled: true
        Accessible.name: title
        ToolTip.visible: hovered && navigation.collapsed
        ToolTip.text: detail !== "" ? title + " — " + detail : title
        ToolTip.delay: 350
        background: Rectangle {
            radius: 10
            color: nav.current ? Theme.accentSoft : nav.hovered ? Theme.surface : "transparent"
            border.width: nav.visualFocus ? 1 : 0; border.color: Theme.accent
            Behavior on color { ColorAnimation { duration: Theme.fast } }
            Rectangle { visible: nav.current; x: 0; y: (parent.height - 18) / 2; width: 3; height: 18; radius: 1.5; color: Theme.accent }
        }
        contentItem: RowLayout {
            spacing: 11
            Glyph { name: nav.iconName; color: nav.current ? Theme.accentText : Theme.muted; Layout.preferredWidth: 20; Layout.preferredHeight: 20 }
            Label {
                visible: nav.width > 60
                text: nav.title; color: nav.current ? Theme.text : Theme.muted
                font.pixelSize: 13; font.weight: nav.current ? Font.DemiBold : Font.Normal
                Layout.fillWidth: true; elide: Text.ElideRight
            }
            Label { visible: nav.width > 60 && nav.detail !== ""; text: nav.detail; color: Theme.muted; font.pixelSize: 11 }
        }
    }

    ColumnLayout {
        anchors.fill: parent; anchors.margins: 12
        spacing: 4
        ScrollView {
            id: navScroll
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true; contentWidth: availableWidth
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ColumnLayout {
                width: navScroll.availableWidth; spacing: 4
                Repeater {
                    model: [ {page:7, title:qsTr("Быстрый старт"), icon:"play"}, {page:0, title:qsTr("Рисование"), icon:"canvas"},
                             {page:4, title:qsTr("Палитра"), icon:"palette"}, {page:5, title:qsTr("Кисть"), icon:"brush"},
                             {page:6, title:qsTr("Слои и действия"), icon:"layers"}, {page:8, title:qsTr("AI"), icon:"sparkles"},
                             {page:1, title:qsTr("Настройки"), icon:"gear"},
                             {page:2, title:qsTr("Профили"), icon:"profiles"}, {page:3, title:qsTr("Горячие клавиши"), icon:"keyboard"} ]
                    delegate: NavButton {
                        required property var modelData
                        objectName: "navigation_" + modelData.page
                        iconName: modelData.icon; title: modelData.title
                        current: navigation.currentPage === modelData.page
                        onClicked: navigation.selected(modelData.page)
                    }
                }
            }
        }
        Rectangle { Layout.fillWidth: true; Layout.topMargin: 6; Layout.bottomMargin: 6; implicitHeight: 1; color: Theme.border }
        NavButton {
            objectName: "themeToggle"
            iconName: navigation.backend.darkTheme ? "moon" : "sun"
            title: navigation.backend.darkTheme ? qsTr("Тёмная тема") : qsTr("Светлая тема")
            onClicked: navigation.backend.setDarkTheme(!navigation.backend.darkTheme)
        }
        NavButton {
            objectName: "languageToggle"
            iconName: "globe"
            title: navigation.backend.language === "en" ? "English" : qsTr("Русский")
            onClicked: navigation.backend.setLanguage(navigation.backend.language === "en" ? "ru" : "en")
        }
        NavButton {
            objectName: "helpButton"
            iconName: "help"; title: qsTr("Помощь")
            current: navigation.currentPage === 9
            onClicked: navigation.selected(9)
        }
    }
}
