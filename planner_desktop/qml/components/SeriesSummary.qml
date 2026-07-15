import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Компактная человекочитаемая сводка правила серии:
// «Каждую неделю: Пн, Ср в 09:00, до 31.12.2026».
RowLayout {
    id: summaryRow

    property string summary: ""
    property bool isError: false

    visible: summary.length > 0
    spacing: Theme.spacingXs

    Accessible.role: Accessible.StaticText
    Accessible.name: "Повторение: " + summary

    AppIcon {
        name: summaryRow.isError ? "info" : "repeat"
        size: 14
        color: summaryRow.isError ? Theme.danger : Theme.textSecondary
        Layout.alignment: Qt.AlignTop
    }
    Label {
        text: summaryRow.summary
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        color: summaryRow.isError ? Theme.danger : Theme.textSecondary
        wrapMode: Text.WordWrap
        Layout.fillWidth: true
    }
}
