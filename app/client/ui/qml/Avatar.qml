import QtQuick

// Round avatar: their picture if they set one, otherwise initials; a green halo while they speak.
// No endless pulse: with friends talking it redrew the window at the monitor rate (233 fps on PC3).
Item {
    id: root
    property string initials: "?"
    property string source: ""
    property color color: Theme.accent
    property bool speaking: false
    property bool animated: true  // the game overlay turns it off: every fade there is frames over the game
    property int size: 32
    readonly property int pictureSize: Math.min(256, Math.ceil(size * 2))
    implicitWidth: size
    implicitHeight: size

    Rectangle {
        id: halo
        anchors.centerIn: parent
        width: root.size + 8; height: width; radius: width / 2
        color: "transparent"
        border.width: 2
        border.color: Theme.speaking
        opacity: root.speaking ? 1 : 0
        scale: root.speaking ? 1 : 0.85
        Behavior on opacity { enabled: root.animated; NumberAnimation { duration: Theme.fast } }
        Behavior on scale { enabled: root.animated; NumberAnimation { duration: Theme.fast; easing.type: Easing.OutBack } }
    }
    Rectangle {
        anchors.fill: parent
        radius: width / 2
        gradient: Gradient {
            GradientStop { position: 0; color: Qt.lighter(root.color, 1.25) }
            GradientStop { position: 1; color: Qt.darker(root.color, 1.25) }
        }
        Image {  // the picture comes already cut to a circle (see images.py)
            id: picture
            anchors.fill: parent
            visible: root.source !== "" && status === Image.Ready
            source: root.source
            sourceSize: Qt.size(root.pictureSize, root.pictureSize)
            smooth: true; mipmap: true
            asynchronous: true
        }
        Text {
            anchors.centerIn: parent
            visible: !picture.visible
            text: root.initials
            color: "white"
            font.family: Theme.font
            font.pixelSize: Math.max(10, root.size * 0.38)
            font.weight: Font.DemiBold
        }
    }
}
