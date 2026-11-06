import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: root
    signal toggleFullscreen()
    property alias settingsOpen: settings.visible

    RowLayout {
        anchors.fill: parent
        spacing: 0
        SpaceRail { Layout.fillHeight: true; onAddSpace: spaceDialog.open() }
        Sidebar {
            Layout.fillHeight: true
            onOpenSettings: root.openSettings(0)
            onOpenInvite: invite.open()
            onLeaveSpace: confirm.ask("Выйти из «" + backend.spaceName + "»?", "Вернуться можно будет только по новому коду приглашения.", "Выйти", function() { backend.leaveSpace() })
            onDeleteSpace: confirm.ask("Удалить «" + backend.spaceName + "»?", "Все каналы и сообщения пропадут у всех участников. Это не отменить.", "Удалить", function() { backend.deleteSpace() })
            onOpenScreenShare: share.open()
            onOpenCamera: camera.open()
            onOpenSpaceSettings: spaceSettings.open()
            onCreateChannel: (type) => channelDialog.openCreate(type)
            onRenameChannel: (id, name, limit, type) => channelDialog.openEdit(id, name, limit, type)
            onDeleteChannel: (id, name) => confirm.ask("Удалить канал «" + name + "»?", "Все, кто в нём сейчас, будут отключены. Сообщения канала пропадут.", "Удалить", function() { backend.deleteChannel(id) })
        }
        Item {
            Layout.fillWidth: true; Layout.fillHeight: true
            HomeView { anchors.fill: parent; visible: !backend.inCall && backend.textChannelId === 0; onAddSpace: spaceDialog.open() }
            ChatView { anchors.fill: parent; visible: backend.textChannelId !== 0 }
            StageView {
                anchors.fill: parent
                visible: backend.inCall && backend.textChannelId === 0
                onOpenSettings: root.openSettings(0)
                onOpenScreenShare: share.open()
                onOpenCamera: camera.open()
                onOpenSafety: safety.open()
                onToggleFullscreen: root.toggleFullscreen()
            }
            // Update banner
            Rectangle {
                visible: !!backend.updateInfo.version
                anchors.top: parent.top; anchors.horizontalCenter: parent.horizontalCenter; anchors.topMargin: 10
                height: 36; radius: 18; width: upd.implicitWidth + 24
                color: Theme.accent
                RowLayout { id: upd; anchors.centerIn: parent; spacing: 8
                    Icon { name: "download"; color: "white"; width: 16; height: 16 }
                    Text { text: backend.updateProgress >= 0 ? "Скачиваю обновление… " + Math.round(backend.updateProgress * 100) + "%" : "Доступна версия " + backend.updateInfo.version + " · нажмите, чтобы обновить"; color: "white"; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold } }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: backend.installUpdate() }
            }
        }
    }

    SettingsView {
        id: settings
        anchors.fill: parent
        visible: false
        opacity: visible ? 1 : 0
        scale: visible ? 1 : 1.03
        Behavior on opacity { NumberAnimation { duration: Theme.normal } }
        Behavior on scale { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }
        onClosed: visible = false
        onOpenSafety: safety.open()
    }

    InviteDialog { id: invite; objectName: "inviteDialog" }
    SpaceDialog { id: spaceDialog; objectName: "spaceDialog" }
    ScreenShareDialog { id: share; objectName: "shareDialog" }
    CameraDialog { id: camera; objectName: "cameraDialog" }
    SpaceSettingsDialog { id: spaceSettings; objectName: "spaceSettingsDialog"; onRenameChannel: (id, name, limit, type) => channelDialog.openEdit(id, name, limit, type) }
    SafetyDialog { id: safety }
    ChannelDialog { id: channelDialog }
    ConfirmDialog { id: confirm }

    function openSettings(tab) { settings.tab = tab; settings.visible = true; settings.forceActiveFocus() }
}
