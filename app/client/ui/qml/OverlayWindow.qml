import QtQuick
import QtQuick.Window
import QtQuick.Layouts

// Tiny always-on-top list for games: who is in the channel and who is talking. Click-through.
Window {
    id: win
    readonly property var s: backend.settings
    readonly property real k: s.overlay_scale || 1
    visible: backend.inCall && !!s.overlay_enabled
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint
    color: "transparent"
    width: 260 * k
    height: Math.max(60, column.implicitHeight + 16)
    title: "VoiceChat overlay"

    readonly property bool right: (s.overlay_corner || "").indexOf("right") >= 0
    readonly property bool bottom: (s.overlay_corner || "").indexOf("bottom") >= 0
    x: right ? Screen.width - width - 16 : 16
    y: bottom ? Screen.height - height - 60 : 16
    onVisibleChanged: if (visible) overlayHelper.clickThrough(win)

    Column {
        id: column
        x: right ? parent.width - width - 8 : 8
        y: 8
        spacing: 4 * win.k
        Repeater {
            model: backend.stageModel
            delegate: Rectangle {
                visible: !win.s.overlay_speakers_only || model.speaking
                height: visible ? 30 * win.k : 0
                width: row.implicitWidth + 16 * win.k
                radius: height / 2
                color: Qt.rgba(0.05, 0.05, 0.08, win.s.overlay_opacity === undefined ? 0.85 : win.s.overlay_opacity)
                border.width: model.speaking ? 2 : 0
                border.color: Theme.speaking
                RowLayout {
                    id: row
                    anchors.verticalCenter: parent.verticalCenter
                    x: 4 * win.k
                    spacing: 6 * win.k
                    Avatar { size: 22 * win.k; initials: model.initials; color: model.color; speaking: model.speaking }
                    Text { text: model.name; color: model.speaking ? "white" : "#c9cbe0"; font.family: Theme.font; font.pixelSize: 13 * win.k; font.weight: model.speaking ? Font.DemiBold : Font.Normal; style: Text.Outline; styleColor: Qt.rgba(0, 0, 0, 0.4) }
                    Icon { visible: model.muted || model.serverMuted; name: "mic_off"; color: Theme.bad; width: 14 * win.k; height: 14 * win.k }
                    Icon { visible: model.deafened; name: "headset_off"; color: Theme.bad; width: 14 * win.k; height: 14 * win.k }
                    Item { width: 2; height: 1 }
                }
            }
        }
    }
}
