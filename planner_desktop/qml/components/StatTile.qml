import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Компактная KPI-плитка для шапки «Сегодня»: иконка в цветном кружке,
// крупное число и подпись. Использует Panel (тень/рамка дизайн-системы).
Panel {
    id: tile

    property string value: "0"
    property string caption: ""
    property string iconName: "circle"
    property color accentColor: Theme.accent
    property color tintColor: Theme.accentSoft

    implicitWidth: Math.max(row.implicitWidth + 2 * Theme.spacingLg, 150)
    implicitHeight: 74

    RowLayout {
        id: row
        anchors.fill: parent
        anchors.leftMargin: Theme.spacingLg
        anchors.rightMargin: Theme.spacingLg
        spacing: Theme.spacingMd

        Rectangle {
            Layout.alignment: Qt.AlignVCenter
            implicitWidth: 38
            implicitHeight: 38
            radius: Theme.radiusSmall + 2
            color: tile.tintColor

            AppIcon {
                anchors.centerIn: parent
                name: tile.iconName
                color: tile.accentColor
                size: 20
            }
        }

        ColumnLayout {
            spacing: 0
            Layout.alignment: Qt.AlignVCenter

            Label {
                text: tile.value
                font.pixelSize: Theme.fontTitle + 2
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textPrimary
            }
            Label {
                text: tile.caption
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textMuted
            }
        }

        Item { Layout.fillWidth: true }
    }
}
