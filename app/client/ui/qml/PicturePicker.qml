import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Dialogs
import QtQuick.Layouts

// Picture with "Выбрать" / "Убрать" buttons: your avatar in the profile, a space's picture in its settings.
RowLayout {
    id: picker
    property string source: ""
    property string initials: "?"
    property color color: Theme.accent
    property bool square: false
    property int size: 72
    property bool editable: true
    property string hint: ""
    signal picked(string url)
    signal cleared()
    spacing: 16

    Item {
        width: picker.size; height: picker.size
        Avatar {
            visible: !picker.square
            anchors.fill: parent; size: picker.size
            initials: picker.initials; color: picker.color; source: picker.source
        }
        Rectangle {
            visible: picker.square
            anchors.fill: parent; radius: picker.size * 0.3
            gradient: Gradient {
                GradientStop { position: 0; color: Qt.lighter(picker.color, 1.15) }
                GradientStop { position: 1; color: Qt.darker(picker.color, 1.25) }
            }
            Text { anchors.centerIn: parent; visible: !img.visible; text: picker.initials; color: "white"; font.family: Theme.font; font.pixelSize: picker.size * 0.34; font.weight: Font.Bold }
            Image {
                id: img
                anchors.fill: parent
                visible: picker.source !== "" && status === Image.Ready
                source: picker.square ? picker.source : ""
                sourceSize.width: 256; sourceSize.height: 256
                smooth: true; mipmap: true; asynchronous: true
            }
        }
        Rectangle {  // camera badge: click the picture to change it
            visible: picker.editable
            anchors.right: parent.right; anchors.bottom: parent.bottom; anchors.margins: -2
            width: 26; height: 26; radius: 13; color: Theme.accent; border.width: 3; border.color: Theme.panel2
            Icon { anchors.centerIn: parent; name: "edit"; color: "white"; width: 13; height: 13 }
        }
        HoverHandler { enabled: picker.editable; cursorShape: Qt.PointingHandCursor }
        TapHandler { enabled: picker.editable; onTapped: dialog.open() }
    }
    ColumnLayout {
        Layout.fillWidth: true; spacing: 8
        Text { visible: picker.hint !== ""; Layout.fillWidth: true; wrapMode: Text.WordWrap; text: picker.hint; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12 }
        RowLayout {
            visible: picker.editable; spacing: 8
            PrimaryButton { secondary: true; implicitHeight: 32; font.pixelSize: 12; text: picker.source ? "Сменить картинку" : "Выбрать картинку"; onClicked: dialog.open() }
            PrimaryButton { visible: picker.source !== ""; secondary: true; implicitHeight: 32; font.pixelSize: 12; text: "Убрать"; onClicked: picker.cleared() }
        }
    }

    FileDialog {
        id: dialog
        title: "Выберите картинку"
        nameFilters: ["Картинки (*.png *.jpg *.jpeg *.bmp *.webp *.gif)"]
        onAccepted: picker.picked(selectedFile.toString())
    }
}
