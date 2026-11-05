import QtQuick
import QtQuick.Layouts

Rectangle {
    color: Theme.bg
    ColumnLayout {
        anchors.centerIn: parent
        spacing: 12
        Rectangle {
            Layout.alignment: Qt.AlignHCenter
            width: 96; height: 96; radius: 32
            gradient: Gradient { orientation: Gradient.Horizontal
                GradientStop { position: 0; color: Theme.accent }
                GradientStop { position: 1; color: "#ff3d9a" } }
            Icon { anchors.centerIn: parent; name: "headset"; color: "white"; width: 48; height: 48 }
            SequentialAnimation on scale { loops: Animation.Infinite
                NumberAnimation { to: 1.05; duration: 1600; easing.type: Easing.InOutSine }
                NumberAnimation { to: 1.0; duration: 1600; easing.type: Easing.InOutSine } }
        }
        Text { Layout.alignment: Qt.AlignHCenter; text: "Заходите в голосовой канал"; color: Theme.text; font.family: Theme.font; font.pixelSize: 22; font.weight: Font.Bold }
        Text { Layout.alignment: Qt.AlignHCenter; text: "Один клик по каналу слева, и вы на связи. Друзья увидят вас сразу."; color: Theme.muted; font.family: Theme.font; font.pixelSize: 14 }
    }
}
