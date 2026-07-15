import QtQuick
import QtQuick.Controls

import "../theme"

// Пресеты правила повторения: «Каждый день», «По будням», «Каждую неделю»,
// «Каждый месяц», «Каждый год», «Настроить…». Полностью клавиатурные
// (Tab + Enter/Space), выбор объявляется через Accessible.
Flow {
    id: bar

    // [{id, label}] из vm.recurrencePresets.
    property var presets: []
    property string currentPreset: ""

    signal triggered(string presetId)

    spacing: Theme.spacingXs

    Repeater {
        model: bar.presets

        delegate: Rectangle {
            id: chip
            required property var modelData

            readonly property bool active: bar.currentPreset === modelData.id
            implicitHeight: 28
            implicitWidth: chipLabel.implicitWidth + 20
            radius: Theme.radiusPill
            color: chip.active ? Theme.accentSoft
                 : chipHover.hovered ? Theme.surfaceHover : Theme.surface
            border.color: chip.active ? Theme.accent : Theme.border
            border.width: 1
            Behavior on color { ColorAnimation { duration: 90 } }

            activeFocusOnTab: true
            Accessible.role: Accessible.RadioButton
            Accessible.name: "Повторение: " + modelData.label
            Accessible.checked: chip.active
            Accessible.focusable: true

            Label {
                id: chipLabel
                anchors.centerIn: parent
                text: chip.modelData.label
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                font.weight: chip.active ? Font.DemiBold : Font.Medium
                color: chip.active ? Theme.accent : Theme.textSecondary
            }

            HoverHandler { id: chipHover; cursorShape: Qt.PointingHandCursor }
            TapHandler {
                onTapped: {
                    chip.forceActiveFocus()
                    bar.triggered(chip.modelData.id)
                }
            }
            Keys.onReturnPressed: bar.triggered(chip.modelData.id)
            Keys.onSpacePressed: bar.triggered(chip.modelData.id)

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
