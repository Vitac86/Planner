import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Заголовок секции списка: название + необязательный счётчик + слот
// действий справа (кладётся как дочерний элемент).
RowLayout {
    id: header

    property string title: ""
    property int count: -1
    default property alias actions: actionRow.data

    spacing: Theme.spacingSm

    Label {
        text: header.title.toUpperCase()
        font.pixelSize: Theme.fontCaption + 1
        font.family: Theme.fontFamily
        font.weight: Font.DemiBold
        font.letterSpacing: 0.6
        color: Theme.textMuted
    }

    Badge {
        visible: header.count >= 0
        text: header.count >= 0 ? String(header.count) : ""
        fg: Theme.textSecondary
        bg: Theme.surfacePressed
    }

    Item { Layout.fillWidth: true }

    RowLayout {
        id: actionRow
        spacing: Theme.spacingXs
        Layout.alignment: Qt.AlignVCenter
    }
}
