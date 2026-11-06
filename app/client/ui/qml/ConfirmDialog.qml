import QtQuick
import QtQuick.Layouts

Modal {
    id: dlg
    property string actionText: "Удалить"
    property var onConfirm: function() {}
    RowLayout {
        Layout.fillWidth: true; spacing: 10
        PrimaryButton { Layout.fillWidth: true; secondary: true; text: "Отмена"; onClicked: dlg.close() }
        PrimaryButton { Layout.fillWidth: true; danger: true; text: dlg.actionText; onClicked: { dlg.onConfirm(); dlg.close() } }
    }
    function ask(question, details, action, callback) { title = question; subtitle = details; actionText = action; onConfirm = callback; open() }
}
