pragma Singleton
import QtQuick

QtObject {
    property color accent: "#7c5cff"
    readonly property color accent2: Qt.lighter(accent, 1.35)
    readonly property color accentSoft: Qt.rgba(accent.r, accent.g, accent.b, 0.16)
    readonly property color bg: "#0d0e14"
    readonly property color rail: "#08090d"
    readonly property color panel: "#13141c"
    readonly property color panel2: "#191a24"
    readonly property color card: "#1f2030"
    readonly property color cardHover: "#262739"
    readonly property color line: "#2a2b3d"
    readonly property color text: "#eceef7"
    readonly property color muted: "#9a9cb3"
    readonly property color faint: "#686a82"
    readonly property color good: "#2fd67b"
    readonly property color warn: "#ffb020"
    readonly property color bad: "#ff4d6a"
    readonly property color speaking: "#2fd67b"
    readonly property string font: Qt.platform.os === "windows" ? "Segoe UI Variable Text" : "Inter"
    readonly property int radius: 12
    readonly property int fast: 140
    readonly property int normal: 220
}
