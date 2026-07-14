import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

ColumnLayout {
    id: bar
    property var vm: null
    property bool compact: false
    spacing: Theme.spacingSm

    SegmentedControl {
        Layout.fillWidth: true
        current: bar.vm ? bar.vm.statusFilter : "all"
        options: [
            { label: "Активные", value: "active" },
            { label: "Выполненные", value: "completed" },
            { label: "Все", value: "all" }
        ]
        Accessible.name: "Фильтр состояния задач"
        onSelected: value => bar.vm.setStatusFilter(value)
    }
    Flow {
        Layout.fillWidth: true
        spacing: Theme.spacingSm
        ComboBox {
            id: scopeCombo
            width: bar.compact ? 190 : 220
            model: [
                { text: "Любое расписание", value: "all" },
                { text: "Сегодня", value: "today" },
                { text: "На этой неделе", value: "this_week" },
                { text: "Запланированные", value: "scheduled" },
                { text: "Без даты", value: "undated" },
                { text: "На весь день", value: "all_day" }
            ]
            textRole: "text"
            currentIndex: {
                var values = ["all", "today", "this_week", "scheduled", "undated", "all_day"]
                return Math.max(0, values.indexOf(bar.vm ? bar.vm.scopeFilter : "all"))
            }
            Accessible.name: "Фильтр расписания"
            onActivated: bar.vm.setScopeFilter(model[currentIndex].value)
        }
        ComboBox {
            id: priorityCombo
            width: bar.compact ? 170 : 190
            model: ["Любой приоритет", "Без приоритета", "Низкий", "Средний", "Высокий"]
            currentIndex: bar.vm ? bar.vm.priorityFilter + 1 : 0
            Accessible.name: "Фильтр приоритета"
            onActivated: bar.vm.setPriorityFilter(currentIndex - 1)
        }
        AppButton {
            visible: bar.vm && bar.vm.activeFilterCount > 0
            text: "Сбросить"
            iconName: "refresh"
            variant: "ghost"
            Accessible.name: "Сбросить все фильтры поиска"
            onClicked: bar.vm.clearFilters()
        }
    }
}
