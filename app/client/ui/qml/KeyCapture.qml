import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// "Click and press a key" field for hotkeys. Works with Ctrl/Shift/Alt combos and mouse side buttons.
Item {
    id: root
    property string title: ""
    property string hint: ""
    property string value: ""
    property bool listening: false
    signal changed(string value)
    Layout.fillWidth: true
    implicitHeight: 52

    ColumnLayout {
        anchors.left: parent.left; anchors.right: box.left; anchors.rightMargin: 16; anchors.verticalCenter: parent.verticalCenter
        spacing: 2
        Text { text: root.title; color: Theme.text; font.family: Theme.font; font.pixelSize: 14 }
        Text { visible: root.hint !== ""; text: root.hint; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; Layout.fillWidth: true; wrapMode: Text.WordWrap }
    }
    Rectangle {
        id: box
        anchors.right: clear.left; anchors.rightMargin: 4; anchors.verticalCenter: parent.verticalCenter
        width: 190; height: 38; radius: 10
        color: Theme.bg
        border.width: root.listening ? 2 : 1
        border.color: root.listening ? Theme.accent : Theme.line
        focus: root.listening
        Text {
            anchors.centerIn: parent
            text: root.listening ? "Нажмите клавишу…" : (root.value || "Не назначено")
            color: root.listening ? Theme.accent2 : (root.value ? Theme.text : Theme.faint)
            font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold
        }
        TapHandler {
            acceptedButtons: Qt.LeftButton | Qt.BackButton | Qt.ForwardButton
            onTapped: (point, button) => {
                if (root.listening && (button === Qt.BackButton || button === Qt.ForwardButton)) {
                    root.finish(button === Qt.BackButton ? "Mouse4" : "Mouse5")
                } else {
                    root.listening = true
                    box.forceActiveFocus()
                }
            }
        }
        Keys.onPressed: (event) => {
            if (!root.listening) return
            event.accepted = true
            if (event.key === Qt.Key_Escape) { root.listening = false; return }
            if ([Qt.Key_Control, Qt.Key_Shift, Qt.Key_Alt, Qt.Key_Meta].indexOf(event.key) >= 0) return
            var name = backend.keyName(event.key, event.text)
            if (name === "") return
            var parts = []
            if (event.modifiers & Qt.ControlModifier) parts.push("Ctrl")
            if (event.modifiers & Qt.ShiftModifier) parts.push("Shift")
            if (event.modifiers & Qt.AltModifier) parts.push("Alt")
            parts.push(name)
            root.finish(parts.join("+"))
        }
        onActiveFocusChanged: if (!activeFocus) root.listening = false
    }
    IconButton { id: clear; anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter; glyph: "close"; tip: "Сбросить"; size: 30; visible: root.value !== ""; onClicked: root.changed("") }
    function finish(text) { listening = false; changed(text) }
}
