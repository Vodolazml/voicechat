import QtQuick

// The VoiceChat mark (assets/logo.svg): a white voice wave on a coral-to-pink tile.
Item {
    id: root
    property int size: 48
    property bool glow: false
    implicitWidth: size
    implicitHeight: size
    readonly property real k: size / 256

    Rectangle {
        visible: root.glow
        anchors.centerIn: parent
        width: root.size * 1.5; height: width; radius: width / 2
        opacity: 0.35
        gradient: Gradient {
            GradientStop { position: 0; color: "#ff5a72" }
            GradientStop { position: 1; color: "transparent" }
        }
    }
    Rectangle {
        anchors.fill: parent
        radius: root.size / 3
        gradient: Gradient {
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: "#ff7849" }
            GradientStop { position: 1; color: "#ff3d9a" }
        }
        Repeater {
            model: [[76.0, 116.4, 23.1], [99.1, 93.3, 69.3], [122.2, 70.2, 115.6], [145.3, 93.3, 69.3], [168.4, 116.4, 23.1]]
            delegate: Rectangle {
                required property var modelData
                x: modelData[0] * root.k; y: modelData[1] * root.k
                width: 11.6 * root.k; height: modelData[2] * root.k
                radius: width / 2
                color: "white"
                antialiasing: true
            }
        }
    }
}
