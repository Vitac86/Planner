import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Единая шапка страницы: крупный заголовок + подзаголовок слева и слот
// действий справа (дочерние элементы кладутся в правый ряд).
RowLayout {
    id: header

    property string title: ""
    property string subtitle: ""
    default property alias actions: actionRow.data

    spacing: Theme.spacingMd

    ColumnLayout {
        spacing: 2
        Layout.alignment: Qt.AlignVCenter

        Label {
            text: header.title
            font.pixelSize: Theme.fontDisplay
            font.family: Theme.fontFamily
            font.weight: Font.DemiBold
            font.letterSpacing: -0.3
            color: Theme.textPrimary
        }
        Label {
            visible: header.subtitle.length > 0
            text: header.subtitle
            font.pixelSize: Theme.fontBody
            font.family: Theme.fontFamily
            color: Theme.textMuted
        }
    }

    Item { Layout.fillWidth: true }

    RowLayout {
        id: actionRow
        spacing: Theme.spacingSm
        Layout.alignment: Qt.AlignVCenter
    }
}
