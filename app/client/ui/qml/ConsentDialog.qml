import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Asked once when the server's privacy policy, terms or consent have a newer edition than the one this
// account accepted (and for accounts made before they existed). The app is usable only after accepting.
Modal {
    id: dlg
    title: "Соглашение и конфиденциальность"
    subtitle: "Чтобы пользоваться сервером, примите пользовательское соглашение, политику конфиденциальности и дайте согласие на обработку персональных данных."
    closePolicy: Popup.NoAutoClose
    readonly property bool needed: backend.view === "main" && !!backend.me.consent_required
                                   && (backend.me.consent_version || 0) < backend.me.consent_required
    onNeededChanged: needed ? open() : close()
    Component.onCompleted: if (needed) open()

    Text {
        Layout.fillWidth: true; wrapMode: Text.WordWrap
        text: "<a href='terms' style='color:#b4a5ff; text-decoration:none'>Пользовательское соглашение</a><br>"
              + "<a href='privacy' style='color:#b4a5ff; text-decoration:none'>Политика конфиденциальности</a><br>"
              + "<a href='consent' style='color:#b4a5ff; text-decoration:none'>Согласие на обработку персональных данных</a>"
        textFormat: Text.RichText; lineHeight: 1.4
        color: Theme.muted; font.family: Theme.font; font.pixelSize: 14
        onLinkActivated: (link) => Qt.openUrlExternally(backend.legalUrl("", link))
        HoverHandler { cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.ArrowCursor }
    }
    Text {
        Layout.fillWidth: true; wrapMode: Text.WordWrap
        text: "Голос и экран шифруются от устройства до устройства и не записываются. Отозвать согласие можно в любой момент, удалив аккаунт в настройках."
        color: Theme.faint; font.family: Theme.font; font.pixelSize: 12
    }
    RowLayout {
        Layout.fillWidth: true; spacing: 10
        PrimaryButton { Layout.fillWidth: true; secondary: true; text: "Выйти"; onClicked: { dlg.close(); backend.logout() } }
        PrimaryButton { Layout.fillWidth: true; text: "Принимаю"; onClicked: backend.acceptConsent() }
    }
}
