import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Modal {
    id: dlg
    title: "Пригласить в «" + backend.spaceName + "»"
    subtitle: "Отправьте другу этот код. Он вводит его при регистрации или в «+» слева, если аккаунт уже есть."

    Rectangle {
        Layout.fillWidth: true; height: 64; radius: 12; color: Theme.bg; border.color: Theme.accent
        RowLayout {
            anchors.fill: parent; anchors.leftMargin: 18; anchors.rightMargin: 8
            Text { Layout.fillWidth: true; text: backend.spaceInviteCode; color: Theme.text; font.family: "Consolas"; font.pixelSize: 24; font.weight: Font.Bold; font.letterSpacing: 2 }
            IconButton { glyph: "copy"; tip: "Копировать"; onClicked: backend.copyText(backend.spaceInviteCode) }
        }
    }
    Text {
        Layout.fillWidth: true; wrapMode: Text.WordWrap
        text: "Участников: " + backend.spaceMemberCount + (backend.canManageChannels ? ". Если код попал не туда, смените его: старый перестанет работать, а кто уже вошёл, останется." : ".")
        color: Theme.muted; font.family: Theme.font; font.pixelSize: 12
    }
    PrimaryButton { visible: backend.canManageChannels; Layout.fillWidth: true; secondary: true; text: "Сменить код"; onClicked: backend.rotateInviteCode() }
}
