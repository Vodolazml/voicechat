import QtQuick
import QtQuick.Controls.Basic

Menu {
    id: menu
    padding: 6
    font.family: Theme.font
    font.pixelSize: 13
    background: Rectangle { implicitWidth: 220; color: "#0f1016"; radius: 10; border.color: Theme.line }
    delegate: MenuItem {
        id: item
        implicitHeight: 34
        contentItem: Text {
            leftPadding: 8
            text: item.text
            font: menu.font
            color: item.text.indexOf("Удалить") === 0 || item.text.indexOf("Отключить") === 0 ? Theme.bad : (item.highlighted ? "white" : Theme.text)
            verticalAlignment: Text.AlignVCenter
        }
        background: Rectangle { radius: 6; color: item.highlighted ? Theme.accent : "transparent" }
        arrow: Icon { visible: item.subMenu; name: "chevron"; rotation: -90; color: Theme.muted; width: 16; height: 16; x: item.width - width - 8; y: (item.height - height) / 2 }
    }
}
