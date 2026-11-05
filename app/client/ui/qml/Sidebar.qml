import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Rectangle {
    id: root
    color: Theme.panel
    implicitWidth: 268
    signal openSettings()
    signal openInvite()
    signal openScreenShare()
    signal createChannel(string type)
    signal renameChannel(int channelId, string name, int limit)
    signal deleteChannel(int channelId, string name)

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        // Space header
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 52
            color: "transparent"
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 16; anchors.rightMargin: 10
                Text { Layout.fillWidth: true; text: backend.spaceName || "VoiceChat"; color: Theme.text; elide: Text.ElideRight; font.family: Theme.font; font.pixelSize: 15; font.weight: Font.Bold }
                IconButton { visible: backend.canInvite; glyph: "person_add"; tip: "Пригласить друга"; size: 30; onClicked: root.openInvite() }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
        }

        ChannelList {
            Layout.fillWidth: true; Layout.fillHeight: true
            onCreateRequested: (type) => root.createChannel(type)
            onRenameRequested: (id, name, limit) => root.renameChannel(id, name, limit)
            onDeleteRequested: (id, name) => root.deleteChannel(id, name)
        }

        // Voice connection card
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: backend.inCall ? 96 : 0
            clip: true
            color: Theme.panel2
            Behavior on Layout.preferredHeight { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
            Rectangle { width: parent.width; height: 1; color: Theme.line }
            ColumnLayout {
                anchors.fill: parent; anchors.margins: 12; spacing: 8
                RowLayout {
                    spacing: 8
                    QualityBars { quality: backend.callState === "connected" ? backend.myQuality : 0 }
                    ColumnLayout {
                        spacing: 0; Layout.fillWidth: true
                        Text {
                            text: backend.callState === "connected" ? "Голос подключён" : (backend.callState === "reconnecting" ? "Переподключаюсь…" : "Подключаюсь…")
                            color: backend.callState === "connected" ? Theme.good : Theme.warn
                            font.family: Theme.font; font.pixelSize: 13; font.weight: Font.Bold
                        }
                        Text {
                            Layout.fillWidth: true; elide: Text.ElideRight
                            text: backend.callChannelName + (backend.ping > 0 ? "  ·  " + backend.ping + " мс" : "")
                            color: Theme.muted; font.family: Theme.font; font.pixelSize: 12
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: backend.showCall() }
                        }
                    }
                    IconButton { glyph: "call_end"; danger: true; tip: "Выйти из канала"; size: 34; onClicked: backend.leaveCall() }
                }
                RowLayout {
                    spacing: 6
                    PrimaryButton {
                        Layout.fillWidth: true; implicitHeight: 32; secondary: !backend.sharing
                        visible: backend.canShare
                        text: backend.sharing ? "Остановить показ" : "Показать экран"
                        font.pixelSize: 12
                        onClicked: backend.sharing ? backend.stopScreen() : root.openScreenShare()
                    }
                    PrimaryButton {
                        Layout.fillWidth: true; implicitHeight: 32; secondary: true
                        text: "Чат и участники"; font.pixelSize: 12
                        onClicked: backend.showCall()
                    }
                }
            }
        }

        // User panel
        Rectangle {
            Layout.fillWidth: true; Layout.preferredHeight: 58
            color: "#0e0f15"
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 8; spacing: 8
                Item {
                    width: 36; height: 36
                    Avatar { anchors.fill: parent; size: 36; initials: backend.me.initials || "?"; color: backend.me.color || Theme.accent; speaking: backend.inCall && backend.transmitting }
                    Rectangle {
                        anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: -2
                        width: 14; height: 14; radius: 7; border.width: 3; border.color: "#0e0f15"
                        color: backend.online ? Theme.good : Theme.faint
                    }
                }
                ColumnLayout {
                    spacing: 0; Layout.fillWidth: true
                    Text { Layout.fillWidth: true; text: backend.me.display_name || ""; elide: Text.ElideRight; color: Theme.text; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
                    Text { Layout.fillWidth: true; text: backend.online ? "в сети" : "нет связи с сервером"; elide: Text.ElideRight; color: backend.online ? Theme.muted : Theme.warn; font.family: Theme.font; font.pixelSize: 11 }
                }
                IconButton {
                    glyph: backend.muted || backend.serverMuted ? "mic_off" : "mic"
                    danger: backend.muted || backend.serverMuted
                    tip: backend.serverMuted ? "Микрофон выключен модератором" : (backend.muted ? "Включить микрофон" : "Выключить микрофон")
                    onClicked: backend.toggleMute()
                }
                IconButton {
                    glyph: backend.deafened ? "headset_off" : "headset"
                    danger: backend.deafened
                    tip: backend.deafened ? "Включить звук" : "Выключить звук"
                    onClicked: backend.toggleDeafen()
                }
                IconButton { glyph: "settings"; tip: "Настройки"; onClicked: root.openSettings() }
            }
        }
    }
}
