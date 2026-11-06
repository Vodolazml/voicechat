import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Window
import VoiceChat

// A stream on the whole screen in its own window; the app window itself stays as it was.
// Esc, double click or the button bring it back.
Window {
    id: full
    property int userId: 0
    property string title2: ""
    color: "black"
    flags: Qt.Window | Qt.FramelessWindowHint
    title: "VoiceChat — трансляция"

    function showFor(uid, onScreen) {
        if (!uid) return
        userId = uid
        if (onScreen) screen = onScreen
        showFullScreen()
        raise(); requestActivate()
        controls.reveal()
    }
    function leave() { close(); userId = 0 }
    onVisibleChanged: if (!visible) userId = 0
    // Watching stopped, or the person left: nothing to show any more.
    Connections {
        target: backend
        function onFocusChanged() { if (full.visible && backend.focusUserId !== full.userId) full.leave() }
    }

    VideoView {
        id: video
        anchors.fill: parent
        userId: full.visible ? full.userId : 0
    }
    Spinner { anchors.centerIn: parent; visible: !video.hasFrame; size: 40 }

    Item {
        id: controls
        anchors.fill: parent
        opacity: hideTimer.running || exitHover.hovered ? 1 : 0
        Behavior on opacity { NumberAnimation { duration: 200 } }
        function reveal() { hideTimer.restart() }
        Timer { id: hideTimer; interval: 2500 }

        Rectangle {
            visible: backend.focusVideoInfo !== ""
            anchors.left: parent.left; anchors.top: parent.top; anchors.margins: 18
            radius: 8; color: Qt.rgba(0, 0, 0, 0.6)
            width: info.implicitWidth + 18; height: info.implicitHeight + 10
            Text { id: info; anchors.centerIn: parent; text: backend.focusVideoInfo; color: "white"; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
        }
        Rectangle {
            anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 18
            height: 40; radius: 20; width: exitRow.implicitWidth + 28
            color: exitHover.hovered ? Qt.rgba(1, 1, 1, 0.22) : Qt.rgba(0, 0, 0, 0.6)
            Row {
                id: exitRow; anchors.centerIn: parent; spacing: 8
                Icon { name: "fullscreen_exit"; color: "white"; width: 20; height: 20; anchors.verticalCenter: parent.verticalCenter }
                Text { text: "Выйти из полноэкранного (Esc)"; color: "white"; font.family: Theme.font; font.pixelSize: 13; anchors.verticalCenter: parent.verticalCenter }
            }
            HoverHandler { id: exitHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: full.leave() }
        }
    }

    // Any mouse movement shows the controls for a moment; the pointer hides with them.
    MouseArea {
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton
        cursorShape: controls.opacity > 0 ? Qt.ArrowCursor : Qt.BlankCursor
        onPositionChanged: controls.reveal()
        onDoubleClicked: full.leave()
        z: -1
    }
    Shortcut { sequences: ["Esc", "F11"]; onActivated: full.leave() }
}
