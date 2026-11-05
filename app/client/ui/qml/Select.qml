import QtQuick
import QtQuick.Controls.Basic
import QtQuick.Layouts

// Labelled dropdown. `options` is a list of {value, title}.
ColumnLayout {
    id: root
    property string title: ""
    property var options: []
    property var current
    signal picked(var value)
    spacing: 6
    Layout.fillWidth: true

    Text { visible: root.title !== ""; text: root.title; color: Theme.muted; font.family: Theme.font; font.pixelSize: 12; font.weight: Font.DemiBold; font.capitalization: Font.AllUppercase }
    ComboBox {
        id: box
        Layout.fillWidth: true
        model: root.options
        textRole: "title"
        valueRole: "value"
        currentIndex: { for (var i = 0; i < root.options.length; ++i) if (root.options[i].value === root.current) return i; return 0; }
        onActivated: root.picked(currentValue)
        font.family: Theme.font
        font.pixelSize: 14
        implicitHeight: 42
        contentItem: Text { leftPadding: 14; text: box.displayText; color: Theme.text; font: box.font; verticalAlignment: Text.AlignVCenter; elide: Text.ElideRight }
        indicator: Icon { name: "chevron"; color: Theme.muted; width: 18; height: 18; x: box.width - width - 12; y: (box.height - height) / 2 }
        background: Rectangle { radius: 10; color: Theme.bg; border.width: 1; border.color: box.popup.visible ? Theme.accent : Theme.line }
        delegate: ItemDelegate {
            width: box.width
            highlighted: box.highlightedIndex === index
            contentItem: Text { text: modelData.title; color: Theme.text; font: box.font; elide: Text.ElideRight }
            background: Rectangle { color: highlighted ? Theme.accentSoft : "transparent"; radius: 8 }
        }
        popup: Popup {
            y: box.height + 4; width: box.width; padding: 6
            implicitHeight: Math.min(contentItem.implicitHeight + 12, 320)
            contentItem: ListView { clip: true; implicitHeight: contentHeight; model: box.popup.visible ? box.delegateModel : null; currentIndex: box.highlightedIndex }
            background: Rectangle { color: Theme.card; radius: 10; border.color: Theme.line }
        }
    }
}
