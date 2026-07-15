import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "."
import "../theme"

Panel {
    id: root
    property var seriesData: ({})
    property bool compact: false
    Layout.fillWidth: true
    implicitHeight: content.implicitHeight + 2 * Theme.spacingMd

    ColumnLayout {
        id: content
        anchors.fill: parent
        anchors.margins: Theme.spacingMd
        spacing: Theme.spacingSm

        RowLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: Theme.spacingSm
            ColumnLayout {
                Layout.fillWidth: true
                Layout.minimumWidth: 0
                spacing: 2
                Label {
                    text: root.seriesData.title || "(без названия)"
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                    font.weight: Font.DemiBold
                    color: Theme.textPrimary
                    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                    Accessible.name: "Серия Google: " + text
                }
                Label {
                    text: root.seriesData.recurrenceSummary || "—"
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    color: root.seriesData.supportStatus === "supported"
                           ? Theme.textSecondary : Theme.warningText
                    wrapMode: Text.WrapAtWordBoundaryOrAnywhere
                    Layout.fillWidth: true
                    Layout.minimumWidth: 0
                }
            }
            RecurrenceSupportBadge {
                supported: root.seriesData.supportStatus === "supported"
                cancelled: Boolean(root.seriesData.cancelled)
                Layout.alignment: Qt.AlignTop
            }
            Badge {
                text: root.seriesData.ownershipText || "Внешняя серия"
                fg: root.seriesData.plannerOwned ? Theme.accent : Theme.textSecondary
                bg: root.seriesData.plannerOwned ? Theme.accentSoft : Theme.surfaceMuted
                Layout.alignment: Qt.AlignTop
                Accessible.name: text
            }
        }

        ExternalSeriesDetails {
            seriesData: root.seriesData
            compact: root.compact
        }
    }
}
