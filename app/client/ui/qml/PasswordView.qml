import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    Aurora { anchors.fill: parent }
    Rectangle {
        anchors.centerIn: parent
        width: Math.min(420, parent.width - 48)
        height: col.implicitHeight + 64
        radius: 20
        color: Qt.rgba(0.08, 0.085, 0.12, 0.9)
        border.color: Qt.rgba(1, 1, 1, 0.07)
        ColumnLayout {
            id: col
            anchors.fill: parent; anchors.margins: 32; spacing: 14
            Text { text: "Придумайте свой пароль"; color: Theme.text; font.family: Theme.font; font.pixelSize: 20; font.weight: Font.Bold }
            Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; text: "Временный пароль от администратора нужно заменить. Минимум 12 символов: буквы, цифры и знаки."; color: Theme.muted; font.family: Theme.font; font.pixelSize: 13 }
            Field { id: oldPwd; Layout.fillWidth: true; placeholderText: "Временный пароль"; echoMode: TextInput.Password }
            Field { id: newPwd; Layout.fillWidth: true; placeholderText: "Новый пароль"; echoMode: TextInput.Password }
            Field { id: again; Layout.fillWidth: true; placeholderText: "Ещё раз"; echoMode: TextInput.Password; onAccepted: go() }
            Text { Layout.fillWidth: true; visible: backend.error !== "" || mismatch; text: mismatch ? "Пароли не совпадают" : backend.error; color: Theme.bad; wrapMode: Text.WordWrap; font.family: Theme.font; font.pixelSize: 13 }
            PrimaryButton { Layout.fillWidth: true; text: "Сохранить"; loading: backend.busy; enabled: !backend.busy; onClicked: go() }
        }
    }
    property bool mismatch: false
    function go() {
        mismatch = newPwd.text !== again.text
        if (!mismatch) backend.changePassword(oldPwd.text, newPwd.text)
    }
}
