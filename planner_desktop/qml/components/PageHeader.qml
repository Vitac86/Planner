import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Единая шапка страницы: крупный заголовок + подзаголовок слева и слот
// действий справа (дочерние элементы кладутся в правый ряд).
GridLayout {
    id: header

    property string title: ""
    property string subtitle: ""
    // На compact-страницах действия переходят под заголовок вместо клипа.
    property bool stackActions: false
    default property alias actions: actionRow.data

    columns: stackActions ? 1 : 3
    columnSpacing: Theme.spacingMd
    rowSpacing: Theme.spacingSm

    ColumnLayout {
        Layout.row: 0
        Layout.column: 0
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        spacing: 2
        Layout.alignment: Qt.AlignVCenter

        Label {
            text: header.title
            font.pixelSize: Theme.fontDisplay
            font.family: Theme.fontFamily
            font.weight: Font.DemiBold
            font.letterSpacing: -0.3
            color: Theme.textPrimary
            elide: Text.ElideRight
            Layout.fillWidth: true
            Layout.minimumWidth: 0
        }
        Label {
            visible: header.subtitle.length > 0
            text: header.subtitle
            font.pixelSize: Theme.fontBody
            font.family: Theme.fontFamily
            color: Theme.textMuted
            wrapMode: Text.WrapAtWordBoundaryOrAnywhere
            maximumLineCount: header.stackActions ? 3 : 2
            elide: Text.ElideRight
            Layout.fillWidth: true
            Layout.minimumWidth: 0
        }
    }

    Item {
        visible: !header.stackActions
        Layout.row: 0
        Layout.column: 1
        Layout.fillWidth: true
        Layout.minimumWidth: 0
    }

    RowLayout {
        id: actionRow
        Layout.row: header.stackActions ? 1 : 0
        Layout.column: header.stackActions ? 0 : 2
        Layout.fillWidth: header.stackActions
        Layout.minimumWidth: 0
        spacing: Theme.spacingSm
        Layout.alignment: header.stackActions
                          ? Qt.AlignLeft : Qt.AlignVCenter | Qt.AlignRight
    }
}
