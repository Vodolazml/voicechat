import QtQuick

// The VoiceChat mark (assets/logo.svg): five voice bars that draw a "V" on the brand gradient.
// Below 56 px it switches to the bolder three-bar version, like the small icon sizes.
Item {
    id: root
    property int size: 48
    property bool glow: false
    implicitWidth: size
    implicitHeight: size
    readonly property bool small: size < 56
    readonly property real k: size / 256

    Rectangle {
        visible: root.glow
        anchors.centerIn: parent
        width: root.size * 1.5; height: width; radius: width / 2
        opacity: 0.35
        gradient: Gradient {
            GradientStop { position: 0; color: "#7b6cff" }
            GradientStop { position: 1; color: "transparent" }
        }
    }
    Rectangle {
        id: tile
        anchors.fill: parent
        radius: root.size * (root.small ? 0.22 : 0.25)
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: "#7b6cff" }
            GradientStop { position: 0.55; color: "#b44dff" }
            GradientStop { position: 1; color: "#ff4f9a" }
        }
        Rectangle {  // soft highlight in the top-left corner
            anchors.fill: parent; radius: parent.radius
            gradient: Gradient {
                GradientStop { position: 0; color: Qt.rgba(1, 1, 1, 0.22) }
                GradientStop { position: 0.6; color: Qt.rgba(1, 1, 1, 0) }
            }
        }
        Repeater {
            model: root.small ? [[44, 56, 44, 80], [106, 56, 44, 148], [168, 56, 44, 80]]
                              : [[47, 62, 26, 48], [81, 62, 26, 88], [115, 62, 26, 134], [149, 62, 26, 88], [183, 62, 26, 48]]
            delegate: Rectangle {
                required property var modelData
                x: modelData[0] * root.k; y: modelData[1] * root.k
                width: modelData[2] * root.k; height: modelData[3] * root.k
                radius: width / 2
                color: "white"
                antialiasing: true
            }
        }
    }
}
