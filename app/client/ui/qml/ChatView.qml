import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Rectangle {
    id: root
    color: Theme.bg
    property bool compactHeader: false
    property int editingId: 0

    ColumnLayout {
        anchors.fill: parent
        spacing: 0

        Rectangle {
            visible: !root.compactHeader
            Layout.fillWidth: true; Layout.preferredHeight: 52; color: "transparent"
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 18; anchors.rightMargin: 12; spacing: 10
                Icon { name: "hash"; color: Theme.faint; width: 22; height: 22 }
                Text { text: backend.chatChannelName; color: Theme.text; font.family: Theme.font; font.pixelSize: 16; font.weight: Font.Bold }
                Rectangle { width: 1; height: 20; color: Theme.line }
                Text { Layout.fillWidth: true; text: "Сообщения хранятся на сервере зашифрованными"; color: Theme.faint; elide: Text.ElideRight; font.family: Theme.font; font.pixelSize: 12 }
            }
            Rectangle { anchors.bottom: parent.bottom; width: parent.width; height: 1; color: Theme.line }
        }

        ListView {
            id: messages
            Layout.fillWidth: true; Layout.fillHeight: true
            clip: true
            model: backend.messagesModel
            spacing: 0
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { width: 8 }
            property bool stick: true
            property int olderFrom: -1
            onMovementEnded: stick = atYEnd
            onCountChanged: {
                if (olderFrom >= 0) {
                    // keep the message the user was reading in place after older ones load above it
                    var shift = count - olderFrom
                    olderFrom = -1
                    Qt.callLater(function() { messages.positionViewAtIndex(shift, ListView.Beginning) })
                } else if (stick) {
                    Qt.callLater(positionViewAtEnd)
                }
            }
            onAtYBeginningChanged: if (atYBeginning && count > 0 && backend.hasMoreMessages && !stick) {
                olderFrom = count
                backend.loadOlder()
            }
            Connections { target: backend; function onChatChanged() { messages.stick = true; Qt.callLater(messages.positionViewAtEnd) } }

            header: Item {
                width: messages.width; height: backend.hasMoreMessages ? 40 : (root.compactHeader ? 110 : 168)
                ColumnLayout {
                    visible: !backend.hasMoreMessages
                    anchors.left: parent.left; anchors.leftMargin: 18; anchors.right: parent.right; anchors.rightMargin: 18
                    anchors.bottom: parent.bottom; anchors.bottomMargin: 14
                    spacing: 6
                    Rectangle {
                        visible: !root.compactHeader
                        width: 60; height: 60; radius: 20
                        gradient: Gradient {
                            GradientStop { position: 0; color: Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.35) }
                            GradientStop { position: 1; color: Qt.rgba(1, 0.31, 0.6, 0.22) }
                        }
                        border.width: 1; border.color: Qt.rgba(1, 1, 1, 0.08)
                        Icon { anchors.centerIn: parent; name: "hash"; color: "white"; width: 30; height: 30 }
                    }
                    Text { Layout.topMargin: 4; text: "Добро пожаловать в #" + backend.chatChannelName; color: Theme.text; font.family: Theme.font; font.pixelSize: root.compactHeader ? 16 : 22; font.weight: Font.Bold; elide: Text.ElideRight; Layout.fillWidth: true }
                    Text { text: "Это начало канала. Сообщения видят все, у кого есть к нему доступ."; color: Theme.muted; font.family: Theme.font; font.pixelSize: 13; wrapMode: Text.WordWrap; Layout.fillWidth: true }
                }
                Spinner { visible: backend.hasMoreMessages; anchors.centerIn: parent; color: Theme.muted; size: 18 }
            }

            delegate: Column {
                id: msg
                width: messages.width
                required property var model
                required property int index

                Item {
                    visible: msg.model.showDay
                    width: parent.width; height: 40
                    Rectangle { anchors.verticalCenter: parent.verticalCenter; x: 16; width: parent.width - 32; height: 1; color: Theme.line }
                    Rectangle {
                        anchors.centerIn: parent; color: root.color; width: dayText.implicitWidth + 24; height: 22; radius: 11
                        border.width: 1; border.color: Theme.line
                        Text { id: dayText; anchors.centerIn: parent; text: msg.model.day; color: Theme.muted; font.family: Theme.font; font.pixelSize: 11; font.weight: Font.DemiBold }
                    }
                }

                Rectangle {
                    width: parent.width
                    height: body.implicitHeight + (msg.model.grouped ? 6 : 14)
                    color: msg.model.mention ? Qt.rgba(1, 0.82, 0.4, 0.07) : (hover.hovered ? Qt.rgba(1, 1, 1, 0.025) : "transparent")
                    Rectangle { visible: msg.model.mention; width: 3; height: parent.height; color: "#ffd166" }

                    Avatar {
                        visible: !msg.model.grouped
                        x: 16; y: 8; size: 38
                        initials: msg.model.initials; color: msg.model.color; source: msg.model.avatar || ""
                    }
                    Text {
                        visible: msg.model.grouped && hover.hovered
                        x: 16; width: 38; y: 5; horizontalAlignment: Text.AlignHCenter
                        text: msg.model.time; color: Theme.faint; font.family: Theme.font; font.pixelSize: 10
                    }

                    Column {
                        id: body
                        x: 68; y: msg.model.grouped ? 3 : 8
                        width: parent.width - 68 - 16
                        spacing: 2
                        Row {
                            visible: !msg.model.grouped
                            spacing: 8
                            Text { text: msg.model.author; color: msg.model.color; font.family: Theme.font; font.pixelSize: 15; font.weight: Font.DemiBold }
                            Text { text: msg.model.time; color: Theme.faint; font.family: Theme.font; font.pixelSize: 11; anchors.baseline: parent.children[0].baseline }
                        }
                        Text {
                            visible: root.editingId !== msg.model.id
                            width: parent.width
                            text: msg.model.deleted ? "<i>сообщение удалено</i>" : msg.model.html + (msg.model.edited ? " <font color='#686a82' size='2'>(изменено)</font>" : "")
                            textFormat: Text.RichText
                            wrapMode: Text.Wrap
                            color: msg.model.deleted ? Theme.faint : Theme.text
                            linkColor: "#6db3ff"
                            font.family: Theme.font; font.pixelSize: 15
                            lineHeight: 1.2
                            onLinkActivated: (link) => backend.openLink(link)
                            HoverHandler { cursorShape: parent.hoveredLink ? Qt.PointingHandCursor : Qt.IBeamCursor }
                        }
                        Field {
                            id: editField
                            visible: root.editingId === msg.model.id
                            width: parent.width
                            text: msg.model.raw
                            onVisibleChanged: if (visible) { forceActiveFocus(); selectAll() }
                            onAccepted: { backend.editMessage(msg.model.id, text); root.editingId = 0 }
                            Keys.onEscapePressed: root.editingId = 0
                        }
                    }

                    Row {
                        visible: hover.hovered && !msg.model.deleted && root.editingId !== msg.model.id
                        anchors.right: parent.right; anchors.rightMargin: 16; y: -10
                        spacing: 2
                        Rectangle {
                            width: actions.implicitWidth + 8; height: 32; radius: 8; color: Theme.card; border.color: Theme.line
                            Row {
                                id: actions; anchors.centerIn: parent; spacing: 2
                                IconButton { size: 26; glyph: "copy"; tip: "Копировать"; onClicked: backend.copyText(msg.model.raw) }
                                IconButton { visible: msg.model.mine; size: 26; glyph: "edit"; tip: "Изменить"; onClicked: root.editingId = msg.model.id }
                                IconButton { visible: msg.model.mine || backend.canModerate; size: 26; glyph: "delete"; danger: true; tip: "Удалить"; onClicked: backend.deleteMessage(msg.model.id) }
                            }
                        }
                    }
                    HoverHandler { id: hover }
                }
            }
        }

        // Composer
        Rectangle {
            Layout.fillWidth: true
            Layout.margins: 14
            Layout.topMargin: 4
            Layout.preferredHeight: Math.min(160, input.implicitHeight + 20)
            radius: 12
            color: Theme.panel2
            border.color: input.activeFocus ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.6) : "transparent"
            RowLayout {
                anchors.fill: parent; anchors.leftMargin: 14; anchors.rightMargin: 6; spacing: 6
                ScrollView {
                    Layout.fillWidth: true; Layout.fillHeight: true
                    TextArea {
                        id: input
                        placeholderText: "Написать в #" + backend.chatChannelName
                        placeholderTextColor: Theme.faint
                        color: Theme.text
                        wrapMode: TextEdit.Wrap
                        font.family: Theme.font; font.pixelSize: 14
                        verticalAlignment: TextEdit.AlignVCenter
                        background: null
                        selectionColor: Theme.accent
                        Keys.onReturnPressed: (event) => {
                            if (event.modifiers & Qt.ShiftModifier) { event.accepted = false; return }
                            root.send()
                        }
                        Keys.onEnterPressed: root.send()
                        onActiveFocusChanged: if (activeFocus) backend.markChatRead()
                    }
                }
                IconButton { glyph: "send"; tip: "Отправить (Enter)"; active: input.text.trim().length > 0; onClicked: root.send() }
            }
        }
    }

    function send() {
        if (input.text.trim().length === 0) return
        backend.sendMessage(input.text)
        input.text = ""
        messages.stick = true
    }
}
