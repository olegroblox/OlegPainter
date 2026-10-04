pragma Singleton
import QtQuick

// One source of colours for the whole QML shell. `dark` is bound from the
// presenter (saved choice); every colour below switches with it.
QtObject {
    property bool dark: true
    // Caption button glyphs: Windows 11 font, the Windows 10 one otherwise. One exact
    // family name: a comma list made Qt scan every system font (DirectWrite warnings).
    readonly property string iconFont: Qt.fontFamilies().indexOf("Segoe Fluent Icons") >= 0 ? "Segoe Fluent Icons" : "Segoe MDL2 Assets"

    readonly property color background: dark ? "#181818" : "#f3f3f2"
    readonly property color sidebar: dark ? "#212121" : "#fbfbfa"
    readonly property color surface: dark ? "#2e2e2e" : "#ffffff"
    readonly property color input: dark ? "#282828" : "#efefed"
    readonly property color raised: dark ? "#3a3a3a" : "#e6e6e3"
    readonly property color border: dark ? "#12ffffff" : "#17000000"
    readonly property color borderHover: dark ? "#24ffffff" : "#2e000000"
    readonly property color text: dark ? "#e6ffffff" : "#e6000000"
    readonly property color muted: dark ? "#8cffffff" : "#99000000"
    // Accent fills keep the brand yellow; accent used as TEXT on light surfaces
    // needs a darker tone to stay readable.
    readonly property color accent: dark ? "#ffd21e" : "#f5c400"
    readonly property color accentText: dark ? "#ffd21e" : "#9a7700"
    readonly property color accentHover: dark ? "#ffdf55" : "#ffd21e"
    readonly property color accentPressed: dark ? "#e3bb14" : "#e0b300"
    readonly property color accentSoft: dark ? "#26ffd21e" : "#33f5c400"
    readonly property color accentBorder: dark ? "#55ffd21e" : "#80d9ad00"
    readonly property color accentInk: "#201900"
    readonly property color danger: dark ? "#ff8585" : "#c93c3c"
    readonly property color dangerSurface: dark ? "#3b2626" : "#fbe7e7"
    readonly property color dangerHover: dark ? "#503030" : "#f6d4d4"
    readonly property color keycap: dark ? "#242424" : "#e3e3e0"
    // Switch and slider handles: light on dark, white with a border on light.
    readonly property color knob: dark ? "#e6ffffff" : "#ffffff"
    readonly property color knobBorder: dark ? "transparent" : "#33000000"
    readonly property color scrim: dark ? "#99000000" : "#66000000"

    // Short, finite interactions. Never animate source pixels or drawing commands.
    readonly property int fast: 120
    readonly property int normal: 180
    readonly property int slow: 220
    readonly property int cardRadius: 16
    readonly property int titleSize: 28
}
