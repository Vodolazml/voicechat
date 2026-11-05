import QtQuick
import QtQuick.Controls.Basic

// Round icon button with hover glow, tooltip and an optional "danger" (red) state.
AbstractButton {
    id: control
    property string glyph: ""
    property string tip: ""
    property bool danger: false
    property bool active: false
    property color tint: danger ? Theme.bad : (active ? Theme.accent2 : Theme.muted)
    property int size: 34
    property color fill: danger ? Qt.rgba(1, 0.3, 0.42, 0.14) : (active ? Theme.accentSoft : "transparent")
    implicitWidth: size
    implicitHeight: size
    hoverEnabled: true
    focusPolicy: Qt.NoFocus

    background: Rectangle {
        radius: control.size / 3.2
        color: control.down ? Qt.darker(Theme.cardHover, 1.2)
                            : control.hovered ? Theme.cardHover : control.fill
        Behavior on color { ColorAnimation { duration: Theme.fast } }
    }
    contentItem: Item {
        Icon {
            anchors.centerIn: parent
            width: control.size * 0.56; height: width
            name: control.glyph
            color: control.hovered && !control.danger && !control.active ? Theme.text : control.tint
            scale: control.down ? 0.88 : 1
            Behavior on scale { NumberAnimation { duration: 90 } }
        }
    }
    ToolTip.visible: hovered && tip !== ""
    ToolTip.delay: 450
    ToolTip.text: tip
}
