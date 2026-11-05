import QtQuick
import QtQuick.Controls.Basic

TextField {
    id: field
    property string label: ""
    color: Theme.text
    placeholderTextColor: Theme.faint
    selectionColor: Theme.accent
    selectedTextColor: "white"
    font.family: Theme.font
    font.pixelSize: 14
    leftPadding: 14; rightPadding: 14
    topPadding: 11; bottomPadding: 11
    background: Rectangle {
        radius: 10
        color: Theme.bg
        border.width: field.activeFocus ? 2 : 1
        border.color: field.activeFocus ? Theme.accent : Theme.line
        Behavior on border.color { ColorAnimation { duration: Theme.fast } }
    }
}
