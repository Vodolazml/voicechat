import QtQuick
import QtQuick.Controls.Basic

Button {
    id: control
    property bool secondary: false
    property bool danger: false
    property bool loading: false
    hoverEnabled: true
    font.family: Theme.font
    font.pixelSize: 14
    font.weight: Font.DemiBold
    implicitHeight: 42
    leftPadding: 18; rightPadding: 18
    contentItem: Item {
        implicitWidth: label.implicitWidth
        implicitHeight: label.implicitHeight
        Text {
            id: label
            anchors.centerIn: parent
            text: control.text
            font: control.font
            color: control.secondary ? Theme.text : "white"
            opacity: control.loading ? 0 : (control.enabled ? 1 : 0.5)
        }
        Spinner { anchors.centerIn: parent; visible: control.loading; size: 18 }
    }
    background: Rectangle {
        radius: 10
        color: control.secondary ? (control.hovered ? Theme.cardHover : Theme.card) : "transparent"
        gradient: control.secondary ? null : grad
        opacity: control.enabled ? 1 : 0.55
        scale: control.down ? 0.98 : 1
        Behavior on scale { NumberAnimation { duration: 90 } }
        Gradient {
            id: grad
            orientation: Gradient.Horizontal
            GradientStop { position: 0; color: control.danger ? Theme.bad : (control.hovered ? Qt.lighter(Theme.accent, 1.12) : Theme.accent) }
            GradientStop { position: 1; color: control.danger ? Qt.darker(Theme.bad, 1.2) : (control.hovered ? Qt.lighter("#c03fff", 1.1) : "#a347ff") }
        }
    }
}
