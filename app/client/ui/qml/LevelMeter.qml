import QtQuick

// Microphone level with the voice threshold marker; turns green when your voice is being sent.
Rectangle {
    id: meter
    property real level: 0
    property real threshold: 0.25
    property bool active: false
    height: 10; radius: 5
    color: Theme.line
    Rectangle {
        height: parent.height; radius: 5
        width: Math.max(parent.radius * 2, parent.width * meter.level)
        color: meter.active ? Theme.good : Theme.faint
        Behavior on width { NumberAnimation { duration: 60 } }
        Behavior on color { ColorAnimation { duration: Theme.fast } }
    }
    Rectangle { visible: meter.threshold > 0; x: parent.width * meter.threshold - 1; width: 2; height: parent.height + 6; y: -3; color: Theme.text; radius: 1 }
}
