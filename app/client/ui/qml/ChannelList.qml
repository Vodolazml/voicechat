import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ListView {
    id: list
    clip: true
    spacing: 2
    model: backend.channelsModel
    boundsBehavior: Flickable.StopAtBounds
    ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded; width: 6 }
    signal createRequested(string type)
    signal renameRequested(int channelId, string name, int limit)
    signal deleteRequested(int channelId, string name)

    section.property: "type"
    section.delegate: Item {
        width: list.width; height: 34
        Text {
            anchors.left: parent.left; anchors.leftMargin: 14; anchors.bottom: parent.bottom; anchors.bottomMargin: 6
            text: section === "text" ? "ТЕКСТОВЫЕ КАНАЛЫ" : "ГОЛОСОВЫЕ КАНАЛЫ"
            color: Theme.muted; opacity: 0.8; font.family: Theme.font; font.pixelSize: 11; font.weight: Font.Bold; font.letterSpacing: 0.8
        }
        IconButton {
            visible: backend.canManageChannels
            anchors.right: parent.right; anchors.rightMargin: 8; anchors.bottom: parent.bottom; anchors.bottomMargin: 2
            size: 24; glyph: "add"; tip: "Создать канал"
            onClicked: list.createRequested(section)
        }
    }

    add: Transition { NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.normal } }
    displaced: Transition { NumberAnimation { properties: "y"; duration: Theme.normal; easing.type: Easing.OutCubic } }

    delegate: Column {
        id: channelItem
        width: list.width
        required property var model
        readonly property bool voice: model.type === "voice"

        Rectangle {
            id: row
            x: 8; width: parent.width - 16; height: 36; radius: 8
            color: channelItem.model.selected ? Theme.cardHover : (rowHover.hovered ? Theme.card : "transparent")
            Behavior on color { ColorAnimation { duration: Theme.fast } }

            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 8; spacing: 8
                Icon {
                    name: channelItem.voice ? "volume" : "hash"
                    color: channelItem.model.active ? Theme.good : Theme.faint
                    width: 18; height: 18
                }
                Text {
                    Layout.fillWidth: true
                    text: channelItem.model.name
                    elide: Text.ElideRight
                    color: channelItem.model.selected || channelItem.model.unread > 0 || rowHover.hovered ? Theme.text : Theme.muted
                    font.family: Theme.font; font.pixelSize: 14
                    font.weight: channelItem.model.unread > 0 ? Font.Bold : Font.Normal
                }
                Icon { visible: channelItem.model.is_private; name: "lock"; color: Theme.faint; width: 14; height: 14 }
                Rectangle {
                    visible: channelItem.voice && channelItem.model.user_limit > 0
                    radius: 6; color: Theme.bg; implicitWidth: lim.implicitWidth + 10; implicitHeight: 18
                    Text { id: lim; anchors.centerIn: parent; text: channelItem.model.memberCount + "/" + channelItem.model.user_limit; color: Theme.muted; font.pixelSize: 11; font.family: Theme.font }
                }
                Rectangle {
                    visible: channelItem.model.unread > 0
                    radius: 9; color: Theme.bad; implicitWidth: Math.max(18, cnt.implicitWidth + 10); implicitHeight: 18
                    Text { id: cnt; anchors.centerIn: parent; text: channelItem.model.unread > 99 ? "99+" : channelItem.model.unread; color: "white"; font.pixelSize: 11; font.weight: Font.Bold; font.family: Theme.font }
                }
            }
            HoverHandler { id: rowHover; cursorShape: Qt.PointingHandCursor }
            TapHandler {
                onTapped: channelItem.voice ? backend.joinChannel(channelItem.model.id) : backend.openChannel(channelItem.model.id)
            }
            TapHandler {
                acceptedButtons: Qt.RightButton
                enabled: backend.canManageChannels
                onTapped: channelMenu.popup()
            }
            StyledMenu {
                id: channelMenu
                MenuItem { text: "Переместить выше"; onTriggered: backend.moveChannel(channelItem.model.id, -1) }
                MenuItem { text: "Переместить ниже"; onTriggered: backend.moveChannel(channelItem.model.id, 1) }
                MenuItem { text: "Изменить"; onTriggered: list.renameRequested(channelItem.model.id, channelItem.model.name, channelItem.model.user_limit) }
                MenuItem { text: "Удалить канал"; onTriggered: list.deleteRequested(channelItem.model.id, channelItem.model.name) }
            }
        }

        // People in a voice channel, nested under it.
        Repeater {
            model: channelItem.voice ? channelItem.model.members : []
            delegate: Rectangle {
                id: member
                required property var modelData
                x: 34; width: list.width - 42; height: 30; radius: 6
                color: memberHover.hovered ? Theme.card : "transparent"
                RowLayout {
                    anchors.fill: parent; anchors.leftMargin: 6; anchors.rightMargin: 6; spacing: 8
                    Avatar { size: 22; initials: member.modelData.initials; color: member.modelData.color; speaking: member.modelData.speaking }
                    Text {
                        Layout.fillWidth: true
                        text: member.modelData.name
                        elide: Text.ElideRight
                        color: member.modelData.speaking ? Theme.text : Theme.muted
                        opacity: member.modelData.reconnecting ? 0.5 : 1
                        font.family: Theme.font; font.pixelSize: 13
                    }
                    Rectangle {
                        visible: member.modelData.streaming
                        radius: 4; color: Theme.bad; implicitWidth: 34; implicitHeight: 16
                        Text { anchors.centerIn: parent; text: "LIVE"; color: "white"; font.pixelSize: 9; font.weight: Font.Black; font.family: Theme.font }
                    }
                    Icon { visible: member.modelData.muted && !member.modelData.deafened; name: "mic_off"; color: Theme.bad; width: 15; height: 15 }
                    Icon { visible: member.modelData.deafened; name: "headset_off"; color: Theme.bad; width: 15; height: 15 }
                }
                HoverHandler { id: memberHover }
                TapHandler {
                    acceptedButtons: Qt.RightButton
                    enabled: backend.canModerate && !member.modelData.isMe
                    onTapped: modMenu.popup()
                }
                ModerationMenu { id: modMenu; userId: member.modelData.id; name: member.modelData.name; muted: member.modelData.muted }
            }
        }
        Item { width: 1; height: channelItem.voice && channelItem.model.memberCount > 0 ? 4 : 0 }
    }
}
