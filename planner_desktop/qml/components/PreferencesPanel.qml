import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

Panel {
    id: panel
    objectName: "preferencesPanel"
    property var vm

    implicitHeight: contents.implicitHeight + 2 * Theme.spacingLg

    ColumnLayout {
        id: contents
        anchors.fill: parent
        anchors.margins: Theme.spacingLg
        spacing: Theme.spacingMd

        SectionHeader {
            title: "Удобство работы"
            Layout.fillWidth: true
        }

        Switch {
            id: completionSwitch
            objectName: "completionStatsSwitch"
            text: "Статистика выполнения"
            checked: panel.vm ? panel.vm.showCompletionStats : true
            font.pixelSize: Theme.fontBody
            font.family: Theme.fontFamily
            Layout.fillWidth: true
            onToggled: if (panel.vm) {
                panel.vm.setShowCompletionStats(checked)
                checked = Qt.binding(function() { return panel.vm.showCompletionStats })
            }
        }
        Label {
            text: "Круг прогресса и счётчики выполнения на странице «Сегодня»."
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textSecondary
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Label {
            text: "При запуске открывать"
            font.pixelSize: Theme.fontBody
            font.family: Theme.fontFamily
            color: Theme.textPrimary
        }
        ComboBox {
            id: startupChoice
            objectName: "startupPageChoice"
            Layout.fillWidth: true
            Layout.maximumWidth: 360
            textRole: "label"
            valueRole: "value"
            model: [
                {label: "Сегодня", value: "today"},
                {label: "Календарь", value: "calendar"},
                {label: "Последнюю открытую страницу", value: "last"}
            ]
            currentIndex: panel.vm
                          ? (panel.vm.startupPage === "calendar" ? 1
                             : panel.vm.startupPage === "last" ? 2 : 0) : 0
            Accessible.name: "Стартовая страница"
            onActivated: if (panel.vm) {
                panel.vm.setStartupPage(currentValue)
                currentIndex = Qt.binding(function() {
                    return panel.vm.startupPage === "calendar" ? 1
                           : panel.vm.startupPage === "last" ? 2 : 0
                })
            }
        }

        Label {
            text: "Рабочие часы в календаре"
            font.pixelSize: Theme.fontBody
            font.family: Theme.fontFamily
            color: Theme.textPrimary
        }
        RowLayout {
            spacing: Theme.spacingSm
            Label {
                text: "С"
                color: Theme.textSecondary
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
            }
            SpinBox {
                id: startHour
                objectName: "workStartHourInput"
                from: 0
                to: panel.vm ? panel.vm.workEndHour - 1 : 22
                value: panel.vm ? panel.vm.workStartHour : 6
                editable: true
                implicitWidth: 160
                Accessible.name: "Начало рабочего дня, час"
                onValueModified: if (panel.vm) {
                    panel.vm.setWorkHours(value, panel.vm.workEndHour)
                    value = Qt.binding(function() { return panel.vm.workStartHour })
                }
            }
            Label {
                text: "до"
                color: Theme.textSecondary
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
            }
            SpinBox {
                id: endHour
                objectName: "workEndHourInput"
                from: panel.vm ? panel.vm.workStartHour + 1 : 7
                to: 24
                value: panel.vm ? panel.vm.workEndHour : 23
                editable: true
                implicitWidth: 160
                Accessible.name: "Конец рабочего дня, час"
                onValueModified: if (panel.vm) {
                    panel.vm.setWorkHours(panel.vm.workStartHour, value)
                    value = Qt.binding(function() { return panel.vm.workEndHour })
                }
            }
        }
        Label {
            text: "Этот диапазон задаёт видимую шкалу времени в календаре."
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textSecondary
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Label {
            text: "Длительность новой задачи"
            font.pixelSize: Theme.fontBody
            font.family: Theme.fontFamily
            color: Theme.textPrimary
        }
        RowLayout {
            spacing: Theme.spacingSm
            SpinBox {
                id: defaultDuration
                objectName: "defaultDurationInput"
                from: 5
                to: 1440
                stepSize: 5
                value: panel.vm ? panel.vm.defaultDurationMinutes : 60
                editable: true
                implicitWidth: 176
                Accessible.name: "Длительность новой задачи, минут"
                onValueModified: if (panel.vm) {
                    panel.vm.setDefaultDurationMinutes(value)
                    value = Qt.binding(function() { return panel.vm.defaultDurationMinutes })
                }
            }
            Label {
                text: "минут"
                color: Theme.textSecondary
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
            }
        }
        Label {
            text: "Подставляется при создании задачи со временем."
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textSecondary
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Label {
            text: "Размер окна, вид и фильтр календаря запоминаются автоматически."
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Label {
            objectName: "preferencesError"
            text: panel.vm ? panel.vm.errorMessage : ""
            visible: text.length > 0
            color: Theme.danger
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
}
