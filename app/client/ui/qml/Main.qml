import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Window

ApplicationWindow {
    id: window
    width: 1280; height: 800
    minimumWidth: 940; minimumHeight: 600
    // Hidden until the update check is over: meanwhile the small splash window shows what is going on,
    // so an update never looks like the app itself opening.
    visible: false
    property bool everShown: false
    readonly property bool starting: backend.view === "updating" || (backend.view === "loading" && !everShown)
    onStartingChanged: revealIfReady()
    Component.onCompleted: revealIfReady()
    function revealIfReady() {
        if (starting || everShown)
            return
        everShown = true
        if (!startHidden)
            window.show()
    }
    title: backend.inCall ? backend.callChannelName + " — VoiceChat" : "VoiceChat"
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
    Component { id: main; MainView { objectName: "mainView"; onToggleFullscreen: window.videoFullscreen() } }
    // Only the video goes full screen, never the app window.
    function videoFullscreen() {
        if (backend.focusUserId !== 0)
            videoFull.showFor(backend.focusUserId, window.screen)
    }
    VideoFullscreen { id: videoFull }
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

    Shortcut { sequence: "F11"; onActivated: window.videoFullscreen() }
    Shortcut { sequence: "Ctrl+M"; enabled: backend.inCall; onActivated: backend.toggleMute() }

    OverlayWindow {}

    UpdateSplash {
        id: splash
        visible: window.starting && !splash.dismissed && (!startHidden || backend.updateProgress >= 0)
    }

    Connections {
        target: backend
        function onShowWindowRequested() {
            if (window.starting) {  // still updating: the tray icon brings back the hidden launcher
                splash.dismissed = false
                if (splash.visible) {
                    if (splash.visibility === Window.Minimized)
                        splash.showNormal()
                    splash.raise(); splash.requestActivate()
                }
                return
            }
            window.everShown = true; window.show(); window.raise(); window.requestActivate()
        }
    }
    onClosing: (close) => {
        if (backend.view === "main" && backend.settings.close_to_tray && trayAvailable) {
            close.accepted = false
            window.hide()
        }
    }
}
