import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Modal {
    id: dlg
    property string code: ""
    title: "Пригласить друга"
    subtitle: "Код одноразовый и действует сутки. Друг вводит его в окне входа, в «Есть приглашение?»."
    Connections { target: backend; function onInviteCreated(value) { dlg.code = value } }

    Toggle { id: admin; title: "Сделать администратором"; hint: "Сможет создавать каналы, приглашать и модерировать"; onToggled: (v) => checked = v }
    Rectangle {
        visible: dlg.code !== ""
        Layout.fillWidth: true; height: 54; radius: 12; color: Theme.bg; border.color: Theme.accent
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 8
            Text { Layout.fillWidth: true; text: dlg.code; color: Theme.text; font.family: "Consolas"; font.pixelSize: 15; elide: Text.ElideMiddle }
            IconButton { glyph: "copy"; tip: "Копировать"; onClicked: backend.copyText(dlg.code) }
        }
    }
    PrimaryButton { Layout.fillWidth: true; text: dlg.code ? "Создать ещё один" : "Создать приглашение"; onClicked: backend.createInvite(admin.checked) }
    onOpened: code = ""
}
