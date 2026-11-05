import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

ColumnLayout {
    id: root
    property string title: ""
    property real from: 0
    property real to: 1
    property real value: 0
    property real stepSize: 0.01
    property string suffix: "%"
    property real displayScale: 100
    signal moved(real value)
    spacing: 6
    Layout.fillWidth: true

    RowLayout {
        Layout.fillWidth: true
        Text { text: root.title; color: Theme.text; font.family: Theme.font; font.pixelSize: 14; Layout.fillWidth: true }
        Text { text: Math.round(slider.value * root.displayScale) + root.suffix; color: Theme.muted; font.family: Theme.font; font.pixelSize: 13 }
    }
    Slider {
        id: slider
        Layout.fillWidth: true
        from: root.from; to: root.to; value: root.value; stepSize: root.stepSize
        onMoved: root.moved(value)
        background: Rectangle {
            x: slider.leftPadding; y: slider.topPadding + slider.availableHeight / 2 - 3
            width: slider.availableWidth; height: 6; radius: 3
            color: Theme.line
            Rectangle {
                width: slider.visualPosition * parent.width; height: parent.height; radius: 3
                gradient: Gradient { orientation: Gradient.Horizontal
                    GradientStop { position: 0; color: Theme.accent }
                    GradientStop { position: 1; color: "#c03fff" } }
            }
        }
        handle: Rectangle {
            x: slider.leftPadding + slider.visualPosition * (slider.availableWidth - width)
            y: slider.topPadding + slider.availableHeight / 2 - height / 2
            width: 18; height: 18; radius: 9; color: "white"
            scale: slider.pressed ? 1.15 : 1
            Behavior on scale { NumberAnimation { duration: 90 } }
        }
    }
}
