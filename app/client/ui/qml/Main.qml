import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Window

ApplicationWindow {
    id: window
    width: 1280; height: 800
    minimumWidth: 940; minimumHeight: 600
    visible: true
    title: backend.inCall ? backend.callChannelName + " · VoiceChat" : "VoiceChat"
    color: Theme.bg
    font.family: Theme.font

    Binding { target: Theme; property: "accent"; value: backend.settings.accent || "#7c5cff" }

    Loader {
        anchors.fill: parent
        sourceComponent: backend.view === "login" ? login : backend.view === "password" ? password
                         : backend.view === "main" ? main : loading
    }
    Component { id: login; LoginView {} }
    Component { id: password; PasswordView {} }
    Component { id: main; MainView { objectName: "mainView"; onToggleFullscreen: window.visibility === Window.FullScreen ? window.showNormal() : window.showFullScreen() } }
    Component {
        id: loading
        Rectangle {
            color: Theme.bg
            Column {
                anchors.centerIn: parent; spacing: 16
                Spinner { anchors.horizontalCenter: parent.horizontalCenter; size: 36; color: Theme.accent }
                Text { text: "Подключаюсь…"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 14 }
            }
        }
    }

    Toasts {
        anchors.top: parent.top; anchors.right: parent.right; anchors.margins: 16
        z: 1000
    }

    Shortcut { sequence: "F11"; onActivated: window.visibility === Window.FullScreen ? window.showNormal() : window.showFullScreen() }
    Shortcut { sequence: "Ctrl+M"; enabled: backend.inCall; onActivated: backend.toggleMute() }

    OverlayWindow {}

    Connections {
        target: backend
        function onShowWindowRequested() { window.show(); window.raise(); window.requestActivate() }
    }
    onClosing: (close) => {
        if (backend.view === "main" && backend.settings.close_to_tray && trayAvailable) {
            close.accepted = false
            window.hide()
        }
    }
}
