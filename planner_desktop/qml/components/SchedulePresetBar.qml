import QtQuick
import QtQuick.Controls

import "../theme"

// Полоса быстрых пресетов планирования в редакторе задачи:
// «Сегодня» / «Завтра» / «Следующий понедельник» / «Без даты» /
// «+1 час» / «На вечер». Список и подписи приходят из Python
// (vm.editorPresets), семантика — domain/scheduling.py; компонент
// только рисует чипы и шлёт triggered(presetId).
Flow {
    id: bar

    // [{id, label}] из vm.editorPresets.
    property var presets: []
    // «+1 час» имеет смысл только в режиме «Со временем» с временем.
    property bool plusHourEnabled: true

    signal triggered(string presetId)

    spacing: Theme.spacingXs + 2

    Repeater {
        model: bar.presets
        delegate: Rectangle {
            id: chip
            required property var modelData

            readonly property bool chipEnabled:
                bar.enabled && (modelData.id !== "plus_hour" || bar.plusHourEnabled)

            implicitHeight: 30
            implicitWidth: chipLabel.implicitWidth + 24
            radius: Theme.radiusPill
            color: chipHover.hovered && chipEnabled
                   ? Theme.accentSoft : Theme.surface
            border.color: chipHover.hovered && chipEnabled
                          ? Theme.accentSoftBorder : Theme.border
            border.width: 1
            opacity: chipEnabled ? 1.0 : 0.45
            Behavior on color { ColorAnimation { duration: 90 } }

            Label {
                id: chipLabel
                anchors.centerIn: parent
                text: chip.modelData.label
                font.pixelSize: Theme.fontCaption + 1
                font.family: Theme.fontFamily
                font.weight: Font.Medium
                color: chipHover.hovered && chip.chipEnabled
                       ? Theme.accent : Theme.textSecondary
            }

            HoverHandler {
                id: chipHover
                cursorShape: chip.chipEnabled ? Qt.PointingHandCursor
                                              : Qt.ArrowCursor
            }
            TapHandler {
                enabled: chip.chipEnabled
                onTapped: bar.triggered(chip.modelData.id)
            }

            activeFocusOnTab: chipEnabled
            Keys.onReturnPressed: if (chipEnabled) bar.triggered(chip.modelData.id)
            Keys.onSpacePressed: if (chipEnabled) bar.triggered(chip.modelData.id)
            Accessible.role: Accessible.Button
            Accessible.name: chip.modelData.label
            Accessible.focusable: chip.chipEnabled
            Rectangle {
                anchors.fill: parent
                anchors.margins: -2
                radius: parent.radius
                color: "transparent"
                border.color: Theme.focusRing
                border.width: 2
                visible: chip.activeFocus
            }
        }
    }
}
