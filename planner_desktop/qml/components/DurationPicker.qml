import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Выбор длительности: чипы-пресеты (15/30/45/60/90/120 минут из Python,
// vm.durationPresets) + «Другая…» с числовым полем. Значение — durationText
// в минутах (строка, "" = длительность по умолчанию в Python).
Flow {
    id: picker

    // [{minutes, label}] — прокидывается из ViewModel (vm.durationPresets).
    property var presets: []
    property string durationText: ""
    property bool customVisible: false

    readonly property int _current: {
        var v = parseInt(durationText, 10)
        return isNaN(v) ? -1 : v
    }
    readonly property bool _isPresetValue: {
        for (var i = 0; i < presets.length; i++)
            if (presets[i].minutes === _current) return true
        return false
    }

    spacing: Theme.spacingXs + 2

    function reset(text) {
        durationText = text || ""
        customVisible = durationText.length > 0 && !_isPresetValue
    }
    function _showCustom() {
        customVisible = true
        Qt.callLater(function() {
            customField.forceActiveFocus()
            customField.selectAll()
        })
    }

    Repeater {
        model: picker.presets
        delegate: Rectangle {
            id: chip
            required property var modelData

            readonly property bool active: !picker.customVisible
                && picker._current === modelData.minutes

            implicitHeight: 30
            implicitWidth: chipLabel.implicitWidth + 22
            radius: Theme.radiusPill
            color: active ? Theme.accentSoft
                 : chipHover.hovered ? Theme.surfaceHover : Theme.surface
            border.color: active ? Theme.accentSoftBorder : Theme.border
            border.width: 1
            Behavior on color { ColorAnimation { duration: 90 } }

            Label {
                id: chipLabel
                anchors.centerIn: parent
                text: chip.modelData.label
                font.pixelSize: Theme.fontCaption + 1
                font.family: Theme.fontFamily
                font.weight: chip.active ? Font.DemiBold : Font.Medium
                color: chip.active ? Theme.accent : Theme.textSecondary
            }
            HoverHandler { id: chipHover; cursorShape: Qt.PointingHandCursor }
            TapHandler {
                onTapped: {
                    picker.customVisible = false
                    picker.durationText = String(chip.modelData.minutes)
                }
            }

            activeFocusOnTab: true
            Accessible.role: Accessible.RadioButton
            Accessible.name: "Длительность " + chip.modelData.label
            Accessible.checked: chip.active
            Accessible.focusable: picker.enabled
            Keys.onReturnPressed: {
                picker.customVisible = false
                picker.durationText = String(chip.modelData.minutes)
            }
            Keys.onSpacePressed: {
                picker.customVisible = false
                picker.durationText = String(chip.modelData.minutes)
            }
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

    // ---- своя длительность ----
    Rectangle {
        id: customChip

        readonly property bool active: picker.customVisible

        implicitHeight: 30
        implicitWidth: customLabel.implicitWidth + 22
        radius: Theme.radiusPill
        color: active ? Theme.accentSoft
             : customHover.hovered ? Theme.surfaceHover : Theme.surface
        border.color: active ? Theme.accentSoftBorder : Theme.border
        border.width: 1

        Label {
            id: customLabel
            anchors.centerIn: parent
            text: "Другая…"
            font.pixelSize: Theme.fontCaption + 1
            font.family: Theme.fontFamily
            font.weight: customChip.active ? Font.DemiBold : Font.Medium
            color: customChip.active ? Theme.accent : Theme.textSecondary
        }
        HoverHandler { id: customHover; cursorShape: Qt.PointingHandCursor }
        TapHandler {
            onTapped: picker._showCustom()
        }
        activeFocusOnTab: picker.enabled
        Keys.onReturnPressed: picker._showCustom()
        Keys.onSpacePressed: picker._showCustom()
        Accessible.role: Accessible.RadioButton
        Accessible.name: "Другая длительность"
        Accessible.checked: customChip.active
        Accessible.focusable: picker.enabled

        Rectangle {
            anchors.fill: parent
            anchors.margins: -2
            radius: parent.radius
            color: "transparent"
            border.color: Theme.focusRing
            border.width: 2
            visible: customChip.activeFocus
        }
    }

    RowLayout {
        visible: picker.customVisible
        spacing: 4

        AppTextField {
            id: customField
            text: picker.durationText
            placeholderText: "мин"
            implicitWidth: 76
            horizontalAlignment: TextInput.AlignHCenter
            validator: IntValidator { bottom: 1; top: 1440 }
            onTextEdited: picker.durationText = text
            Accessible.name: "Длительность в минутах"
        }
        Label {
            text: "мин"
            font.pixelSize: Theme.fontCaption + 1
            font.family: Theme.fontFamily
            color: Theme.textMuted
        }
    }
}
