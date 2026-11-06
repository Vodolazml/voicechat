import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Pick a whole screen or one window, see a live preview of exactly what friends will get, pick quality.
Modal {
    id: dlg
    title: "Показать экран"
    subtitle: "Трансляция зашифрована сквозным шифрованием, как и голос. Курсор мыши тоже виден."
    width: Math.min(720, parent ? parent.width - 48 : 720)
    property var monitors: []
    property var windows: []
    property string kind: "monitor"                 // monitor | window
    property string ident: String(backend.settings.screen_monitor || 1)
    property string preset: backend.settings.screen_preset || "1080p60"
    property var presets: []
    property int tick: 0
    onAboutToShow: {
        monitors = backend.monitors()
        windows = backend.windows()
        presets = backend.sharePresets()
        if (!presets.some(p => p.key === preset) && presets.length)
            preset = presets[presets.length - 1].key
        if (kind === "window" && !windows.some(w => String(w.hwnd) === ident)) {
            kind = "monitor"; ident = String(backend.settings.screen_monitor || 1)
        }
        tick++
    }
    // The preview refreshes while the dialog is open, so you see what is on that screen or window right now.
    Timer { interval: 900; repeat: true; running: dlg.visible; onTriggered: dlg.tick++ }

    function shot(kind, id) { return "image://shot/" + kind + "/" + id + "/" + dlg.tick }

    RowLayout {
        Layout.fillWidth: true; spacing: 8
        Repeater {
            model: [{key: "monitor", title: "Экран целиком", icon: "monitor"}, {key: "window", title: "Окно", icon: "window"}]
            delegate: Rectangle {
                height: 34; radius: 17; width: tabRow.implicitWidth + 28
                color: dlg.kind === modelData.key ? Theme.accent : Theme.card
                RowLayout {
                    id: tabRow; anchors.centerIn: parent; spacing: 6
                    Icon { name: modelData.icon; color: "white"; width: 16; height: 16 }
                    Text { text: modelData.title; color: "white"; font.family: Theme.font; font.pixelSize: 13 }
                }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        if (dlg.kind === modelData.key) return
                        dlg.kind = modelData.key
                        if (modelData.key === "monitor") dlg.ident = String(dlg.monitors.length ? dlg.monitors[0].index : 1)
                        else { dlg.windows = backend.windows(); dlg.ident = dlg.windows.length ? String(dlg.windows[0].hwnd) : "" }
                    }
                }
            }
        }
        Item { Layout.fillWidth: true }
        IconButton { visible: dlg.kind === "window"; glyph: "refresh"; size: 30; tip: "Обновить список окон"; onClicked: dlg.windows = backend.windows() }
    }

    // Sources
    Flickable {
        Layout.fillWidth: true
        Layout.preferredHeight: 150
        contentWidth: sources.implicitWidth
        clip: true
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }
        Row {
            id: sources
            spacing: 10
            Repeater {
                model: dlg.kind === "monitor" ? dlg.monitors : dlg.windows
                delegate: Rectangle {
                    readonly property string itemId: dlg.kind === "monitor" ? String(modelData.index) : String(modelData.hwnd)
                    readonly property bool chosen: itemId === dlg.ident
                    width: 200; height: 140; radius: 12
                    color: Theme.bg
                    border.width: 2
                    border.color: chosen ? Theme.accent : (srcHover.hovered ? Theme.line : "transparent")
                    Image {
                        id: thumb
                        x: 6; y: 6; width: parent.width - 12; height: 100
                        fillMode: Image.PreserveAspectFit
                        sourceSize.width: 320
                        cache: false
                        asynchronous: true
                        source: modelData.minimized ? "" : dlg.shot(dlg.kind, parent.itemId).replace(/\/\d+$/, "/0")
                    }
                    Icon {
                        anchors.horizontalCenter: thumb.horizontalCenter; anchors.verticalCenter: thumb.verticalCenter
                        visible: thumb.status !== Image.Ready
                        name: dlg.kind === "monitor" ? "monitor" : "window"; color: Theme.faint; width: 34; height: 34
                    }
                    Text {
                        x: 10; y: 110; width: parent.width - 20
                        text: dlg.kind === "monitor" ? modelData.title : (modelData.app ? modelData.app + " · " : "") + modelData.title
                        elide: Text.ElideRight
                        color: chosen ? Theme.text : Theme.muted; font.family: Theme.font; font.pixelSize: 12
                    }
                    HoverHandler { id: srcHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: dlg.ident = parent.itemId }
                }
            }
        }
    }
    Text {
        visible: dlg.kind === "window" && dlg.windows.length === 0
        text: "Открытых окон не нашлось. Нажмите «обновить», когда откроете нужное."
        color: Theme.faint; font.family: Theme.font; font.pixelSize: 12
    }

    // Live preview of the chosen source
    Text { text: "ПРЕДПРОСМОТР"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
    Rectangle {
        Layout.fillWidth: true
        Layout.preferredHeight: Math.min(260, width * 9 / 16)
        radius: 12; color: "black"; clip: true
        Image {
            id: preview
            anchors.fill: parent; anchors.margins: 2
            fillMode: Image.PreserveAspectFit
            sourceSize.width: 960
            cache: false
            asynchronous: true
            source: dlg.visible && dlg.ident !== "" ? dlg.shot(dlg.kind, dlg.ident) : ""
            // keep the last picture on screen while the next one loads, so it never blinks
            property url shown
            onStatusChanged: if (status === Image.Ready) shown = source
        }
        Text {
            anchors.centerIn: parent
            visible: preview.status !== Image.Ready && !preview.shown.toString()
            text: dlg.ident === "" ? "Выберите экран или окно" : "Загружаю…"
            color: Theme.faint; font.family: Theme.font; font.pixelSize: 13
        }
    }

    Text { text: "КАЧЕСТВО"; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold }
    Flow {
        Layout.fillWidth: true; spacing: 8
        Repeater {
            model: dlg.presets
            delegate: Rectangle {
                height: 34; radius: 17; width: t.implicitWidth + 28
                color: modelData.key === dlg.preset ? Theme.accent : Theme.card
                Text { id: t; anchors.centerIn: parent; text: modelData.title; color: "white"; font.family: Theme.font; font.pixelSize: 13 }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: dlg.preset = modelData.key }
            }
        }
    }
    Text {
        Layout.fillWidth: true; wrapMode: Text.WordWrap
        text: (backend.spaceShareQuality !== "2160p30" ? "Лучшее качество в этом пространстве ограничено его настройками. " : "")
              + (dlg.kind === "window" ? "Окно показывается таким, как оно видно на экране: если его перекрыть другим окном, зрители увидят перекрытие." : "Для игр в 60 к/с лучше запускать их в оконном режиме без рамки.")
        color: Theme.faint; font.family: Theme.font; font.pixelSize: 12
    }
    PrimaryButton {
        Layout.fillWidth: true
        text: dlg.kind === "window" ? "Показать это окно" : "Показать этот экран"
        enabled: dlg.ident !== ""
        onClicked: { backend.startShare(dlg.kind, dlg.ident, dlg.preset); dlg.close() }
    }
}
