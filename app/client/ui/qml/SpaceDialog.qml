import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Create your own space, or join a friend's one with its invite code.
Modal {
    id: dlg
    title: "Пространства"
    subtitle: "Создайте своё и позовите друзей по коду, или войдите в чужое по коду приглашения."

    Text { text: "Войти по коду"; color: Theme.text; font.family: Theme.font; font.pixelSize: 14; font.weight: Font.DemiBold }
    RowLayout {
        Layout.fillWidth: true; spacing: 8
        Field { id: codeField; Layout.fillWidth: true; placeholderText: "Например ABCD-EFGH"; onAccepted: dlg.join() }
        PrimaryButton { text: "Войти"; enabled: codeField.text.trim().length >= 8; onClicked: dlg.join() }
    }
    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.line }
    Text { text: "Создать своё"; color: Theme.text; font.family: Theme.font; font.pixelSize: 14; font.weight: Font.DemiBold }
    RowLayout {
        Layout.fillWidth: true; spacing: 8
        Field { id: nameField; Layout.fillWidth: true; placeholderText: "Название, например «Наша компания»"; onAccepted: dlg.create() }
        PrimaryButton { text: "Создать"; secondary: true; enabled: nameField.text.trim().length >= 2; onClicked: dlg.create() }
    }

    onOpened: { codeField.text = ""; nameField.text = ""; codeField.forceActiveFocus() }
    function join() { if (codeField.text.trim().length >= 8) { backend.joinSpace(codeField.text); close() } }
    function create() { if (nameField.text.trim().length >= 2) { backend.createSpace(nameField.text); close() } }
}
