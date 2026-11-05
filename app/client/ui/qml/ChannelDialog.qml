import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

Modal {
    id: dlg
    property int channelId: 0          // 0 = create
    property string channelType: "voice"
    property alias name: nameField.text
    property int limit: 0
    title: channelId ? "Настройки канала" : (channelType === "voice" ? "Новый голосовой канал" : "Новый текстовый канал")

    Field { id: nameField; Layout.fillWidth: true; placeholderText: "Название"; onAccepted: dlg.save() }
    Select {
        visible: dlg.channelType === "voice"
        title: "Лимит участников"
        current: dlg.limit
        options: [{value: 0, title: "Без лимита"}, {value: 2, title: "2"}, {value: 3, title: "3"}, {value: 5, title: "5"}, {value: 10, title: "10"}, {value: 25, title: "25"}]
        onPicked: (v) => dlg.limit = v
    }
    Toggle { id: priv; visible: dlg.channelId === 0; title: "Приватный канал"; hint: "Виден только тем, кого вы добавите"; onToggled: (v) => checked = v }
    PrimaryButton { Layout.fillWidth: true; text: dlg.channelId ? "Сохранить" : "Создать"; enabled: nameField.text.trim().length >= 2; onClicked: dlg.save() }

    function openCreate(type) { channelId = 0; channelType = type; nameField.text = ""; limit = 0; priv.checked = false; open(); nameField.forceActiveFocus() }
    function openEdit(id, currentName, currentLimit) { channelId = id; channelType = "voice"; nameField.text = currentName; limit = currentLimit; open() }
    function save() {
        if (nameField.text.trim().length < 2) return
        if (channelId) backend.updateChannel(channelId, nameField.text, limit)
        else backend.createChannel(nameField.text, channelType, priv.checked, limit)
        close()
    }
}
