import QtQuick
import QtQuick.Layouts

// In-app notifications. They never take focus and never open a system dialog.
ListView {
    id: list
    width: 360
    height: contentHeight
    interactive: false
    spacing: 8
    model: backend.toastsModel
    add: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.normal }
            NumberAnimation { property: "x"; from: 60; to: 0; duration: Theme.normal; easing.type: Easing.OutCubic }
        }
    }
    remove: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.fast } }
    displaced: Transition { NumberAnimation { properties: "y"; duration: Theme.normal; easing.type: Easing.OutCubic } }

    delegate: Rectangle {
        width: list.width
        height: Math.max(48, label.implicitHeight + 24)
        radius: 12
        color: "#1b1c28"
        border.color: Qt.rgba(1, 1, 1, 0.07)
        readonly property color tint: model.level === "error" ? Theme.bad : model.level === "warning" ? Theme.warn
                                      : model.level === "success" ? Theme.good : model.level === "mention" ? "#ffd166" : Theme.accent2
        Rectangle { width: 4; height: parent.height - 16; radius: 2; x: 8; anchors.verticalCenter: parent.verticalCenter; color: parent.tint }
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 22; anchors.rightMargin: 8; spacing: 8
            Icon {
                name: model.level === "error" || model.level === "warning" ? "bell" : model.level === "success" ? "check" : model.level === "mention" ? "chat" : "bell"
                color: parent.parent.tint; width: 18; height: 18
            }
            Text { id: label; Layout.fillWidth: true; text: model.text; color: Theme.text; wrapMode: Text.WordWrap; font.family: Theme.font; font.pixelSize: 13 }
            IconButton { size: 24; glyph: "close"; onClicked: backend.dismissToast(model.id) }
        }
    }
}
