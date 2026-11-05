import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts
import VoiceChat

Rectangle {
    id: root
    color: Theme.bg
    property bool chatOpen: true
    signal openSettings()
    signal openScreenShare()
    signal openSafety()
    signal toggleFullscreen()

    RowLayout {
        anchors.fill: parent
        spacing: 0

        Item {
            Layout.fillWidth: true; Layout.fillHeight: true

            // Header
            RowLayout {
                id: header
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
                anchors.margins: 14; height: 36; spacing: 10
                Icon { name: "volume"; color: Theme.good; width: 22; height: 22 }
                Text { text: backend.callChannelName; color: Theme.text; font.family: Theme.font; font.pixelSize: 16; font.weight: Font.Bold }
                Rectangle {
                    radius: 13; height: 26; width: e2ee.implicitWidth + 20
                    color: Qt.rgba(0.18, 0.84, 0.48, 0.12)
                    RowLayout { id: e2ee; anchors.centerIn: parent; spacing: 6
                        Icon { name: "lock"; color: Theme.good; width: 13; height: 13 }
                        Text { text: "Сквозное шифрование"; color: Theme.good; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold } }
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: root.openSafety() }
                    ToolTip.visible: e2eeHover.hovered; ToolTip.text: "Нажмите, чтобы сверить коды безопасности с друзьями"
                    HoverHandler { id: e2eeHover }
                }
                Item { Layout.fillWidth: true }
                Text { visible: backend.ping > 0; text: backend.ping + " мс"; color: backend.ping < 80 ? Theme.good : (backend.ping < 160 ? Theme.warn : Theme.bad); font.family: Theme.font; font.pixelSize: 12 }
                IconButton { glyph: "chat"; active: root.chatOpen; tip: root.chatOpen ? "Скрыть чат" : "Показать чат"; onClicked: root.chatOpen = !root.chatOpen }
            }

            // Focused stream
            Item {
                id: focusArea
                visible: backend.focusUserId !== 0
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: header.bottom; anchors.bottom: strip.top
                anchors.margins: 14
                Rectangle {
                    anchors.fill: parent; radius: 16; color: "black"; clip: true
                    VideoView { id: focused; anchors.fill: parent; userId: backend.focusUserId }
                    Spinner { anchors.centerIn: parent; visible: !focused.hasFrame; size: 32 }
                    Rectangle {
                        visible: focused.hasFrame && backend.focusVideoInfo !== ""
                        anchors.left: parent.left; anchors.top: parent.top; anchors.margins: 12
                        radius: 8; color: Qt.rgba(0, 0, 0, 0.6)
                        width: focusStats.implicitWidth + 18; height: focusStats.implicitHeight + 10
                        Column {
                            id: focusStats; anchors.centerIn: parent
                            Text { text: backend.focusVideoInfo; color: "white"; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
                            Text { visible: backend.focusVideoDetail !== ""; text: backend.focusVideoDetail; color: Qt.rgba(1, 1, 1, 0.7); font.family: Theme.font; font.pixelSize: 11 }
                        }
                    }
                    Row {
                        anchors.right: parent.right; anchors.top: parent.top; anchors.margins: 12; spacing: 6
                        opacity: focusHover.hovered ? 1 : 0
                        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
                        IconButton { glyph: "fullscreen"; tip: "Во весь экран (F11)"; fill: Qt.rgba(0, 0, 0, 0.6); onClicked: root.toggleFullscreen() }
                        IconButton { glyph: "fullscreen_exit"; tip: "Вернуться к сетке"; fill: Qt.rgba(0, 0, 0, 0.6); onClicked: backend.focusUser(0) }
                    }
                    HoverHandler { id: focusHover }
                    TapHandler { onDoubleTapped: root.toggleFullscreen() }
                }
            }
            ListView {
                id: strip
                visible: backend.focusUserId !== 0
                anchors.left: parent.left; anchors.right: parent.right; anchors.bottom: controls.top
                anchors.margins: 14; height: visible ? 110 : 0
                orientation: ListView.Horizontal; spacing: 10; clip: true
                model: backend.stageModel
                delegate: StageTile { width: 180; height: 104; small: true; person: model; onFocusRequested: backend.focusUser(model.id) }
            }

            // Grid: picks the column count that gives the biggest 16:9 tiles, centred in the free space.
            Item {
                id: gridArea
                visible: backend.focusUserId === 0
                anchors.left: parent.left; anchors.right: parent.right; anchors.top: header.bottom; anchors.bottom: controls.top
                anchors.margins: 14
                readonly property int spacing: 12
                readonly property int n: Math.max(1, backend.stageModel.count)
                readonly property int cols: {
                    var best = 1, bestSize = 0
                    for (var c = 1; c <= n; ++c) {
                        var rows = Math.ceil(n / c)
                        var w = (width - (c - 1) * spacing) / c
                        var h = Math.min(w * 9 / 16, (height - (rows - 1) * spacing) / rows)
                        if (h > bestSize * 0.95) { bestSize = Math.max(h, bestSize); best = c }  // near-ties: wider grid
                    }
                    return best
                }
                readonly property int rows: Math.ceil(n / cols)
                readonly property real tileW: Math.floor(Math.min((width - (cols - 1) * spacing) / cols,
                                                                   ((height - (rows - 1) * spacing) / rows) * 16 / 9))
                Flow {
                    id: grid
                    anchors.centerIn: parent
                    width: gridArea.cols * gridArea.tileW + (gridArea.cols - 1) * gridArea.spacing + 1
                    spacing: gridArea.spacing
                    Repeater {
                        model: backend.stageModel
                        delegate: StageTile {
                            width: gridArea.tileW; height: Math.floor(gridArea.tileW * 9 / 16)
                            person: model
                            onFocusRequested: backend.focusUser(model.id)
                        }
                    }
                }
            }

            // Floating controls
            Rectangle {
                id: controls
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom; anchors.bottomMargin: 16
                height: 60; radius: 30; width: bar.implicitWidth + 24
                color: Theme.panel2; border.color: Theme.line
                RowLayout {
                    id: bar; anchors.centerIn: parent; spacing: 10
                    IconButton { size: 44; glyph: backend.muted || backend.serverMuted ? "mic_off" : "mic"; danger: backend.muted || backend.serverMuted; tip: backend.muted ? "Включить микрофон" : "Выключить микрофон"; onClicked: backend.toggleMute() }
                    IconButton { size: 44; glyph: backend.deafened ? "headset_off" : "headset"; danger: backend.deafened; tip: backend.deafened ? "Включить звук" : "Выключить звук"; onClicked: backend.toggleDeafen() }
                    IconButton { size: 44; visible: backend.canShare; glyph: "screen"; active: backend.sharing; tip: backend.sharing ? "Остановить показ экрана" : "Показать экран"; onClicked: backend.sharing ? backend.stopScreen() : root.openScreenShare() }
                    IconButton { size: 44; glyph: "settings"; tip: "Настройки звука"; onClicked: root.openSettings() }
                    Rectangle {
                        width: 64; height: 44; radius: 22; color: leaveHover.hovered ? Qt.lighter(Theme.bad, 1.1) : Theme.bad
                        Icon { anchors.centerIn: parent; name: "call_end"; color: "white"; width: 24; height: 24 }
                        HoverHandler { id: leaveHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: backend.leaveCall() }
                        ToolTip.visible: leaveHover.hovered; ToolTip.text: "Выйти из канала"
                    }
                }
            }
        }

        Rectangle { visible: root.chatOpen; Layout.fillHeight: true; width: 1; color: Theme.line }
        ChatView {
            visible: root.chatOpen
            Layout.preferredWidth: 360; Layout.fillHeight: true
            compactHeader: true
            color: Theme.panel
        }
    }
}
