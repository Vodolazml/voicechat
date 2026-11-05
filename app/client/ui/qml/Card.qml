import QtQuick
import QtQuick.Layouts

Rectangle {
    default property alias content: inner.data
    Layout.fillWidth: true
    implicitHeight: inner.implicitHeight + 32
    radius: 14
    color: Theme.panel2
    border.color: Qt.rgba(1, 1, 1, 0.04)
    ColumnLayout { id: inner; anchors.fill: parent; anchors.margins: 16 }
}
