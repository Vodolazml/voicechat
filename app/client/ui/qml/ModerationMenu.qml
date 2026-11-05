import QtQuick
import QtQuick.Controls.Basic

StyledMenu {
    id: menu
    property int userId: 0
    property string name: ""
    property bool muted: false

    StyledMenu {
        id: moveMenu
        title: "Переместить в…"
        Instantiator {
            model: menu.visible ? backend.voiceChannels() : []
            delegate: MenuItem { text: modelData.name; onTriggered: backend.moveUser(menu.userId, modelData.id) }
            onObjectAdded: (index, object) => moveMenu.insertItem(index, object)
            onObjectRemoved: (index, object) => moveMenu.removeItem(object)
        }
    }
    MenuItem { text: menu.muted ? "Разрешить говорить" : "Заглушить для всех"; onTriggered: backend.serverMute(menu.userId, !menu.muted) }
    MenuItem { text: "Отключить от канала"; onTriggered: backend.kickUser(menu.userId) }
}
