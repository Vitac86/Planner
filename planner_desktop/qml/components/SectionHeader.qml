import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Заголовок секции списка: название + необязательный счётчик.
RowLayout {
    id: header

    property string title: ""
    property int count: -1

    spacing: Theme.spacingSm

    Label {
        text: header.title
        font.pixelSize: Theme.fontSubtitle
        font.weight: Font.DemiBold
        color: Theme.textSecondary
    }

    Badge {
        visible: header.count >= 0
        text: header.count >= 0 ? String(header.count) : ""
        fg: Theme.textSecondary
        bg: Theme.surfacePressed
    }

    Item { Layout.fillWidth: true }
}
