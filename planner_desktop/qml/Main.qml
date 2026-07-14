import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Material
import QtQuick.Layouts
import QtQuick.Effects

import "components"
import "pages"
import "theme"

ApplicationWindow {
    id: root
    visible: true
    width: 1240
    height: 800
    // Минимальный работоспособный размер задаёт Python (domain/layout.py).
    minimumWidth: uiVm.minWindowWidth
    minimumHeight: uiVm.minWindowHeight
    title: "Planner — экспериментальный десктоп (PySide6/QML)"

    Material.theme: Material.Light
    Material.accent: Theme.accent
    Material.primary: Theme.accent
    color: Theme.background
    font.family: Theme.fontFamily

    property int currentPage: 0

    // ---- маршрутизация сокращений ----
    // Политика в Python (domain/keyboard.py через uiVm.allowShortcut):
    // «голые» клавиши уступают текстовому вводу и открытым диалогам.
    function _typingNow() {
        var it = root.activeFocusItem
        return !!(it && (it instanceof TextInput || it instanceof TextEdit))
    }
    readonly property bool anyDialogOpen:
        todayPage.dialogsOpen || calendarPage.dialogsOpen || historyPage.dialogsOpen
    function _allow(name) {
        return uiVm.allowShortcut(name, root._typingNow(), root.anyDialogOpen)
    }
    function _newTaskOnCurrentPage() {
        if (root.currentPage === 1) calendarPage.newTask()
        else { root.currentPage = 0; todayPage.newTask() }
    }
    function _newScheduledTaskOnCurrentPage() {
        if (root.currentPage === 1) calendarPage.newScheduledTask()
        else { root.currentPage = 0; todayPage.newScheduledTask() }
    }
    function _currentTaskPage() {
        if (root.currentPage === 0) return todayPage
        if (root.currentPage === 1) return calendarPage
        if (root.currentPage === 2) return historyPage
        return null
    }

    Item {
        id: shortcutScope
        anchors.fill: parent
        focus: true

        // «Голые» клавиши обрабатываются по цепочке фокуса: если Enter/Space/
        // Delete съело текстовое поле или кнопка — сюда они не долетят,
        // поэтому набор текста сокращения не ломают. Политика Python
        // проверяется дополнительно (диалоги, зарезервированные клавиши).
        Keys.onPressed: event => {
            var page = root._currentTaskPage()
            if (event.key === Qt.Key_Escape) {
                if (root.currentPage === 1 && calendarPage.cancelInteraction()) {
                    event.accepted = true
                } else if (page && root._allow("clear_selection")) {
                    page.clearSelection()
                    event.accepted = true
                }
            } else if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
                if (page && root._allow("open_selected")) {
                    page.openSelected()
                    event.accepted = true
                }
            } else if (event.key === Qt.Key_Space) {
                if (page && root._allow("toggle_selected")) {
                    page.toggleSelected()
                    event.accepted = true
                }
            } else if (event.key === Qt.Key_Delete || event.key === Qt.Key_Backspace) {
                if (page && root._allow("delete_selected")) {
                    page.deleteSelected()
                    event.accepted = true
                }
            }
        }

        RowLayout {
            anchors.fill: parent
            spacing: 0

            Sidebar {
                Layout.fillHeight: true
                currentIndex: root.currentPage
                onPageSelected: index => root.currentPage = index
            }

            // Область контента с мягким вертикальным градиентом фона.
            Item {
                Layout.fillWidth: true
                Layout.fillHeight: true

                Rectangle {
                    anchors.fill: parent
                    gradient: Gradient {
                        GradientStop { position: 0.0; color: Theme.background }
                        GradientStop { position: 1.0; color: Theme.backgroundAlt }
                    }
                }

                StackLayout {
                    anchors.fill: parent
                    currentIndex: root.currentPage

                    TodayPage { id: todayPage; objectName: "todayPage" }
                    CalendarPage { id: calendarPage; objectName: "calendarPage" }
                    HistoryPage { id: historyPage; objectName: "historyPage" }
                    SettingsPage {}
                }
            }
        }
    }

    // ---- клавиатурные сокращения окна (см. docs/SHORTCUTS.md) ----
    Shortcut {
        sequences: [StandardKey.New, "Ctrl+N"]
        enabled: root._allow("new_task")
        onActivated: root._newTaskOnCurrentPage()
    }
    Shortcut {
        sequence: "Ctrl+Shift+N"
        enabled: root._allow("new_scheduled_task")
        onActivated: root._newScheduledTaskOnCurrentPage()
    }
    Shortcut {
        sequences: ["Ctrl+K", "Meta+K"]
        enabled: root._allow("quick_add")
        onActivated: { root.currentPage = 0; todayPage.focusQuickAdd() }
    }
    Shortcut {
        sequence: "/"
        enabled: root._allow("quick_add_slash")
        onActivated: { root.currentPage = 0; todayPage.focusQuickAdd() }
    }
    // Ctrl+R — перечитать ТОЛЬКО локальные модели; Google-синк не запускается.
    Shortcut {
        sequence: "Ctrl+R"
        enabled: root._allow("refresh")
        onActivated: {
            todayVm.refresh()
            calendarVm.refresh()
            historyVm.refresh()
            dailyVm.refresh()
            settingsVm.refresh()
            toast.show("Данные обновлены")
        }
    }
    // Ctrl+F зарезервирован за поиском (фаза 3) — сознательно не привязан.

    // Навигация по дням недели на «Календаре» стрелками (вне текстовых полей).
    Shortcut {
        sequence: "Left"
        enabled: root.currentPage === 1 && root._allow("calendar_prev_day")
        onActivated: calendarPage.selectPrevDay()
    }
    Shortcut {
        sequence: "Right"
        enabled: root.currentPage === 1 && root._allow("calendar_next_day")
        onActivated: calendarPage.selectNextDay()
    }
    Shortcut {
        sequence: "PgUp"
        enabled: root.currentPage === 1 && root._allow("calendar_prev_period")
        onActivated: calendarPage.selectPrevPeriod()
    }
    Shortcut {
        sequence: "PgDown"
        enabled: root.currentPage === 1 && root._allow("calendar_next_period")
        onActivated: calendarPage.selectNextPeriod()
    }
    Shortcut {
        sequence: "Home"
        enabled: root.currentPage === 1 && calendarPage.gridFocused
                 && root._allow("calendar_today")
        onActivated: calendarPage.goToToday()
    }
    Shortcut {
        sequence: "Up"
        enabled: root.currentPage === 1 && calendarPage.gridFocused
                 && root._allow("calendar_prev_event")
        onActivated: calendarPage.selectPrevEvent()
    }
    Shortcut {
        sequence: "Down"
        enabled: root.currentPage === 1 && calendarPage.gridFocused
                 && root._allow("calendar_next_event")
        onActivated: calendarPage.selectNextEvent()
    }
    Shortcut {
        sequence: "Alt+Up"
        enabled: root.currentPage === 1 && root._allow("calendar_move_slot")
        onActivated: calendarPage.moveSelectedMinutes(-15)
    }
    Shortcut {
        sequence: "Alt+Down"
        enabled: root.currentPage === 1 && root._allow("calendar_move_slot")
        onActivated: calendarPage.moveSelectedMinutes(15)
    }
    Shortcut {
        sequence: "Alt+Shift+Left"
        enabled: root.currentPage === 1 && root._allow("calendar_move_day")
        onActivated: calendarPage.moveSelectedDays(-1)
    }
    Shortcut {
        sequence: "Alt+Shift+Right"
        enabled: root.currentPage === 1 && root._allow("calendar_move_day")
        onActivated: calendarPage.moveSelectedDays(1)
    }
    Shortcut {
        sequence: "Alt+Shift+Up"
        enabled: root.currentPage === 1 && root._allow("calendar_resize")
        onActivated: calendarPage.resizeSelectedMinutes(-15)
    }
    Shortcut {
        sequence: "Alt+Shift+Down"
        enabled: root.currentPage === 1 && root._allow("calendar_resize")
        onActivated: calendarPage.resizeSelectedMinutes(15)
    }
    Shortcut {
        sequence: "Ctrl+Alt+A"
        enabled: root.currentPage === 1 && root._allow("calendar_to_all_day")
        onActivated: calendarPage.convertSelectedToAllDay()
    }
    Shortcut {
        sequence: "Ctrl+Alt+U"
        enabled: root.currentPage === 1 && root._allow("calendar_unschedule")
        onActivated: calendarPage.unscheduleSelected()
    }

    // ---- всплывашки успеха/ошибки ----
    Toast {
        id: toast
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
    }

    Connections {
        target: todayVm
        function onToastMessage(text) { toast.show(text) }
        function onToastError(text) { toast.showError(text) }
    }
    Connections {
        target: calendarVm
        function onToastMessage(text) { toast.show(text) }
        function onToastError(text) { toast.showError(text) }
    }
    Connections {
        target: historyVm
        function onToastMessage(text) { toast.show(text) }
        function onToastError(text) { toast.showError(text) }
    }
    Connections {
        target: dailyVm
        function onToastMessage(text) { toast.show(text) }
    }
    Connections {
        target: settingsVm
        function onToastMessage(text) { toast.show(text) }
    }
}
