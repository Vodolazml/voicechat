import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// In-window dialog (never a separate OS window, so it can't pull a game out of fullscreen).
Popup {
    id: modal
    property string title: ""
    property string subtitle: ""
    default property alias content: body.data
    parent: Overlay.overlay
    anchors.centerIn: parent
    width: Math.min(460, parent ? parent.width - 48 : 460)
    modal: true
    focus: true
    padding: 24
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
    Overlay.modal: Rectangle { color: Qt.rgba(0, 0, 0, 0.55) }
    enter: Transition { ParallelAnimation {
        NumberAnimation { property: "opacity"; from: 0; to: 1; duration: Theme.fast }
        NumberAnimation { property: "scale"; from: 0.94; to: 1; duration: Theme.normal; easing.type: Easing.OutBack } } }
    exit: Transition { NumberAnimation { property: "opacity"; to: 0; duration: Theme.fast } }
    background: Rectangle { radius: 18; color: Theme.panel2; border.color: Theme.line }
    contentItem: ColumnLayout {
        spacing: 14
        RowLayout {
            Layout.fillWidth: true
            ColumnLayout {
                Layout.fillWidth: true; spacing: 2
                Text { text: modal.title; color: Theme.text; font.family: Theme.font; font.pixelSize: 18; font.weight: Font.Bold; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                Text { visible: modal.subtitle !== ""; text: modal.subtitle; color: Theme.muted; font.family: Theme.font; font.pixelSize: 13; Layout.fillWidth: true; wrapMode: Text.WordWrap }
            }
            IconButton { glyph: "close"; size: 30; onClicked: modal.close() }
        }
        ColumnLayout { id: body; Layout.fillWidth: true; spacing: 12 }
    }
}
