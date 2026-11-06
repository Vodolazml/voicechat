import QtQuick
import QtQuick.Window

// Small Discord-style launcher window: checking for updates, downloading one, restarting.
// The main window appears only after this is done, so an update never looks like the app opening.
Window {
    id: splash
    width: 300; height: 340
    flags: Qt.SplashScreen | Qt.FramelessWindowHint | Qt.WindowStaysOnTopHint
    // Declared inside the (still hidden) main window, Qt would make that its transient parent, and a
    // transient window is not shown before its parent is: in 0.4.0 the splash never appeared.
    transientParent: null
    color: "transparent"
    title: "VoiceChat"
    x: Screen.virtualX + (Screen.width - width) / 2
    y: Screen.virtualY + (Screen.height - height) / 2

    readonly property bool downloading: backend.updateProgress >= 0

    Rectangle {
        anchors.fill: parent
        radius: 18
        color: Theme.bg
        border.color: Qt.rgba(1, 1, 1, 0.08)
        clip: true

        Aurora { anchors.fill: parent; opacity: 0.8 }

        Column {
            anchors.centerIn: parent
            anchors.verticalCenterOffset: -6
            spacing: 18
            width: parent.width - 56

            Item {
                width: 96; height: 96
                anchors.horizontalCenter: parent.horizontalCenter
                Logo {
                    id: mark
                    anchors.centerIn: parent
                    size: 84; glow: true
                    SequentialAnimation on scale {
                        loops: Animation.Infinite
                        NumberAnimation { from: 1; to: 1.06; duration: 700; easing.type: Easing.InOutSine }
                        NumberAnimation { from: 1.06; to: 1; duration: 700; easing.type: Easing.InOutSine }
                    }
                }
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: splash.downloading ? "Обновляю до " + (backend.updateInfo.version || "")
                      : backend.view === "updating" ? "Проверяю обновления…" : "Запускаю…"
                color: Theme.text; font.family: Theme.font; font.pixelSize: 17; font.weight: Font.Bold
                wrapMode: Text.WordWrap
            }
            Rectangle {
                width: parent.width; height: 6; radius: 3
                color: Qt.rgba(1, 1, 1, 0.08)
                clip: true
                Rectangle {  // real progress while downloading, a running stripe while checking
                    visible: splash.downloading
                    width: parent.width * Math.max(0.03, backend.updateProgress); height: parent.height; radius: 3
                    gradient: Gradient {
                        orientation: Gradient.Horizontal
                        GradientStop { position: 0; color: "#ff7849" }
                        GradientStop { position: 1; color: "#ff3d9a" }
                    }
                    Behavior on width { NumberAnimation { duration: 200 } }
                }
                Rectangle {
                    id: stripe
                    readonly property real track: 244
                    visible: !splash.downloading
                    width: track * 0.3; height: parent.height; radius: 3
                    color: "#ff5a72"
                    NumberAnimation on x {
                        loops: Animation.Infinite; running: !splash.downloading && splash.visible
                        from: -stripe.track * 0.3; to: stripe.track; duration: 1100; easing.type: Easing.InOutQuad
                    }
                }
            }
            Text {
                width: parent.width
                horizontalAlignment: Text.AlignHCenter
                text: splash.downloading ? Math.round(backend.updateProgress * 100) + "%"
                                           + (backend.updateSizes ? " · " + backend.updateSizes : "")
                                           + "\nпрограмма перезапустится сама"
                                         : "Версия " + backend.version
                color: Theme.muted; font.family: Theme.font; font.pixelSize: 12
            }
        }
    }
}
