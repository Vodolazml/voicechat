import QtQuick
import QtQuick.Controls.Basic

// Left rail: the app mark, your spaces as coloured tiles, "+" to create or join one, the version.
Rectangle {
    id: rail
    signal addSpace()
    color: Theme.rail
    implicitWidth: 72

    Rectangle { anchors.right: parent.right; width: 1; height: parent.height; color: Qt.rgba(1, 1, 1, 0.04) }

    Column {
        anchors.top: parent.top
        anchors.topMargin: 14
        anchors.horizontalCenter: parent.horizontalCenter
        spacing: 10

        Logo {
            anchors.horizontalCenter: parent.horizontalCenter
            size: 44
            HoverHandler { id: logoHover }
            ToolTip.visible: logoHover.hovered; ToolTip.text: "VoiceChat " + backend.version; ToolTip.delay: 300
        }
        Rectangle { width: 28; height: 2; radius: 1; color: Theme.line; anchors.horizontalCenter: parent.horizontalCenter }

        Repeater {
            model: backend.spacesModel
            delegate: Item {
                id: space
                width: 72; height: 50
                readonly property bool current: model.id === backend.spaceId
                readonly property color tint: model.color || Theme.accent
                // Selection pill on the left edge, in the space's own colour.
                Rectangle {
                    x: -2; anchors.verticalCenter: parent.verticalCenter
                    width: 5; radius: 3
                    color: space.current ? space.tint : Theme.text
                    height: space.current ? 32 : (hover.hovered ? 16 : (model.unread > 0 ? 8 : 0))
                    Behavior on height { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutBack } }
                }
                Rectangle {
                    id: tile
                    anchors.centerIn: parent
                    width: 46; height: 46; radius: 15
                    scale: hover.hovered && !space.current ? 1.05 : 1
                    Behavior on scale { NumberAnimation { duration: Theme.fast } }
                    gradient: Gradient {
                        GradientStop { position: 0; color: space.current || hover.hovered ? Qt.lighter(space.tint, 1.15) : Theme.card }
                        GradientStop { position: 1; color: space.current || hover.hovered ? Qt.darker(space.tint, 1.25) : Theme.card }
                    }
                    border.width: space.current ? 0 : 1
                    border.color: Qt.rgba(1, 1, 1, 0.05)
                    Text {
                        anchors.centerIn: parent; text: model.initials
                        color: space.current || hover.hovered ? "white" : Theme.muted
                        font.family: Theme.font; font.pixelSize: 15; font.weight: Font.Bold
                    }
                    Rectangle {
                        visible: model.unread > 0 && !space.current
                        anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: -4
                        width: Math.max(20, badge.implicitWidth + 10); height: 20; radius: 10
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
            width: 72; height: 50
            Rectangle {
                anchors.centerIn: parent
                width: 46; height: 46; radius: 15
                color: addHover.hovered ? Theme.accentSoft : "transparent"
                border.width: 1.5
                border.color: addHover.hovered ? Theme.accent : Theme.line
                Behavior on color { ColorAnimation { duration: Theme.fast } }
                Icon { anchors.centerIn: parent; name: "add"; color: addHover.hovered ? Theme.accent2 : Theme.muted; width: 22; height: 22 }
            }
            HoverHandler { id: addHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: rail.addSpace() }
            ToolTip.visible: addHover.hovered
            ToolTip.text: "Создать пространство или войти по коду"
            ToolTip.delay: 300
        }
    }

    Rectangle {
        anchors.bottom: parent.bottom; anchors.bottomMargin: 12
        anchors.horizontalCenter: parent.horizontalCenter
        width: ver.implicitWidth + 12; height: 20; radius: 10
        color: backend.updateInfo.version ? Theme.accentSoft : "transparent"
        Text {
            id: ver; anchors.centerIn: parent
            text: "v" + backend.version
            color: backend.updateInfo.version ? Theme.accent2 : Theme.faint
            font.family: Theme.font; font.pixelSize: 10; font.weight: Font.DemiBold
        }
        HoverHandler { id: verHover }
        ToolTip.visible: verHover.hovered
        ToolTip.text: backend.updateInfo.version ? "Доступна версия " + backend.updateInfo.version + ", установится при следующем запуске"
                                                 : "VoiceChat " + backend.version + ", последняя версия"
        ToolTip.delay: 300
    }
}
