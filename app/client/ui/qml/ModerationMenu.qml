import QtQuick
import QtQuick.Controls.Basic

// Right-click menu on a person in a voice channel: wake them up (anyone), moderation (creator, moderators).
StyledMenu {
    id: menu
    property int userId: 0
    property string name: ""
    property bool muted: false
    property bool deafened: false
    property int wakeWait: 0
    readonly property bool canWake: menu.muted || menu.deafened
    onAboutToShow: wakeWait = backend.wakeWait(menu.userId)

    MenuItem {
        visible: menu.canWake; height: visible ? implicitHeight : 0
        enabled: menu.wakeWait === 0
        text: menu.wakeWait > 0 ? "Разбудить (через " + menu.wakeWait + " с)" : "Разбудить"
        onTriggered: backend.wakeUser(menu.userId)
    }
    MenuItem { text: "Изменить имя для себя"; onTriggered: backend.askNickname(menu.userId) }
    MenuSeparator { visible: backend.canModerate; height: visible ? implicitHeight : 0; contentItem: Rectangle { implicitHeight: 1; color: Theme.line } }
    StyledMenu {
        id: moveMenu
        title: "Переместить в…"
        enabled: backend.canModerate
        Instantiator {
            model: menu.visible && backend.canModerate ? backend.voiceChannels() : []
            delegate: MenuItem { text: modelData.name; onTriggered: backend.moveUser(menu.userId, modelData.id) }
            onObjectAdded: (index, object) => moveMenu.insertItem(index, object)
            onObjectRemoved: (index, object) => moveMenu.removeItem(object)
        }
    }
    MenuItem { visible: backend.canModerate; height: visible ? implicitHeight : 0; text: menu.muted ? "Разрешить говорить" : "Заглушить для всех"; onTriggered: backend.serverMute(menu.userId, !menu.muted) }
    MenuItem { visible: backend.canModerate; height: visible ? implicitHeight : 0; text: "Отключить от канала"; onTriggered: backend.kickUser(menu.userId) }
}
