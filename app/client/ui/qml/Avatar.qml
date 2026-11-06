import QtQuick

// Round avatar: their picture if they set one, otherwise initials; a soft green halo pulses while they speak.
Item {
    id: root
    property string initials: "?"
    property string source: ""
    property color color: Theme.accent
    property bool speaking: false
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
        Behavior on opacity { NumberAnimation { duration: Theme.fast } }
        Behavior on scale { NumberAnimation { duration: Theme.fast; easing.type: Easing.OutBack } }
    }
    Rectangle {
        anchors.centerIn: parent
        width: root.size + 16; height: width; radius: width / 2
        color: "transparent"
        border.width: 2
        border.color: Theme.speaking
        visible: root.speaking
        opacity: 0
        SequentialAnimation on opacity {
            running: root.speaking; loops: Animation.Infinite
            NumberAnimation { from: 0.45; to: 0; duration: 900; easing.type: Easing.OutCubic }
        }
        SequentialAnimation on scale {
            running: root.speaking; loops: Animation.Infinite
            NumberAnimation { from: 0.8; to: 1.15; duration: 900; easing.type: Easing.OutCubic }
        }
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
