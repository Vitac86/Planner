import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Сегментированный переключатель (пилюля из вкладок) дизайн-системы.
// options — список {label, value[, count]}; current — выбранное value;
// selected(value) — сигнал выбора. Используется фильтрами «Истории»
// (7 дней / 30 / всё) и «Календаря» (все / активные / выполненные / ежедневные).
Rectangle {
    id: control

    property var options: []
    property string current: ""
    signal selected(string value)

    implicitHeight: 36
    implicitWidth: row.implicitWidth + 8
    radius: Theme.radiusSmall + 2
    color: Theme.surfaceMuted
    border.color: Theme.border
    border.width: 1

    Row {
        id: row
        anchors.centerIn: parent
        spacing: 2

        Repeater {
            model: control.options
            delegate: Rectangle {
                id: seg
                required property var modelData
                required property int index

                readonly property bool active: control.current === modelData.value
                implicitWidth: segRow.implicitWidth + 24
                implicitHeight: 28
                radius: Theme.radiusSmall
                color: active ? Theme.surface
                     : segHover.hovered ? Theme.surfaceHover : "transparent"
                border.color: active ? Theme.accentSoftBorder : "transparent"
                border.width: 1

                Behavior on color { ColorAnimation { duration: 90 } }

                Row {
                    id: segRow
                    anchors.centerIn: parent
                    spacing: 6

                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        text: seg.modelData.label
                        font.pixelSize: Theme.fontCaption + 1
                        font.family: Theme.fontFamily
                        font.weight: seg.active ? Font.DemiBold : Font.Medium
                        color: seg.active ? Theme.accent : Theme.textSecondary
                    }
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: seg.modelData.count !== undefined && seg.modelData.count >= 0
                        implicitHeight: 18
                        implicitWidth: countLabel.implicitWidth + 12
                        radius: height / 2
                        color: seg.active ? Theme.accentSoft : Theme.surfacePressed
                        Label {
                            id: countLabel
                            anchors.centerIn: parent
                            text: seg.modelData.count !== undefined ? String(seg.modelData.count) : ""
                            font.pixelSize: Theme.fontCaption - 1
                            font.family: Theme.fontFamily
                            font.weight: Font.DemiBold
                            color: seg.active ? Theme.accent : Theme.textMuted
                        }
                    }
                }

                HoverHandler { id: segHover; cursorShape: Qt.PointingHandCursor }
                TapHandler { onTapped: control.selected(seg.modelData.value) }

                // фокус-обводка для доступности с клавиатуры
                focus: false
                activeFocusOnTab: true
                Keys.onReturnPressed: control.selected(seg.modelData.value)
                Keys.onSpacePressed: control.selected(seg.modelData.value)
                Rectangle {
                    anchors.fill: parent
                    anchors.margins: -2
                    radius: parent.radius + 2
                    color: "transparent"
                    border.color: Theme.focusRing
                    border.width: 2
                    visible: seg.activeFocus
                }
            }
        }
    }
}
