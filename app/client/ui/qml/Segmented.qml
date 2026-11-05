import QtQuick
import QtQuick.Layouts

ColumnLayout {
    id: root
    property string title: ""
    property var options: []
    property var current
    signal picked(var value)
    spacing: 6
    Layout.fillWidth: true
    Text { visible: root.title !== ""; text: root.title; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold; font.capitalization: Font.AllUppercase }
    Flow {
        Layout.fillWidth: true; spacing: 6
        Repeater {
            model: root.options
            delegate: Rectangle {
                height: 36; radius: 10; width: label.implicitWidth + 28
                color: modelData.value === root.current ? Theme.accent : (h.hovered ? Theme.cardHover : Theme.card)
                Behavior on color { ColorAnimation { duration: Theme.fast } }
                Text { id: label; anchors.centerIn: parent; text: modelData.title; color: modelData.value === root.current ? "white" : Theme.text; font.family: Theme.font; font.pixelSize: 13 }
                HoverHandler { id: h; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: root.picked(modelData.value) }
            }
        }
    }
}
