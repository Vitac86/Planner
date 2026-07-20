import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Список планов удалённого разделения (активные, конфликты, история).
// rows — settingsVm.remoteSplitRows; открытие никаких сетевых вызовов
// не делает.
ColumnLayout {
    id: history

    property var rows: []

    signal recoveryRequested(var plan)

    spacing: Theme.spacingSm

    Repeater {
        model: history.rows
        delegate: Rectangle {
            required property var modelData
            Layout.fillWidth: true
            implicitHeight: rowColumn.implicitHeight + 2 * Theme.spacingMd
            radius: Theme.radiusMedium
            color: Theme.surfaceHover
            border.color: Theme.border
            border.width: 1

            ColumnLayout {
                id: rowColumn
                anchors.fill: parent
                anchors.margins: Theme.spacingMd
                spacing: Theme.spacingXs

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingSm
                    Label {
                        text: modelData.seriesTitle || modelData.seriesUid
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        font.weight: Font.DemiBold
                        color: Theme.textPrimary
                        elide: Text.ElideRight
                        Layout.fillWidth: true
                    }
                    AppButton {
                        text: modelData.isActive || modelData.state === "conflict"
                              || modelData.state === "terminal_error"
                              ? "Действия" : "Детали"
                        variant: "ghost"
                        onClicked: history.recoveryRequested(modelData)
                    }
                }

                RemoteSeriesSplitProgress {
                    Layout.fillWidth: true
                    planData: modelData
                }

                Label {
                    text: "Слот: " + (modelData.targetSlot || "—")
                          + "  •  создан: " + (modelData.createdAt || "—")
                          + (modelData.completedAt
                             ? "  •  завершён: " + modelData.completedAt : "")
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    color: Theme.textMuted
                    elide: Text.ElideRight
                    Layout.fillWidth: true
                }
            }
        }
    }

    Label {
        visible: !history.rows || history.rows.length === 0
        text: "Планов удалённого разделения нет."
        font.pixelSize: Theme.fontCaption
        font.family: Theme.fontFamily
        color: Theme.textMuted
        Layout.fillWidth: true
    }
}
