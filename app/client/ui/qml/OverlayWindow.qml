import QtQuick
import QtQuick.Window
import QtQuick.Layouts

// Tiny always-on-top list for games: who is in the channel and who is talking. Click-through.
Window {
    id: win
    readonly property var s: backend.settings
    readonly property real k: s.overlay_scale || 1
    // A window of its own, not owned by the main one: Windows hides owned windows together with their
    // owner, so minimising VoiceChat used to take the overlay away as well.
    transientParent: null
    visible: backend.inCall && !!s.overlay_enabled
    flags: Qt.Tool | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint | Qt.WindowTransparentForInput | Qt.WindowDoesNotAcceptFocus | Qt.NoDropShadowWindowHint
    color: "transparent"
    width: 260 * k
    height: Math.max(60, column.implicitHeight + 16)
    title: "VoiceChat overlay"

    readonly property bool custom: s.overlay_corner === "custom"
    readonly property bool right: custom ? (s.overlay_x || 0) > 0.5 : (s.overlay_corner || "").indexOf("right") >= 0
    readonly property bool bottom: (s.overlay_corner || "").indexOf("bottom") >= 0
    // "Своё место": anywhere on the screen, set by two sliders (fractions of the free space).
    x: custom ? Math.round((Screen.width - width) * Math.max(0, Math.min(1, s.overlay_x || 0)))
              : (right ? Screen.width - width - 16 : 16)
    y: custom ? Math.round((Screen.height - height) * Math.max(0, Math.min(1, s.overlay_y || 0)))
              : (bottom ? Screen.height - height - 60 : 16)
    onVisibleChanged: if (visible) overlayHelper.clickThrough(win)

    // "Always on top" is only set once when the window appears; a game or another overlay that makes
    // itself topmost later lands above it. Put it back on top every second while it is shown.
    Timer {
        interval: 1000
        repeat: true
        running: win.visible
        onTriggered: overlayHelper.keepOnTop(win)
    }

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
                color: Qt.rgba(0.05, 0.05, 0.08, win.s.overlay_opacity === undefined ? 0.5 : win.s.overlay_opacity)
                border.width: model.speaking ? 2 : 0
                border.color: Theme.speaking
                RowLayout {
                    id: row
                    // Names, avatars and icons fade separately from the background.
                    opacity: win.s.overlay_text_opacity === undefined ? 1 : Math.max(0.15, win.s.overlay_text_opacity)
                    anchors.verticalCenter: parent.verticalCenter
                    x: 4 * win.k
                    spacing: 6 * win.k
                    Avatar { size: 22 * win.k; initials: model.initials; color: model.color; source: model.avatar || ""; speaking: model.speaking }
                    Text { text: model.name; color: model.speaking ? "white" : "#c9cbe0"; font.family: Theme.font; font.pixelSize: 13 * win.k; font.weight: model.speaking ? Font.DemiBold : Font.Normal; style: Text.Outline; styleColor: Qt.rgba(0, 0, 0, 0.4) }
                    Icon { visible: model.muted || model.serverMuted; name: "mic_off"; color: Theme.bad; width: 14 * win.k; height: 14 * win.k }
                    Icon { visible: model.deafened; name: "headset_off"; color: Theme.bad; width: 14 * win.k; height: 14 * win.k }
                    Item { width: 2; height: 1 }
                }
            }
        }
    }
}
