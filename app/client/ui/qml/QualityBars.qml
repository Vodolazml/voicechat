import QtQuick

// Connection quality: 0 poor, 1 good, 2 excellent, 3 lost.
Row {
    property int quality: 2
    spacing: 2
    readonly property int level: quality === 3 ? 0 : quality + 1
    readonly property color tint: level >= 3 ? Theme.good : (level === 2 ? Theme.warn : Theme.bad)
    Repeater {
        model: 3
        Rectangle {
            width: 3; height: 5 + index * 3; radius: 1
            anchors.bottom: parent.bottom
            color: index < level ? tint : Theme.line
        }
    }
}
