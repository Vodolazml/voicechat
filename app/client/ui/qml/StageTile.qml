import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import VoiceChat

// One person on the stage: big avatar (or their screen if you watch it), name, mute state, speaking glow.
Rectangle {
    id: tile
    property var person
    property bool small: false
    signal focusRequested()
    radius: 16
    color: Theme.card
    clip: true
    border.width: person.speaking ? 3 : 1
    border.color: person.speaking ? Theme.speaking : Qt.rgba(1, 1, 1, 0.05)
    Behavior on border.color { ColorAnimation { duration: Theme.fast } }

    property color tint: person.color
    gradient: Gradient {
        GradientStop { position: 0; color: Qt.rgba(tile.tint.r, tile.tint.g, tile.tint.b, 0.22) }
        GradientStop { position: 1; color: Theme.card }
    }

    // Their camera fills the tile while they are not showing a screen you watch.
    VideoView {
        id: cam
        anchors.fill: parent
        anchors.margins: 3
        userId: tile.person.camera ? tile.person.cameraKey : 0
        visible: tile.person.camera && !tile.person.watching && hasFrame
    }

    VideoView {
        id: video
        anchors.fill: parent
        anchors.margins: 3
        userId: tile.person.watching ? tile.person.id : 0
        visible: tile.person.watching
    }

    Avatar {
        anchors.centerIn: parent
        visible: tile.person.watching ? !video.hasFrame : !cam.visible
        size: Math.min(tile.width, tile.height) * (tile.small ? 0.42 : 0.36)
        initials: tile.person.initials
        color: tile.person.color
        source: tile.person.avatar || ""
        speaking: tile.person.speaking
    }
    Spinner { anchors.centerIn: parent; visible: tile.person.watching && !video.hasFrame; size: 28 }

    // LIVE badge and "watch" button for streams
    Rectangle {
        visible: tile.person.streaming
        anchors.top: parent.top; anchors.left: parent.left; anchors.margins: 10
        radius: 5; color: Theme.bad; width: 42; height: 20
        Text { anchors.centerIn: parent; text: "LIVE"; color: "white"; font.pixelSize: 11; font.weight: Font.Black; font.family: Theme.font }
    }
    // Real resolution and frame rate, measured on the received (or, for us, the sent) stream.
    Rectangle {
        visible: tile.person.watching && video.hasFrame && tile.person.videoInfo !== ""
        anchors.top: parent.top; anchors.left: parent.left; anchors.margins: 10
        anchors.leftMargin: tile.person.streaming ? 58 : 10
        radius: 6; color: Qt.rgba(0, 0, 0, 0.6)
        width: statsCol.implicitWidth + 14; height: statsCol.implicitHeight + 6
        Column {
            id: statsCol
            anchors.centerIn: parent
            Text { text: tile.person.videoInfo; color: "white"; font.family: Theme.font; font.pixelSize: 11; font.weight: Font.DemiBold }
            Text { visible: !tile.small && tile.person.videoDetail !== ""; text: tile.person.videoDetail; color: Qt.rgba(1, 1, 1, 0.7); font.family: Theme.font; font.pixelSize: 10 }
        }
    }
    PrimaryButton {
        visible: tile.person.streaming && !tile.person.watching && !tile.person.isMe && !tile.small
        anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 10
        text: "Смотреть"
        implicitHeight: 28
        font.pixelSize: 12
        leftPadding: 14; rightPadding: 14
        onClicked: backend.watch(tile.person.id, true)
    }

    // Name plate
    Rectangle {
        anchors.left: parent.left; anchors.bottom: parent.bottom; anchors.margins: 10
        radius: 8; color: Qt.rgba(0, 0, 0, 0.55)
        width: plate.implicitWidth + 16; height: 26
        RowLayout {
            id: plate
            anchors.centerIn: parent; spacing: 6
            Icon { visible: tile.person.serverMuted || (tile.person.muted && !tile.person.deafened); name: "mic_off"; color: Theme.bad; width: 14; height: 14 }
            Icon { visible: tile.person.deafened; name: "headset_off"; color: Theme.bad; width: 14; height: 14 }
            Icon { visible: tile.person.keyChanged; name: "shield"; color: Theme.warn; width: 14; height: 14 }
            Text { text: tile.person.name + (tile.person.isMe ? " (вы)" : ""); color: "white"; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
            Text { visible: tile.person.localMuted; text: "· заглушён у вас"; color: Theme.warn; font.family: Theme.font; font.pixelSize: 11 }
        }
    }
    QualityBars {
        visible: !tile.person.isMe && tile.person.quality < 2
        anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12
        quality: tile.person.quality
    }

    Row {
        visible: hover.hovered && tile.person.watching
        anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: 10
        spacing: 4
        IconButton { glyph: "fullscreen"; tip: "Развернуть"; fill: Qt.rgba(0, 0, 0, 0.55); onClicked: tile.focusRequested() }
        IconButton { glyph: "close"; tip: tile.person.isMe ? "Остановить показ экрана" : "Перестать смотреть"; fill: Qt.rgba(0, 0, 0, 0.55); onClicked: tile.person.isMe ? backend.stopScreen() : backend.watch(tile.person.id, false) }
    }

    HoverHandler { id: hover }
    TapHandler { onDoubleTapped: if (tile.person.watching) tile.focusRequested() }
    TapHandler { acceptedButtons: Qt.RightButton; enabled: !tile.person.isMe; onTapped: personMenu.popup() }

    StyledMenu {
        id: personMenu
        property int wakeWait: 0
        onAboutToShow: wakeWait = backend.wakeWait(tile.person.id)
        MenuItem {
            visible: tile.person.canWake; height: visible ? implicitHeight : 0
            enabled: personMenu.wakeWait === 0
            text: personMenu.wakeWait > 0 ? "Разбудить (через " + personMenu.wakeWait + " с)" : "Разбудить"
            onTriggered: backend.wakeUser(tile.person.id)
        }
        MenuItem {
            contentItem: SliderRow {
                title: "Громкость"; from: 0; to: 3; value: tile.person.volume
                onMoved: (v) => backend.setUserVolume(tile.person.id, v)
            }
            height: 64
            background: null
        }
        MenuItem { text: tile.person.localMuted ? "Снова слышать" : "Заглушить у себя"; onTriggered: backend.setUserLocallyMuted(tile.person.id, !tile.person.localMuted) }
        MenuItem { visible: tile.person.streaming; height: visible ? implicitHeight : 0; text: tile.person.watching ? "Перестать смотреть" : "Смотреть трансляцию"; onTriggered: backend.watch(tile.person.id, !tile.person.watching) }
        MenuSeparator { visible: backend.canModerate; contentItem: Rectangle { implicitHeight: 1; color: Theme.line } }
        MenuItem { visible: backend.canModerate; height: visible ? implicitHeight : 0; text: tile.person.serverMuted ? "Разрешить говорить" : "Заглушить для всех"; onTriggered: backend.serverMute(tile.person.id, !tile.person.serverMuted) }
        MenuItem { visible: backend.canModerate; height: visible ? implicitHeight : 0; text: "Отключить от канала"; onTriggered: backend.kickUser(tile.person.id) }
    }
}
