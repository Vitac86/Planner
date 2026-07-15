import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

ColumnLayout {
    id: root
    property var seriesData: ({})
    property bool compact: false
    spacing: Theme.spacingSm
    Layout.fillWidth: true
    Layout.minimumWidth: 0

    GridLayout {
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        columns: root.compact ? 1 : 2
        columnSpacing: Theme.spacingLg
        rowSpacing: Theme.spacingXs

        component KeyLabel: Label {
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textMuted
            wrapMode: Text.WordWrap
        }
        component ValueLabel: Label {
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textPrimary
            wrapMode: Text.WrapAtWordBoundaryOrAnywhere
            Layout.fillWidth: true
            Layout.minimumWidth: 0
        }

        KeyLabel { text: "Тип" }
        ValueLabel { text: root.seriesData.timingText || "—" }
        KeyLabel { text: "Часовой пояс" }
        ValueLabel { text: root.seriesData.timezoneName || "—" }
        KeyLabel { text: "Импортированных экземпляров" }
        ValueLabel { text: String(root.seriesData.importedInstanceCount || 0) }
        KeyLabel { text: "Последнее изменение Google" }
        ValueLabel { text: root.seriesData.lastRemoteUpdate || "—" }
    }

    Label {
        visible: (root.seriesData.unsupportedReason || "").length > 0
        text: "Причина: " + root.seriesData.unsupportedReason
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        color: Theme.warningText
        wrapMode: Text.WrapAtWordBoundaryOrAnywhere
        Layout.fillWidth: true
        Layout.minimumWidth: 0
    }

    ColumnLayout {
        visible: (root.seriesData.rawRecurrence || "").length > 0
        Layout.fillWidth: true
        Layout.minimumWidth: 0
        spacing: Theme.spacingXs
        Label {
            text: "Исходное правило (можно выделить и скопировать)"
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        TextArea {
            objectName: "externalSeriesRawRecurrence"
            text: root.seriesData.rawRecurrence || ""
            readOnly: true
            selectByMouse: true
            wrapMode: TextEdit.WrapAnywhere
            font.pixelSize: Theme.fontCaption
            font.family: "Consolas"
            color: Theme.textPrimary
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            implicitHeight: Math.min(96, Math.max(42, contentHeight + 12))
            background: Rectangle {
                radius: Theme.radiusSmall
                color: Theme.surfaceMuted
                border.color: Theme.border
                border.width: 1
            }
            Accessible.name: "Исходное правило повторения Google"
        }
    }
}
