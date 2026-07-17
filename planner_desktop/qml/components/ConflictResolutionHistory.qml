import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Local audit history of explicit conflict/remote-deleted resolutions.
// Read-only list; rows come from SettingsViewModel.resolutionHistoryRows.
ColumnLayout {
    id: history

    property var rows: []

    spacing: Theme.spacingSm

    Label {
        visible: history.rows.length === 0
        text: "Решений по конфликтам пока не было."
        font.pixelSize: Theme.fontBody
        font.family: Theme.fontFamily
        color: Theme.textMuted
        Layout.fillWidth: true
        wrapMode: Text.WordWrap
    }

    Repeater {
        model: history.rows
        delegate: Rectangle {
            id: historyRow
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: historyRowColumn.implicitHeight + 2 * Theme.spacingSm
            radius: Theme.radiusSmall
            color: Theme.surfaceMuted
            border.color: Theme.border
            border.width: 1
            Accessible.name: modelData.kindText + ". " + modelData.statusText

            ColumnLayout {
                id: historyRowColumn
                anchors.fill: parent
                anchors.margins: Theme.spacingSm
                spacing: 2

                Label {
                    text: historyRow.modelData.kindText
                          + " — " + historyRow.modelData.statusText
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                    font.weight: Font.Medium
                    color: Theme.textPrimary
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                }
                Label {
                    text: "Серия " + historyRow.modelData.seriesUid
                          + " · создано " + historyRow.modelData.createdAt
                          + (historyRow.modelData.completedAt !== "—"
                             ? " · завершено " + historyRow.modelData.completedAt
                             : "")
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    color: Theme.textSecondary
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                }
                Label {
                    visible: (historyRow.modelData.error || "").length > 0
                    text: historyRow.modelData.error
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    color: Theme.danger
                    Layout.fillWidth: true
                    wrapMode: Text.WordWrap
                    Accessible.role: Accessible.AlertMessage
                }
            }
        }
    }
}
