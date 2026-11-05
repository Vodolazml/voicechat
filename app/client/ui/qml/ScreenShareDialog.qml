import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Modal {
    id: dlg
    title: "Показать экран"
    subtitle: "Трансляция зашифрована сквозным шифрованием, как и голос."
    property var monitors: []
    property int monitor: backend.settings.screen_monitor || 1
    property string preset: backend.settings.screen_preset || "1080p60"
    onAboutToShow: monitors = backend.monitors()

    Text { text: "ЭКРАН"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
    Flow {
        Layout.fillWidth: true; spacing: 10
        Repeater {
            model: dlg.monitors
            delegate: Rectangle {
                width: 196; height: 112; radius: 12
                color: Theme.bg
                border.width: 2
                border.color: modelData.index === dlg.monitor ? Theme.accent : Theme.line
                ColumnLayout {
                    anchors.centerIn: parent; spacing: 6
                    Icon { Layout.alignment: Qt.AlignHCenter; name: "monitor"; color: modelData.index === dlg.monitor ? Theme.accent2 : Theme.muted; width: 40; height: 40 }
                    Text { Layout.alignment: Qt.AlignHCenter; text: modelData.title; color: Theme.text; font.family: Theme.font; font.pixelSize: 12 }
                }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: dlg.monitor = modelData.index }
            }
        }
    }
    Text { text: "КАЧЕСТВО"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
    Flow {
        Layout.fillWidth: true; spacing: 8
        Repeater {
            model: backend.screenPresets
            delegate: Rectangle {
                height: 34; radius: 17; width: t.implicitWidth + 28
                color: modelData.key === dlg.preset ? Theme.accent : Theme.card
                Text { id: t; anchors.centerIn: parent; text: modelData.title; color: "white"; font.family: Theme.font; font.pixelSize: 13 }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: dlg.preset = modelData.key }
            }
        }
    }
    Text { Layout.fillWidth: true; wrapMode: Text.WordWrap; text: "Для игр в 60 к/с лучше запускать их в оконном режиме без рамки. Высокие пресеты требуют быстрого интернета у вас и у зрителей."; color: Theme.faint; font.family: Theme.font; font.pixelSize: 12 }
    PrimaryButton { Layout.fillWidth: true; text: "Начать трансляцию"; enabled: dlg.monitors.length > 0; onClicked: { backend.startScreen(dlg.monitor, dlg.preset); dlg.close() } }
}
