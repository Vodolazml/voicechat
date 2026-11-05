import QtQuick
import QtQuick.Controls.Basic

Rectangle {
    id: rail
    signal addSpace()
    color: Theme.rail
    implicitWidth: 72

    Text {
        anchors.bottom: parent.bottom; anchors.bottomMargin: 10
        anchors.horizontalCenter: parent.horizontalCenter
        text: "v" + backend.version
        color: Theme.faint; font.family: Theme.font; font.pixelSize: 11
        HoverHandler { id: verHover }
        ToolTip.visible: verHover.hovered
        ToolTip.text: backend.updateInfo.version ? "Доступна версия " + backend.updateInfo.version + ", установится при следующем запуске"
                                                 : "VoiceChat " + backend.version + ", последняя версия"
        ToolTip.delay: 300
    }

    Column {
        anchors.top: parent.top
        anchors.topMargin: 12
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 8

        Rectangle {
            width: 48; height: 48; radius: 16
            gradient: Gradient { orientation: Gradient.Horizontal
                GradientStop { position: 0; color: Theme.accent }
                GradientStop { position: 1; color: "#ff3d9a" } }
            Icon { anchors.centerIn: parent; name: "wave"; color: "white"; width: 26; height: 26 }
        }
        Rectangle { width: 32; height: 2; radius: 1; color: Theme.line; anchors.horizontalCenter: parent.horizontalCenter }

        Repeater {
            model: backend.spacesModel
            delegate: Item {
                width: 72; height: 52
                anchors.horizontalCenter: parent ? parent.horizontalCenter : undefined
                readonly property bool current: model.id === backend.spaceId
                Rectangle {
                    x: 0; anchors.verticalCenter: parent.verticalCenter
                    width: 4; radius: 2; color: Theme.text
                    height: current ? 36 : (hover.hovered ? 18 : (model.unread > 0 ? 8 : 0))
                    Behavior on height { NumberAnimation { duration: Theme.fast } }
                }
                Rectangle {
                    id: tile
                    anchors.centerIn: parent
                    width: 48; height: 48
                    radius: current || hover.hovered ? 16 : 24
                    color: current ? Theme.accent : (hover.hovered ? Theme.cardHover : Theme.card)
                    Behavior on radius { NumberAnimation { duration: Theme.fast } }
                    Behavior on color { ColorAnimation { duration: Theme.fast } }
                    Text { anchors.centerIn: parent; text: model.initials; color: "white"; font.family: Theme.font; font.pixelSize: 16; font.weight: Font.DemiBold }
                    Rectangle {
                        visible: model.unread > 0 && !current
                        anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: -3
                        width: Math.max(18, badge.implicitWidth + 8); height: 18; radius: 9
                        color: Theme.bad; border.width: 3; border.color: Theme.rail
                        Text { id: badge; anchors.centerIn: parent; text: model.unread > 99 ? "99+" : model.unread; color: "white"; font.pixelSize: 10; font.weight: Font.Bold; font.family: Theme.font }
                    }
                }
                HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: backend.selectSpace(model.id) }
                ToolTip.visible: hover.hovered
                ToolTip.text: model.name
                ToolTip.delay: 300
            }
        }

        Item {
            width: 72; height: 52
            Rectangle {
                anchors.centerIn: parent
                width: 48; height: 48
                radius: addHover.hovered ? 16 : 24
                color: addHover.hovered ? Theme.good : Theme.card
                Behavior on radius { NumberAnimation { duration: Theme.fast } }
                Behavior on color { ColorAnimation { duration: Theme.fast } }
                Icon { anchors.centerIn: parent; name: "add"; color: addHover.hovered ? "white" : Theme.good; width: 24; height: 24 }
            }
            HoverHandler { id: addHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: rail.addSpace() }
            ToolTip.visible: addHover.hovered
            ToolTip.text: "Создать пространство или войти по коду"
            ToolTip.delay: 300
        }
    }
}
