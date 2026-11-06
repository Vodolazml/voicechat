import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Modal {
    id: dlg
    title: "Коды безопасности"
    subtitle: "Сверьте код голосом с каждым другом. Если цифры совпадают, между вами никого нет: ни сервер, ни кто-то ещё не может слушать разговор."
    onAboutToShow: backend.loadSafetyCodes()

    Text { visible: backend.safetyCodes.length === 0; text: "В канале пока нет других участников."; color: Theme.muted; font.family: Theme.font; font.pixelSize: 13 }
    Repeater {
        model: backend.safetyCodes
        delegate: Rectangle {
            Layout.fillWidth: true; height: 64; radius: 12
            color: Theme.bg; border.color: modelData.changed ? Theme.warn : Theme.line
            RowLayout {
                anchors.fill: parent; anchors.margins: 12; spacing: 10
                ColumnLayout {
                    Layout.fillWidth: true; spacing: 2
                    Text { text: modelData.name + (modelData.changed ? " · ключ сменился" : ""); color: modelData.changed ? Theme.warn : Theme.text; font.family: Theme.font; font.pixelSize: 13; font.weight: Font.DemiBold }
                    Text { text: modelData.code; color: Theme.accent2; font.family: "Consolas"; font.pixelSize: 16; font.letterSpacing: 1 }
                }
                PrimaryButton { visible: modelData.changed; implicitHeight: 32; text: "Совпадает"; onClicked: backend.confirmSafety(modelData.id) }
            }
        }
    }
    Text { text: "Ваш отпечаток: " + backend.myFingerprint; color: Theme.faint; font.family: "Consolas"; font.pixelSize: 11; Layout.fillWidth: true; elide: Text.ElideMiddle }
}
