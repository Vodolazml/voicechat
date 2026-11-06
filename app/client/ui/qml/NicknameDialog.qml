import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// A name for someone that only you see (in channels, the call, chat and the overlay).
Modal {
    id: dlg
    property int userId: 0
    property string realName: ""
    title: "Имя для себя"
    subtitle: "Это имя видите только вы. Сам человек и остальные видят «" + dlg.realName + "»."

    Field { id: nameField; Layout.fillWidth: true; placeholderText: dlg.realName; onAccepted: dlg.save() }
    RowLayout {
        Layout.fillWidth: true; spacing: 10
        PrimaryButton { Layout.fillWidth: true; text: "Сохранить"; onClicked: dlg.save() }
        PrimaryButton {
            visible: backend.nickname(dlg.userId) !== "" || nameField.text !== ""
            Layout.fillWidth: true; text: "Вернуть настоящее"; secondary: true
            onClicked: { nameField.text = ""; dlg.save() }
        }
    }

    function openFor(id) {
        userId = id
        realName = backend.realName(id)
        nameField.text = backend.nickname(id)
        open()
        nameField.forceActiveFocus()
    }
    function save() { backend.setNickname(userId, nameField.text); close() }

    Connections { target: backend; function onNicknameRequested(id) { dlg.openFor(id) } }
}
