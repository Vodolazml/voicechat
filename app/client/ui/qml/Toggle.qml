import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// A settings row: title, optional hint, and a switch on the right.
Item {
    id: root
    property string title: ""
    property string hint: ""
    property bool checked: false
    signal toggled(bool value)
    implicitHeight: Math.max(44, col.implicitHeight + 16)
    Layout.fillWidth: true

    ColumnLayout {
        id: col
        anchors.left: parent.left
        anchors.right: sw.left
        anchors.rightMargin: 16
        anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        Text { text: root.title; color: Theme.text; font.family: Theme.font; font.pixelSize: 14; Layout.fillWidth: true; wrapMode: Text.WordWrap }
        Text { visible: root.hint !== ""; text: root.hint; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    }
    Switch {
        id: sw
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        checked: root.checked
        onToggled: root.toggled(checked)
        indicator: Rectangle {
            implicitWidth: 42; implicitHeight: 24; radius: 12
            color: sw.checked ? Theme.accent : Theme.line
            Behavior on color { ColorAnimation { duration: Theme.fast } }
            Rectangle {
                width: 18; height: 18; radius: 9; y: 3
                x: sw.checked ? parent.width - width - 3 : 3
                color: "white"
                Behavior on x { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutCubic } }
            }
        }
    }
}
