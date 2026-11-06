import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Item {
    id: root
    property bool inviteMode: false

    Aurora { anchors.fill: parent }

    Rectangle {
        id: card
        anchors.centerIn: parent
        width: Math.min(420, parent.width - 48)
        height: form.implicitHeight + 64
        radius: 20
        color: Qt.rgba(0.08, 0.085, 0.12, 0.86)
        border.color: Qt.rgba(1, 1, 1, 0.07)
        Behavior on height { NumberAnimation { duration: Theme.normal; easing.type: Easing.OutCubic } }

        opacity: 0; scale: 0.96
        Component.onCompleted: { opacity = 1; scale = 1 }
        Behavior on opacity { NumberAnimation { duration: 380 } }
        Behavior on scale { NumberAnimation { duration: 420; easing.type: Easing.OutBack } }

        ColumnLayout {
            id: form
            anchors.fill: parent
            anchors.margins: 32
            spacing: 14

            RowLayout {
                spacing: 12
                Logo { size: 48 }
                ColumnLayout {
                    spacing: 0
                    Text { text: "VoiceChat"; color: Theme.text; font.family: Theme.font; font.pixelSize: 22; font.weight: Font.Bold }
                    Text { text: root.inviteMode ? "Новый аккаунт" : "С возвращением!"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 13 }
                }
            }

            Item { height: 4 }

            Field {
                id: server
                Layout.fillWidth: true
                visible: !backend.serverLocked
                placeholderText: "Адрес сервера, например chat.example.org"
                text: backend.serverUrl
            }
            Field {
                id: username
                Layout.fillWidth: true
                placeholderText: "Логин"
                text: backend.rememberedUsername
                onAccepted: password.forceActiveFocus()
            }
            Field {
                id: displayName
                Layout.fillWidth: true
                visible: root.inviteMode
                placeholderText: "Имя, которое увидят друзья"
            }
            Field {
                id: password
                Layout.fillWidth: true
                placeholderText: root.inviteMode ? "Пароль: от 8 символов" : "Пароль"
                echoMode: TextInput.Password
                onAccepted: submit()
            }
            PasswordMeter { Layout.fillWidth: true; visible: root.inviteMode && password.text.length > 0; value: password.text }

            CheckBox {
                id: remember
                checked: backend.rememberMe
                text: "Запомнить меня на этом компьютере"
                font.family: Theme.font
                font.pixelSize: 13
                contentItem: Text { leftPadding: 30; text: remember.text; font: remember.font; color: Theme.muted; verticalAlignment: Text.AlignVCenter }
                indicator: Rectangle {
                    x: 0; y: (remember.height - height) / 2
                    width: 20; height: 20; radius: 6
                    color: remember.checked ? Theme.accent : "transparent"
                    border.color: remember.checked ? Theme.accent : Theme.line; border.width: 2
                    Icon { anchors.centerIn: parent; name: "check"; color: "white"; width: 14; height: 14; visible: remember.checked }
                }
            }

            Text {
                Layout.fillWidth: true
                visible: backend.error !== ""
                text: backend.error
                color: Theme.bad
                wrapMode: Text.WordWrap
                font.family: Theme.font; font.pixelSize: 13
            }

            PrimaryButton {
                Layout.fillWidth: true
                text: root.inviteMode ? "Создать аккаунт" : "Войти"
                loading: backend.busy
                enabled: !backend.busy
                onClicked: submit()
            }

            Text {
                Layout.alignment: Qt.AlignHCenter
                text: root.inviteMode ? "Уже есть аккаунт? <a href='#' style='color:#b4a5ff; text-decoration:none'>Войти</a>" : "Нет аккаунта? <a href='#' style='color:#b4a5ff; text-decoration:none'>Зарегистрироваться</a>"
                textFormat: Text.RichText
                linkColor: Theme.accent2
                color: Theme.muted
                font.family: Theme.font; font.pixelSize: 13
                onLinkActivated: root.inviteMode = !root.inviteMode
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
        }
    }

    Text {
        anchors.bottom: parent.bottom; anchors.horizontalCenter: parent.horizontalCenter; anchors.bottomMargin: 16
        text: "Голос и экран защищены сквозным шифрованием · версия " + backend.version
        color: Theme.faint; font.family: Theme.font; font.pixelSize: 12
    }

    function submit() {
        if (root.inviteMode)
            backend.register(server.text, "", username.text, displayName.text, password.text, remember.checked)
        else
            backend.login(server.text, username.text, password.text, remember.checked)
    }
}
