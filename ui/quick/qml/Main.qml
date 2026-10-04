import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Dialogs

ApplicationWindow {
    id: root
    required property var backend
    objectName: "mainWindow"
    title: "OlegPainter v" + backend.appVersion
    width: 1280
    height: 860
    minimumWidth: 360
    minimumHeight: 360
    // The header is the title bar (like Steam); ui/quick/window_frame.py keeps the
    // native snap, resize edges, shadow and rounded corners of the frameless window.
    flags: Qt.Window | Qt.FramelessWindowHint | Qt.WindowSystemMenuHint
           | Qt.WindowMinMaxButtonsHint | Qt.WindowCloseButtonHint
           | (backend.alwaysOnTop ? Qt.WindowStaysOnTopHint : 0)
    readonly property bool maximized: visibility === Window.Maximized
    function toggleMaximized() { if (maximized) showNormal(); else showMaximized() }
    visible: true
    color: Theme.background
    font: Qt.application.font
    palette.window: Theme.background
    palette.base: Theme.input
    palette.button: Theme.raised
    palette.text: Theme.text
    palette.placeholderText: Theme.muted
    palette.windowText: Theme.text
    palette.buttonText: Theme.text
    palette.highlight: Theme.accent
    palette.highlightedText: "#211c0b"
    property int page: 0
    // First launch opens the quick start; leaving it once marks it as seen.
    Component.onCompleted: if (!backend.quickStartSeen) page = 7
    property bool showOriginal: false
    readonly property bool narrow: width < 760
    onNarrowChanged: if (!narrow) navigationDrawer.close()
    readonly property bool compact: height < 680
    readonly property var appState: backend.view
    readonly property var cfg: root.appState.settings || ({})
    readonly property bool busy: ["started", "paused", "stopping"].indexOf(root.appState.phase) >= 0
    function key(code) { return root.appState.bindings && root.appState.bindings[code] ? root.appState.bindings[code] : "" }
    function hotkey(code) { return root.appState.bindings && root.appState.bindings[code] ? "  ·  " + root.appState.bindings[code] : "" }
    function indexOfId(list, value) {
        for (let i = 0; i < list.length; i++) if (list[i].id === value) return i;
        return -1;
    }
    onClosing: function(event) { event.accepted = backend.closeApplication() }
    Binding { target: Theme; property: "dark"; value: root.backend.darkTheme }
    Popup {
        objectName: "shutdownPanel"
        parent: Overlay.overlay
        anchors.centerIn: parent
        width: Math.min(520, root.width - 48)
        padding: 24
        modal: true
        closePolicy: Popup.NoAutoClose
        visible: !!root.appState.closing
        background: Rectangle { color: Theme.surface; radius: 16; border.color: Theme.border }
        contentItem: ColumnLayout {
            spacing: 16
            Label { text: qsTr("Завершение работы"); font.pixelSize: 22; font.weight: Font.DemiBold }
            BusyIndicator { running: !root.appState.close_error; visible: running; palette.dark: Theme.accent; Layout.alignment: Qt.AlignHCenter }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                text: root.appState.close_error
                      ? qsTr("Не удалось освободить ввод: ") + root.appState.close_error
                      : root.appState.close_phase === "saving"
                        ? qsTr("Сохраняем изображение и настройки. Окно закроется автоматически.")
                        : qsTr("Останавливаем фоновые задачи и освобождаем управление мышью. Окно закроется автоматически.")
            }
            ActionButton { text: qsTr("Повторить завершение"); visible: !!root.appState.close_error; onClicked: backend.closeApplication() }
        }
    }
    onPageChanged: {
        if (page !== 7) backend.setQuickStartSeen(true)
        backend.cancelRecording()
        if (scroll.contentItem) scroll.contentItem.contentY = 0
        if (page === 2) backend.refreshPresets()
        pageReveal.restart()
    }
    // Animate only the arriving content; commands and fixed stop controls stay immediate.
    NumberAnimation { id: pageReveal; target: scroll; property: "opacity"; from: 0.35; to: 1; duration: Theme.normal; easing.type: Easing.OutCubic }

    Connections {
        target: backend
        function onOpenRequested() { sourceDialog.open() }
        function onHelpRequested() { root.page = 9 }
        function onBrushSetupRequested() { root.page = 5 }
        function onPageRequested(page) { root.page = page }
        function onImageSearchRequested() { imageSearchDialog.open() }
        function onImageArrived() { if (root.page !== 7) root.page = 0 }
    }
    BackgroundEditor {
        id: backgroundEditor; backend: root.backend
        onPageRequested: function(page) { root.page = page }
        onViewRequested: function(original) { imageViewer.openWith(original ? "original" : "result") }
    }
    ImageViewer { id: imageViewer; backend: root.backend }
    CalibrationDialog { id: calibrationDialog; backend: root.backend }
    ImageSearchDialog { id: imageSearchDialog; backend: root.backend }
    // IMAGE-SOURCE-001: files from Explorer and pictures dragged out of a browser.
    DropArea {
        id: imageDrop
        objectName: "imageDrop"
        parent: Overlay.overlay
        anchors.fill: parent
        enabled: root.appState.can_edit
        keys: ["text/uri-list", "text/html", "text/plain"]
        onDropped: function(drop) {
            drop.accept(Qt.CopyAction)
            root.backend.dropImage(drop.hasUrls ? drop.urls : [], drop.hasHtml ? drop.html : "", drop.hasText ? drop.text : "")
        }
        Rectangle {
            anchors.fill: parent; anchors.margins: 12
            visible: imageDrop.containsDrag
            radius: 16; color: Theme.accentSoft; border.color: Theme.accent; border.width: 2
            Label {
                anchors.centerIn: parent; width: parent.width - 48
                horizontalAlignment: Text.AlignHCenter; wrapMode: Text.WordWrap
                text: qsTr("Отпустите, чтобы открыть картинку"); font.pixelSize: 20; font.weight: Font.DemiBold; color: Theme.text
            }
        }
    }
    FileDialog {
        id: sourceDialog
        title: qsTr("Открыть изображение")
        nameFilters: [qsTr("Изображения (*.png *.jpg *.jpeg *.bmp *.webp *.gif)"), qsTr("Все файлы (*)")]
        onAccepted: backend.openSource(selectedFile)
    }
    FileDialog {
        id: importProfileDialog
        title: qsTr("Импортировать профиль")
        nameFilters: [qsTr("Профиль OlegPainter (*.json)")]
        onAccepted: backend.importPreset(selectedFile)
    }
    FileDialog {
        id: exportProfileDialog
        property string slug: ""
        title: qsTr("Экспортировать профиль")
        nameFilters: [qsTr("Профиль OlegPainter (*.json)")]
        defaultSuffix: "json"
        fileMode: FileDialog.SaveFile
        onAccepted: backend.exportPreset(slug, selectedFile)
    }
    SurfaceDialog {
        id: resetHotkeys
        anchors.centerIn: parent
        modal: true
        title: qsTr("Сбросить все горячие клавиши?")
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: backend.resetAllHotkeys()
        Label { text: qsTr("Все сочетания вернутся к исходным.") }
    }
    SurfaceDialog {
        id: whatsNewDialog
        objectName: "whatsNewDialog"
        anchors.centerIn: parent
        width: Math.min(560, root.width - 40)
        modal: true
        title: qsTr("Что нового в версии %1").arg(root.backend.appVersion)
        standardButtons: Dialog.Ok
        onClosed: root.backend.dismissWhatsNew()
        ColumnLayout {
            width: parent.width; spacing: 8
            Repeater {
                model: root.backend.whatsNew
                Label { required property string modelData; text: "• " + modelData; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            }
        }
        Component.onCompleted: if (root.backend.whatsNewDue) open()
    }
    SurfaceDialog {
        id: overwritePreset
        property string slug: ""
        property string name: ""
        anchors.centerIn: parent
        modal: true
        title: qsTr("Перезаписать профиль?")
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: backend.preset("overwrite", slug)
        Label { text: qsTr("В профиль «%1» запишутся текущие настройки тех же разделов.").arg(overwritePreset.name); wrapMode: Text.WordWrap; width: 320 }
    }
    SurfaceDialog {
        id: deletePreset
        property string slug: ""
        anchors.centerIn: parent
        modal: true
        title: qsTr("Удалить профиль?")
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: backend.preset("delete", slug)
        Label { text: qsTr("Сохранённый профиль будет удалён.") }
    }
    // Interception is a third-party kernel driver: install or remove it only after an informed yes.
    SurfaceDialog {
        id: driverConsent
        objectName: "driverConsent"
        property string command: "install"
        anchors.centerIn: parent
        width: Math.min(560, root.width - 40)
        modal: true
        title: command === "uninstall" ? qsTr("Удалить драйвер Interception?") : qsTr("Установить драйвер Interception?")
        standardButtons: Dialog.Yes | Dialog.No
        onAccepted: root.backend.driverAction(command)
        ColumnLayout {
            width: parent.width; spacing: 8
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                text: qsTr("Interception — сторонний драйвер ввода (автор — Francisco Lopes). Он не входит в OlegPainter: мы его не разрабатываем и не отвечаем за его работу и ошибки.")
            }
            Label {
                visible: driverConsent.command !== "uninstall"
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.danger
                text: qsTr("У драйвера бывают серьёзные сбои: после переподключения мыши или клавиатуры ввод может пропасть до перезагрузки, а в редких случаях Windows приходится восстанавливать. Устанавливайте его, только если согласны с этим риском.")
            }
            Label {
                visible: driverConsent.command !== "uninstall"
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                text: qsTr("Пока он установлен, не запускаются игры с античитами EasyAntiCheat, Riot Vanguard, EA (Battlefield) и FACEIT — перед ними драйвер нужно удалять.")
            }
            Label {
                Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted
                text: driverConsent.command === "uninstall"
                      ? qsTr("Запустится официальный установщик с командой удаления. Нужны права администратора и перезагрузка. Без драйвера программа не сможет рисовать.")
                      : (root.backend.inputDriver.can_install
                         ? qsTr("Запустится официальный установщик автора. Windows спросит права администратора, после установки нужна перезагрузка. Удалить драйвер можно на странице «Помощь».")
                         : qsTr("Откроется страница автора: скачайте Interception.zip и следуйте инструкции. После установки нужна перезагрузка."))
            }
        }
    }
    header: Rectangle {
        objectName: "titleBar"
        height: root.compact ? 44 : 48; color: Theme.sidebar
        Rectangle { anchors.bottom: parent.bottom; height: 1; width: parent.width; color: Theme.border }
        // Empty header space moves the window (Windows snap included); double click maximizes.
        DragHandler { target: null; onActiveChanged: if (active) root.startSystemMove() }
        TapHandler { acceptedButtons: Qt.LeftButton; onDoubleTapped: root.toggleMaximized() }
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 8; anchors.rightMargin: 0; spacing: 6
            // The app icon is the menu control, as in modern desktop apps: it collapses or expands
            // the sidebar (on a narrow window it opens the menu); hovering shows where it will go.
            Button {
                id: brandToggle
                objectName: "sidebarToggle"
                readonly property bool opensMenu: root.narrow ? !navigationDrawer.opened : root.backend.sidebarCollapsed
                readonly property string hint: root.narrow ? (navigationDrawer.opened ? qsTr("Закрыть меню") : qsTr("Открыть меню"))
                                                           : root.backend.sidebarCollapsed ? qsTr("Развернуть меню") : qsTr("Свернуть меню")
                hoverEnabled: true
                padding: 5; leftPadding: 6; rightPadding: 2
                Layout.preferredHeight: 36
                Accessible.name: hint
                ToolTip.visible: hovered; ToolTip.text: hint; ToolTip.delay: 500
                onClicked: root.narrow ? (navigationDrawer.opened ? navigationDrawer.close() : navigationDrawer.open()) : root.backend.setSidebarCollapsed(!root.backend.sidebarCollapsed)
                background: Rectangle {
                    radius: 10
                    color: brandToggle.down ? Theme.input : brandToggle.hovered ? Theme.raised : "transparent"
                    border.color: brandToggle.visualFocus ? Theme.accent : "transparent"
                    border.width: brandToggle.visualFocus ? 2 : 1
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                }
                contentItem: RowLayout {
                    spacing: 0
                    Image {
                        objectName: "appIcon"; source: root.backend.appIconUrl
                        Layout.preferredWidth: 26; Layout.preferredHeight: 26
                        sourceSize.width: Math.ceil(26 * Screen.devicePixelRatio); sourceSize.height: Math.ceil(26 * Screen.devicePixelRatio)
                        fillMode: Image.PreserveAspectFit; smooth: true; mipmap: true
                        scale: brandToggle.down ? 0.94 : 1
                        Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
                    }
                    // Space is always reserved, so the title does not jump when the arrow fades in.
                    Glyph {
                        objectName: "sidebarChevron"
                        name: "chevron-down"; color: Theme.text
                        rotation: brandToggle.opensMenu ? -90 : 90
                        Layout.preferredWidth: 14; Layout.preferredHeight: 14
                        opacity: brandToggle.hovered || brandToggle.visualFocus ? 1 : 0
                        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                        Behavior on rotation { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
                    }
                }
            }
            Label { text: "OlegPainter"; visible: root.width >= 520; font.pixelSize: 16; font.weight: Font.DemiBold; font.letterSpacing: -0.3; Layout.minimumWidth: 0; elide: Text.ElideRight }
            Label {
                objectName: "appVersion"
                visible: root.width >= 520
                text: "v" + root.backend.appVersion
                font.pixelSize: 11; font.weight: Font.DemiBold
                color: Theme.accentText
                leftPadding: 7; rightPadding: 7; topPadding: 2; bottomPadding: 2
                background: Rectangle { radius: height / 2; color: Theme.accentSoft; border.color: Theme.accentBorder }
                Layout.alignment: Qt.AlignVCenter
            }
            Item { visible: root.narrow && root.width >= 520; Layout.fillWidth: true }
            Item { visible: root.narrow && root.width < 520; Layout.fillWidth: true }
            Item { visible: !root.narrow; Layout.fillWidth: true }
            ActionButton {
                objectName: "pinWindow"; iconName: "pin"; subtle: true
                glyphColor: root.backend.alwaysOnTop ? Theme.accent : Theme.muted
                Layout.preferredWidth: 36
                hint: root.backend.alwaysOnTop ? qsTr("Поверх всех окон — включено") : qsTr("Закрепить поверх всех окон")
                onClicked: root.backend.setAlwaysOnTop(!root.backend.alwaysOnTop)
            }
            ActionButton {
                objectName: "desktopToolsButton"; iconName: "panel"; text: root.narrow ? "" : qsTr("Экранные инструменты"); hint: qsTr("HUD и трафарет"); subtle: true
                Layout.preferredWidth: root.narrow ? 36 : implicitWidth
                onClicked: desktopTools.open()
                AppMenu {
                    id: desktopTools; y: parent.height + 4
                    x: parent.width - width
                    AppMenuItem { text: qsTr("Показать / скрыть HUD"); shortcutText: root.key("toggle_overlay"); onTriggered: backend.action("toggle_overlay") }
                    AppMenuItem { text: qsTr("Редактировать HUD"); shortcutText: root.key("edit_overlay"); enabled: root.appState.can_edit; onTriggered: backend.action("edit_overlay") }
                    MenuSeparator { contentItem: Rectangle { implicitHeight: 1; color: Theme.border } }
                    AppMenuItem { text: qsTr("Показать / скрыть трафарет"); shortcutText: root.key("toggle_stencil"); enabled: root.appState.can_edit; onTriggered: backend.action("toggle_stencil") }
                    AppMenuItem { text: qsTr("Редактировать трафарет"); shortcutText: root.key("edit_stencil"); enabled: root.appState.can_edit; onTriggered: backend.action("edit_stencil") }
                }
            }
            Item { Layout.preferredWidth: 4 }
            CaptionButton { objectName: "minimizeWindow"; glyph: "\uE921"; hint: qsTr("Свернуть"); Layout.fillHeight: true; onClicked: root.showMinimized() }
            CaptionButton { objectName: "maximizeWindow"; glyph: root.maximized ? "\uE923" : "\uE922"; hint: root.maximized ? qsTr("Свернуть в окно") : qsTr("Развернуть"); Layout.fillHeight: true; onClicked: root.toggleMaximized() }
            CaptionButton { objectName: "closeWindow"; glyph: "\uE8BB"; closeButton: true; hint: qsTr("Закрыть"); Layout.fillHeight: true; onClicked: root.close() }
        }
    }
    Drawer {
        id: navigationDrawer; objectName: "navigationDrawer"
        width: Math.min(240, root.width - 48); height: root.height - root.header.height; y: root.header.height
        padding: 0; modal: true; interactive: root.narrow
        NavigationPanel { anchors.fill: parent; backend: root.backend; currentPage: root.page; onSelected: function(page) { root.page = page; navigationDrawer.close() } }
    }
    RowLayout {
        anchors.fill: parent; spacing: 0
        NavigationPanel {
            objectName: "sidebar"; visible: !root.narrow
            property real panelWidth: collapsed ? 64 : 216
            Behavior on panelWidth { NumberAnimation { duration: Theme.slow; easing.type: Easing.OutCubic } }
            Layout.preferredWidth: panelWidth; Layout.fillHeight: true
            backend: root.backend; currentPage: root.page; collapsed: root.backend.sidebarCollapsed
            onSelected: function(page) { root.page = page }
        }
        ColumnLayout {
            Layout.fillHeight: true; Layout.fillWidth: true
            Layout.margins: root.narrow ? 12 : root.compact ? 16 : 24
            spacing: root.compact ? 10 : 14
            RowLayout {
                Layout.fillWidth: true
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 4
                    Label { text: [qsTr("Рисование"), qsTr("Настройки"), qsTr("Профили"), qsTr("Горячие клавиши"), qsTr("Палитра"), qsTr("Кисть"), qsTr("Слои и действия"), qsTr("Быстрый старт"), qsTr("AI"), qsTr("Помощь")][root.page]; font.pixelSize: root.compact ? 22 : Theme.titleSize; font.weight: Font.DemiBold; font.letterSpacing: -0.5; Layout.fillWidth: true; elide: Text.ElideRight }
                    Label {
                        visible: !root.compact; Layout.fillWidth: true; elide: Text.ElideRight; font.pixelSize: 13; color: Theme.muted
                        text: [qsTr("Подготовьте изображение — и перенесите его на холст."), qsTr("Параметры подготовки и рисования."), qsTr("Ваши сохранённые рабочие настройки."), qsTr("Управление программой, даже когда она не в фокусе."), qsTr("Цвета вашей программы и их смешивание."), qsTr("Настройте размер и обучите кисть."), qsTr("Порядок слоёв и действия при выборе цвета."), qsTr("Несколько шагов — и можно рисовать в игре или программе."), qsTr("Локальные нейросети: фон, объекты, глубина, контуры."), qsTr("Видео, ответы и связь с автором.")][root.page]
                    }
                }
            }
            // DRIVER-001: without Interception nothing can be drawn; say so before setup starts.
            Rectangle {
                objectName: "driverBanner"
                visible: !root.backend.inputDriver.ready
                Layout.fillWidth: true
                implicitHeight: driverRow.implicitHeight + 20
                radius: 12; color: Theme.dangerSurface; border.color: Theme.danger
                RowLayout {
                    id: driverRow
                    anchors.fill: parent; anchors.margins: 10; spacing: 10
                    Label {
                        Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.text
                        text: root.backend.inputDriver.state === "reboot"
                              ? qsTr("Драйвер управления мышью установлен, но ещё не работает. Перезагрузите компьютер.")
                              : qsTr("Не установлен драйвер управления мышью (Interception). Настраивать можно, но рисовать программа не сможет.")
                    }
                    ActionButton {
                        objectName: "installDriver"
                        visible: root.backend.inputDriver.state !== "reboot"
                        text: root.backend.inputDriver.can_install ? qsTr("Установить драйвер") : qsTr("Как установить")
                        primary: true
                        onClicked: { driverConsent.command = "install"; driverConsent.open() }
                    }
                    ActionButton { objectName: "recheckDriver"; text: qsTr("Проверить снова"); subtle: true; onClicked: root.backend.driverAction("recheck") }
                }
            }
            RowLayout {
            Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 40
            spacing: 14
            ScrollView {
                id: scroll; objectName: "pagesScroll"
                Layout.fillWidth: true; Layout.fillHeight: true; Layout.minimumHeight: 40
                clip: true; contentWidth: availableWidth
                ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
                ColumnLayout {
                    width: scroll.availableWidth; spacing: 14
                    DrawingWorkspace {
                        visible: root.page === 0; Layout.fillWidth: true
                        backend: root.backend; showOriginal: root.showOriginal; availableHeight: scroll.availableHeight
                        onOriginalSelected: function(original) { root.showOriginal = original }
                        onCalibrationRequested: calibrationDialog.open()
                        onBackgroundRequested: backgroundEditor.open()
                        onViewRequested: function(original) { imageViewer.openWith(original ? "original" : "result") }
                        onSettingsRequested: root.page = 1
                        onSearchRequested: imageSearchDialog.open()
                    }
                    QuickStart { objectName: "quickStart"; visible: root.page === 7; Layout.fillWidth: true; backend: root.backend; narrow: root.narrow }
                    BrushEditor { visible: root.page === 5; Layout.fillWidth: true; backend: root.backend }
                    SequencesEditor { objectName: "sequencesEditor"; visible: root.page === 6; Layout.fillWidth: true; backend: root.backend }
                    AiPage {
                        objectName: "aiPage"; visible: root.page === 8; Layout.fillWidth: true; backend: root.backend; narrow: root.narrow
                        onProcessingRequested: { root.page = 0; backgroundEditor.open() }
                    }
                    HelpPage {
                        objectName: "helpPage"; visible: root.page === 9; Layout.fillWidth: true; backend: root.backend; narrow: root.narrow
                        onPageRequested: function(page) { root.page = page }
                        onDriverInstallRequested: { driverConsent.command = "install"; driverConsent.open() }
                        onDriverRemoveRequested: { driverConsent.command = "uninstall"; driverConsent.open() }
                    }
                    ColumnLayout {
                        visible: root.page === 1
                        Layout.fillWidth: true
                        spacing: 18
                        PreviewPanel {
                            objectName: "settingsPreviewInline"
                            visible: root.width < 1100
                            Layout.fillWidth: true; Layout.preferredHeight: 300
                            backend: root.backend
                            onViewRequested: function(original) { imageViewer.openWith(original ? "original" : "result") }
                        }
                        Card {
                            Layout.fillWidth: true
                            ColumnLayout {
                                anchors.fill: parent
                                spacing: 12
                                Label { text: qsTr("Программа и выбор цвета"); font.pixelSize: 15; font.weight: Font.DemiBold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                GridLayout {
                                    Layout.fillWidth: true
                                    columns: root.narrow ? 1 : 3
                                    columnSpacing: 12; rowSpacing: 6
                                    // Wide: captions in row 0, boxes in row 1. Narrow: caption/box pairs stacked.
                                    Label { text: qsTr("Где рисуем"); color: Theme.muted; font.pixelSize: 12; Layout.row: 0; Layout.column: 0 }
                                    Label { text: qsTr("Маршрут рисования"); color: Theme.muted; font.pixelSize: 12; Layout.row: root.narrow ? 2 : 0; Layout.column: root.narrow ? 0 : 1 }
                                    Label { text: qsTr("Способ выбора цвета"); color: Theme.muted; font.pixelSize: 12; Layout.row: root.narrow ? 4 : 0; Layout.column: root.narrow ? 0 : 2 }
                                    ChoiceBox { objectName: "placeSelector"; Layout.fillWidth: true; Layout.row: 1; Layout.column: 0; model: root.appState.places || []; textRole: "label"; valueRole: "id"; currentIndex: indexOfId(model, root.appState.current_place_id); enabled: root.appState.can_edit; onActivated: backend.selectProfile(currentValue, "") }
                                    ChoiceBox { Layout.fillWidth: true; Layout.row: root.narrow ? 3 : 1; Layout.column: root.narrow ? 0 : 1; model: root.appState.algorithms || []; textRole: "label"; valueRole: "id"; currentIndex: indexOfId(model, root.appState.current_algo_code); enabled: root.appState.can_edit; onActivated: backend.selectProfile(root.appState.current_place_id, currentValue) }
                                    ChoiceBox { Layout.fillWidth: true; Layout.row: root.narrow ? 5 : 1; Layout.column: root.narrow ? 0 : 2; objectName: "methodSelector"; model: root.backend.colorMethods; textRole: "label"; valueRole: "id"; currentIndex: indexOfId(model, cfg.color_picking_method); enabled: root.appState.can_edit; onActivated: backend.setChoice("color_picking_method", currentValue) }
                                }
                                Label { text: qsTr("Калибровка на экране"); color: Theme.muted; font.pixelSize: 12; Layout.topMargin: 4 }
                                // Only the calibration of the chosen method: another one would not be used.
                                Flow {
                                    objectName: "calibrationButtons"
                                    Layout.fillWidth: true
                                    spacing: 8
                                    readonly property string method: root.cfg.color_picking_method || ""
                                    ActionButton { visible: parent.method === "hex_field"; text: qsTr("Указать поле HEX"); shortcutText: root.key("capture_hex_palette"); enabled: root.appState.can_edit; onClicked: backend.action("capture_hex_palette") }
                                    ActionButton { visible: parent.method === "hsv_palette"; text: qsTr("Указать круг"); enabled: root.appState.can_edit; onClicked: backend.action("calibrate_color_circle") }
                                    ActionButton { visible: parent.method === "hsv_palette"; text: qsTr("Указать яркость"); enabled: root.appState.can_edit; onClicked: backend.action("calibrate_brightness_slider") }
                                    ActionButton { visible: parent.method === "manual_palette"; text: qsTr("Открыть палитру"); onClicked: root.page = 4 }
                                    ActionButton { visible: parent.method === "screen_palette"; text: qsTr("Обвести палитру"); enabled: root.appState.can_edit; onClicked: backend.action("calibrate_screen_palette") }
                                    ActionButton { visible: parent.method === "wheel_square"; text: qsTr("Обвести колесо"); enabled: root.appState.can_edit; onClicked: backend.action("calibrate_wheel_square") }
                                    ActionButton { objectName: "showCalibration"; text: qsTr("Показать калибровку"); iconName: "crop"; subtle: true; hint: qsTr("Показать на экране, куда программа будет щёлкать"); enabled: root.appState.can_edit; onClicked: backend.showCalibration() }
                                }
                                // «Контур + заливка» needs to know how the program switches pen and bucket.
                                ColumnLayout {
                                    id: outlineTools; objectName: "outlineFillTools"
                                    visible: root.appState.current_algo_code === "outline_and_fill"
                                    Layout.fillWidth: true; Layout.topMargin: 6; spacing: 8
                                    readonly property var tools: root.appState.outline_fill || ({})
                                    Label { text: qsTr("Как программа переключает кисть и заливку"); color: Theme.muted; font.pixelSize: 12 }
                                    ChoiceBox {
                                        objectName: "outlineFillMode"; Layout.fillWidth: true; enabled: root.appState.can_edit
                                        model: [{id: "keys", label: qsTr("Клавишами")}, {id: "coords", label: qsTr("Щелчком по кнопкам инструментов")}]
                                        textRole: "label"; valueRole: "id"
                                        currentIndex: outlineTools.tools.mode === "coords" ? 1 : 0
                                        onActivated: backend.setOutlineFill({mode: currentValue})
                                    }
                                    RowLayout {
                                        visible: outlineTools.tools.mode !== "coords"; spacing: 8
                                        Label { text: qsTr("Кисть"); color: Theme.muted }
                                        InputField { objectName: "outlineBrushKey"; Layout.preferredWidth: 90; text: outlineTools.tools.brush_key || ""; enabled: root.appState.can_edit; onEditingFinished: backend.setOutlineFill({brush_key: text}) }
                                        Label { text: qsTr("Заливка"); color: Theme.muted }
                                        InputField { objectName: "outlineFillKey"; Layout.preferredWidth: 90; text: outlineTools.tools.fill_key || ""; enabled: root.appState.can_edit; onEditingFinished: backend.setOutlineFill({fill_key: text}) }
                                    }
                                    RowLayout {
                                        visible: outlineTools.tools.mode === "coords"; spacing: 8
                                        ActionButton { objectName: "captureOutlineTools"; text: qsTr("Указать кнопки кисти и заливки"); enabled: root.appState.can_edit; onClicked: backend.captureOutlineFillTools() }
                                        Label {
                                            Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
                                            text: outlineTools.tools.brush_coord && outlineTools.tools.fill_coord ? qsTr("Кнопки указаны") : qsTr("Сначала щёлкните кнопку кисти, затем кнопку заливки (ведро)")
                                        }
                                    }
                                    Label {
                                        Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 12
                                        text: qsTr("Без этой настройки программа не сможет взять ведро и дорисует области кистью — медленнее. В Paint укажите кнопки инструментов.")
                                    }
                                }
                            }
                        }
                        SettingsEditor {
                            Layout.fillWidth: true
                            backend: root.backend
                            values: root.cfg
                        }
                    }
                    PaletteEditor {
                        objectName: "paletteEditor"
                        visible: root.page === 4
                        Layout.fillWidth: true
                        backend: root.backend
                    }
                    ColumnLayout {
                        visible: root.page === 2
                        Layout.fillWidth: true
                        spacing: 12
                        Card {
                            Layout.fillWidth: true
                            ColumnLayout {
                                anchors.fill: parent; spacing: 12
                                Label { text: qsTr("Новый профиль"); font.pixelSize: 15; font.weight: Font.DemiBold }
                                GridLayout {
                                    columns: root.narrow ? 1 : 2
                                    Layout.fillWidth: true
                                    columnSpacing: 8; rowSpacing: 8
                                    InputField { id: presetName; objectName: "presetName"; Layout.fillWidth: true; placeholderText: qsTr("Имя нового профиля"); enabled: root.appState.can_edit }
                                    ActionButton {
                                        objectName: "savePreset"
                                        text: qsTr("Сохранить текущие настройки"); Layout.fillWidth: root.narrow; primary: true
                                        enabled: root.appState.can_edit && presetName.text.trim() !== "" && profileParts.chosen.length > 0
                                        onClicked: if (backend.savePreset(presetName.text, profileParts.chosen)) presetName.clear()
                                    }
                                }
                                Label { text: qsTr("Что сохранить. Клавиши и картинку обычно не передают другим: у каждого свои."); color: Theme.muted; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                                Flow {
                                    id: profileParts
                                    objectName: "profileParts"
                                    Layout.fillWidth: true; spacing: 4
                                    property var chosen: []
                                    Component.onCompleted: chosen = (backend.profileCategories || []).filter(c => c.default).map(c => c.id)
                                    Repeater {
                                        model: backend.profileCategories
                                        ToggleSwitch {
                                            required property var modelData
                                            objectName: "profilePart_" + modelData.id
                                            width: root.narrow ? profileParts.width : Math.min(implicitWidth, profileParts.width)
                                            text: modelData.label
                                            ToolTip.visible: hovered; ToolTip.text: modelData.detail; ToolTip.delay: 500
                                            checked: profileParts.chosen.indexOf(modelData.id) >= 0
                                            onToggled: profileParts.chosen = checked ? profileParts.chosen.concat([modelData.id])
                                                                                     : profileParts.chosen.filter(id => id !== modelData.id)
                                        }
                                    }
                                }
                                Flow {
                                    Layout.fillWidth: true; spacing: 8
                                    ActionButton { text: qsTr("Импортировать профиль"); width: Math.min(implicitWidth, parent.width); enabled: root.appState.can_edit; onClicked: importProfileDialog.open() }
                                    ActionButton { objectName: "openProfilesFolder"; iconName: "folder-open"; text: qsTr("Открыть папку"); width: Math.min(implicitWidth, parent.width); subtle: true; onClicked: backend.openProfilesFolder() }
                                }
                            }
                        }
                        Card {
                            Layout.fillWidth: true
                            padding: 6
                            ColumnLayout {
                                anchors.fill: parent; spacing: 0
                                Label { visible: (root.appState.presets || []).length === 0; Layout.fillWidth: true; Layout.margins: 10; wrapMode: Text.WordWrap; text: qsTr("Сохранённых профилей пока нет."); color: Theme.muted }
                                Repeater {
                                    model: root.appState.presets || []
                                    delegate: ColumnLayout {
                                        required property var modelData
                                        required property int index
                                        Layout.fillWidth: true; spacing: 0
                                        Rectangle { visible: index > 0; Layout.fillWidth: true; Layout.leftMargin: 10; Layout.rightMargin: 10; implicitHeight: 1; color: Theme.border }
                                        GridLayout {
                                            columns: root.narrow ? 4 : 5
                                            Layout.fillWidth: true; Layout.margins: 10
                                            columnSpacing: 6
                                            ColumnLayout {
                                                Layout.fillWidth: true; Layout.columnSpan: root.narrow ? 4 : 1; spacing: 2
                                                Label { text: modelData.name; Layout.fillWidth: true; wrapMode: Text.WordWrap; font.weight: Font.Medium }
                                                Label { visible: !!modelData.categories; text: qsTr("Изменит: ") + modelData.categories; Layout.fillWidth: true; wrapMode: Text.WordWrap; color: Theme.muted; font.pixelSize: 11 }
                                            }
                                            ActionButton { text: qsTr("Применить"); enabled: root.appState.can_edit; onClicked: backend.preset("apply", modelData.slug) }
                                            ActionButton { text: qsTr("Перезаписать"); subtle: true; hint: qsTr("Сохранить в этот профиль текущие настройки"); enabled: root.appState.can_edit; onClicked: { overwritePreset.slug = modelData.slug; overwritePreset.name = modelData.name; overwritePreset.open() } }
                                            ActionButton { text: qsTr("Экспорт"); subtle: true; onClicked: { exportProfileDialog.slug = modelData.slug; exportProfileDialog.open() } }
                                            ActionButton { text: qsTr("Удалить"); subtle: true; danger: true; enabled: root.appState.can_edit; onClicked: { deletePreset.slug = modelData.slug; deletePreset.open() } }
                                        }
                                    }
                                }
                            }
                        }
                    }
                    ColumnLayout {
                        visible: root.page === 3
                        Layout.fillWidth: true
                        spacing: 12
                        Label { visible: !!backend.recordingCode; text: qsTr("Нажмите сочетание клавиш. Esc — отмена."); color: Theme.accentText; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                        ActionButton { objectName: "resetAllHotkeys"; text: qsTr("Сбросить все"); subtle: true; enabled: !busy && !backend.recordingCode; onClicked: resetHotkeys.open() }
                        Card {
                            Layout.fillWidth: true
                            padding: 6
                            ColumnLayout {
                                anchors.fill: parent; spacing: 0
                                Repeater {
                                    objectName: "hotkeyRows"
                                    model: root.appState.hotkeys || []
                                    delegate: ColumnLayout {
                                        required property var modelData
                                        required property int index
                                        Layout.fillWidth: true; spacing: 0
                                        Rectangle { visible: index > 0; Layout.fillWidth: true; Layout.leftMargin: 10; Layout.rightMargin: 10; implicitHeight: 1; color: Theme.border }
                                        GridLayout {
                                            columns: root.narrow ? 3 : 5
                                            Layout.fillWidth: true; Layout.leftMargin: 10; Layout.rightMargin: 6; Layout.topMargin: 6; Layout.bottomMargin: 6
                                            columnSpacing: 6
                                            Label { text: modelData.label; Layout.fillWidth: true; Layout.columnSpan: root.narrow ? 3 : 1; wrapMode: Text.WordWrap }
                                            Label { visible: !root.narrow; text: modelData.scope === "global" ? qsTr("Глобально") : qsTr("В окне"); color: Theme.muted; font.pixelSize: 12; Layout.preferredWidth: 80 }
                                            ActionButton { Layout.preferredWidth: 155; text: backend.recordingCode === modelData.code ? qsTr("Нажмите клавиши…") : modelData.sequence || qsTr("Отключено"); primary: backend.recordingCode === modelData.code; enabled: !busy && (backend.recordingCode === "" || backend.recordingCode === modelData.code); onClicked: backend.beginRecording(modelData.code) }
                                            ActionButton { text: qsTr("Сброс"); subtle: true; enabled: !busy && !backend.recordingCode; onClicked: backend.changeHotkey(modelData.code, "default") }
                                            ActionButton { text: "×"; subtle: true; hint: qsTr("Отключить"); enabled: !busy && !backend.recordingCode; onClicked: backend.changeHotkey(modelData.code, "") }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
            // SETTINGS-PREVIEW-001: the picture beside the settings that change it.
            PreviewPanel {
                visible: root.page === 1 && root.width >= 1100
                Layout.preferredWidth: Math.min(480, Math.max(320, root.width * 0.3))
                Layout.fillHeight: true
                backend: root.backend
                onViewRequested: function(original) { imageViewer.openWith(original ? "original" : "result") }
            }
            }
            Card {
                visible: !!root.appState.brush_learning && !!root.appState.brush_learning.active
                Layout.fillWidth: true; padding: 10
                RowLayout {
                    anchors.fill: parent; spacing: 8
                    Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight; text: root.appState.brush_learning ? root.appState.brush_learning.message : "" }
                    ActionButton { objectName: "brushCancel"; iconName: "stop"; danger: true; text: qsTr("Остановить"); enabled: root.appState.brush_learning && !root.appState.brush_learning.cancelling; onClicked: backend.brushCommand("cancel") }
                }
            }
            Card {
                objectName: "drawingControls"
                elevated: true
                visible: root.page === 0 || busy || !!root.appState.capture.active || !!root.appState.desktop_mode
                Layout.fillWidth: true; padding: root.compact ? 10 : 14
                ColumnLayout {
                    anchors.fill: parent; spacing: 8
                    RowLayout {
                        Layout.fillWidth: true
                        Rectangle { width: 6; height: 6; radius: 3; color: root.appState.can_start || busy ? Theme.accent : Theme.muted }
                        Label { text: busy ? (root.appState.phase === "paused" ? qsTr("На паузе") : root.appState.phase === "stopping" ? qsTr("Остановка…") : qsTr("Рисование")) : root.appState.phase === "completed" ? qsTr("Рисунок завершён") : root.appState.can_start ? qsTr("Готово к рисованию") : qsTr("Подготовка"); font.weight: Font.DemiBold; Layout.fillWidth: true }
                        Label { text: root.appState.current_place_label || ""; visible: !root.narrow; color: Theme.muted; font.pixelSize: 12 }
                    }
                    Label { text: root.appState.next_step.detail; visible: !busy && text !== "" && root.height >= 480; wrapMode: Text.WordWrap; maximumLineCount: 2; elide: Text.ElideRight; Layout.fillWidth: true; color: Theme.muted; font.pixelSize: 12 }
                    RowLayout {
                        visible: busy; Layout.fillWidth: true
                        ProgressBar {
                            id: drawingProgress
                            Layout.fillWidth: true; from: 0; to: 100; value: backend.stats.percent || 0
                            background: Rectangle { implicitHeight: 6; radius: 3; color: Theme.input }
                            contentItem: Item {
                                implicitHeight: 6
                                Rectangle {
                                    width: drawingProgress.visualPosition * parent.width; height: parent.height; radius: 3; color: Theme.accent
                                    Behavior on width { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
                                }
                            }
                        }
                        Label { text: (backend.stats.percent || 0) + "%"; Layout.preferredWidth: 40 }
                        Label { visible: !root.narrow; text: backend.stats.eta_text || ""; color: Theme.muted }
                    }
                    RowLayout {
                        Layout.fillWidth: true; spacing: 8
                        ActionButton { objectName: "nextStepButton"; text: root.appState.next_step.label; hint: text; iconName: "play"; visible: !busy && !!root.appState.next_step.action; primary: true; Layout.fillWidth: true; onClicked: backend.nextStep() }
                        ActionButton { objectName: "startButton"; visible: busy || !root.appState.next_step.action; Layout.fillWidth: true; primary: true; iconName: root.appState.phase === "started" ? "pause" : "play"; text: root.appState.phase === "started" ? qsTr("Пауза") : root.appState.phase === "paused" ? qsTr("Продолжить") : qsTr("Начать рисование"); shortcutText: root.narrow ? "" : root.appState.bindings.start_pause || ""; enabled: root.appState.can_start || root.appState.phase === "started" || root.appState.phase === "paused"; onClicked: backend.action("start_pause") }
                        ActionButton { objectName: "stopButton"; iconName: "stop"; text: qsTr("Стоп"); shortcutText: root.narrow ? "" : root.appState.bindings.stop || ""; danger: enabled; hint: qsTr("Остановить"); enabled: (busy || root.appState.capture.active || !!root.appState.desktop_mode) && root.appState.phase !== "stopping"; onClicked: backend.action("stop") }
                    }
                }
            }
            // A picture copied in the browser after «Найти картинку»: one click to take it.
            Rectangle {
                objectName: "clipboardOffer"
                visible: root.backend.clipboardOffer && root.appState.can_edit
                Layout.fillWidth: true
                implicitHeight: offerRow.implicitHeight + 16
                radius: 10; color: Theme.accentSoft; border.color: Theme.accent
                RowLayout {
                    id: offerRow
                    anchors.fill: parent; anchors.margins: 8; anchors.leftMargin: 12; spacing: 8
                    Label { Layout.fillWidth: true; wrapMode: Text.WordWrap; text: qsTr("Вы скопировали картинку. Вставить её в OlegPainter?") }
                    ActionButton { objectName: "acceptClipboardOffer"; iconName: "paste"; text: qsTr("Вставить"); primary: true; onClicked: root.backend.acceptClipboardOffer() }
                    ActionButton { objectName: "dismissClipboardOffer"; text: "×"; subtle: true; hint: qsTr("Не вставлять"); onClicked: root.backend.dismissClipboardOffer() }
                }
            }
            // Problems get a visible red card with a close button; notes stay a quiet line.
            Rectangle {
                objectName: "statusBar"
                Layout.fillWidth: true
                visible: backend.message !== "" && (root.height >= 480 || backend.messageError)
                implicitHeight: statusRow.implicitHeight + (backend.messageError ? 12 : 0)
                radius: 10
                color: backend.messageError ? Theme.dangerSurface : "transparent"
                border.color: backend.messageError ? Theme.danger : "transparent"
                RowLayout {
                    id: statusRow
                    anchors.fill: parent; anchors.leftMargin: backend.messageError ? 12 : 0; anchors.rightMargin: backend.messageError ? 4 : 0
                    spacing: 6
                    Label {
                        objectName: "statusMessage"; Layout.fillWidth: true
                        text: backend.message
                        maximumLineCount: backend.messageError ? (root.narrow ? 3 : 4) : (root.narrow ? 1 : 2)
                        wrapMode: Text.WordWrap; elide: Text.ElideRight
                        font.pixelSize: backend.messageError ? 13 : 11
                        color: backend.messageError ? Theme.text : Theme.muted
                        ToolTip.visible: statusHover.hovered && truncated; ToolTip.text: text
                        HoverHandler { id: statusHover }
                    }
                    ActionButton {
                        objectName: "dismissMessage"; visible: backend.messageError
                        text: "×"; subtle: true; hint: qsTr("Скрыть сообщение")
                        onClicked: backend.dismissMessage()
                    }
                }
            }
        }
    }
}
