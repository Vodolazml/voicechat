import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Space settings: picture (creator only), name, channel names, best screen share quality, cameras,
// and who are moderators (creator only). Members without rights see it read-only.
Modal {
    id: dlg
    title: backend.canEditSpace ? "Настройки пространства" : "Пространство «" + backend.spaceName + "»"
    width: Math.min(560, parent ? parent.width - 48 : 560)
    signal renameChannel(int channelId, string name, int limit, string type)

    property string quality: backend.spaceShareQuality
    property bool camera: backend.cameraAllowed
    readonly property bool isCreator: backend.spaceRole === "admin"
    onAboutToShow: {
        nameField.text = backend.spaceName
        quality = backend.spaceShareQuality
        camera = backend.cameraAllowed
        backend.loadMembers()
    }

    Flickable {
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(contentHeight, (dlg.parent ? dlg.parent.height : 700) - 200)
        contentHeight: col.implicitHeight
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        ColumnLayout {
            id: col
            width: parent.width - 12
            spacing: 12

            PicturePicker {
                Layout.fillWidth: true
                square: true
                editable: dlg.isCreator
                source: backend.spaceAvatar
                initials: backend.spaceName.length ? backend.spaceName.substring(0, 2).toUpperCase() : "?"
                color: Theme.accent
                hint: dlg.isCreator ? "Картинка пространства видна всем участникам слева, в списке пространств."
                                    : "Картинку пространства меняет только его создатель."
                onPicked: (url) => backend.setSpaceAvatar(url)
                onCleared: backend.setSpaceAvatar("")
            }

            Text { text: "НАЗВАНИЕ"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
            Field { id: nameField; Layout.fillWidth: true; enabled: backend.canEditSpace; placeholderText: "Название пространства" }

            Text { text: "КАНАЛЫ"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
            Repeater {
                model: backend.channelsModel
                delegate: Rectangle {
                    Layout.fillWidth: true; height: 40; radius: 10; color: Theme.bg
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 12; anchors.rightMargin: 6; spacing: 10
                        Icon { name: model.type === "voice" ? "volume" : "hash"; color: Theme.muted; width: 16; height: 16 }
                        Text { Layout.fillWidth: true; text: model.name; elide: Text.ElideRight; color: Theme.text; font.family: Theme.font; font.pixelSize: 13 }
                        IconButton { visible: backend.canEditSpace; glyph: "edit"; size: 28; tip: "Переименовать"; onClicked: dlg.renameChannel(model.id, model.name, model.user_limit, model.type) }
                    }
                }
            }

            Text { text: "ДЕМОНСТРАЦИЯ ЭКРАНА: ЛУЧШЕЕ КАЧЕСТВО"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
            Flow {
                Layout.fillWidth: true; spacing: 8
                Repeater {
                    model: backend.screenPresets
                    delegate: Rectangle {
                        height: 32; radius: 16; width: qt.implicitWidth + 26
                        color: modelData.key === dlg.quality ? Theme.accent : Theme.card
                        opacity: backend.canEditSpace || modelData.key === dlg.quality ? 1 : 0.5
                        Text { id: qt; anchors.centerIn: parent; text: modelData.title; color: "white"; font.family: Theme.font; font.pixelSize: 12 }
                        HoverHandler { enabled: backend.canEditSpace; cursorShape: Qt.PointingHandCursor }
                        TapHandler { enabled: backend.canEditSpace; onTapped: dlg.quality = modelData.key }
                    }
                }
            }
            Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; text: "Выше этого никто в пространстве не сможет транслировать. Меньше качество, меньше нагрузка на интернет у всех."; color: Theme.faint; font.family: Theme.font; font.pixelSize: 12 }

            Toggle {
                Layout.fillWidth: true
                enabled: backend.canEditSpace
                title: "Разрешить веб-камеры"
                hint: "Участники смогут включать камеру в голосовых каналах"
                checked: dlg.camera
                onToggled: (v) => dlg.camera = v
            }

            PrimaryButton {
                visible: backend.canEditSpace
                Layout.fillWidth: true
                text: "Сохранить"
                enabled: nameField.text.trim().length >= 2
                onClicked: { backend.updateSpace(nameField.text, dlg.quality, dlg.camera); dlg.close() }
            }

            Text { text: "УЧАСТНИКИ · " + backend.spaceMembers.length; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold; Layout.topMargin: 6 }
            Text {
                visible: dlg.isCreator
                Layout.fillWidth: true; wrapMode: Text.WordWrap
                text: "Модератор может всё, что и вы, кроме создания и удаления каналов, удаления пространства и смены его картинки."
                color: Theme.faint; font.family: Theme.font; font.pixelSize: 12
            }
            Repeater {
                model: backend.spaceMembers
                delegate: Rectangle {
                    Layout.fillWidth: true; height: 48; radius: 10; color: Theme.bg
                    RowLayout {
                        anchors.fill: parent; anchors.leftMargin: 10; anchors.rightMargin: 10; spacing: 10
                        Avatar { size: 30; initials: modelData.initials; color: modelData.color; source: modelData.avatar || "" }
                        Text { Layout.fillWidth: true; text: modelData.display_name + (modelData.isMe ? " (вы)" : ""); elide: Text.ElideRight; color: Theme.text; font.family: Theme.font; font.pixelSize: 13 }
                        Rectangle {
                            visible: modelData.role !== "member"
                            radius: 8; height: 22; width: roleText.implicitWidth + 16
                            color: modelData.role === "admin" ? Theme.accentSoft : Qt.rgba(0.13, 0.76, 0.65, 0.16)
                            Text { id: roleText; anchors.centerIn: parent; text: modelData.role === "admin" ? "создатель" : "модератор"; color: modelData.role === "admin" ? Theme.accent2 : Theme.good; font.family: Theme.font; font.pixelSize: 11; font.weight: Font.DemiBold }
                        }
                        PrimaryButton {
                            visible: dlg.isCreator && modelData.role !== "admin"
                            secondary: true; implicitHeight: 28; font.pixelSize: 12
                            text: modelData.role === "moderator" ? "Снять модератора" : "Сделать модератором"
                            onClicked: backend.setModerator(modelData.id, modelData.role !== "moderator")
                        }
                    }
                }
            }
        }
    }
}
