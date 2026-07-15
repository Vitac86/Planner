import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"
import "../theme"

ScrollView {
    id: page
    contentWidth: availableWidth
    clip: true

    // Режим раскладки считает Python (domain/layout.py): compact/normal/wide.
    // wide — две колонки (список + правая сводка/инспектор); normal — одна
    // колонка, инспектор выезжает панелью; compact — то же, плотнее.
    readonly property string layoutMode: uiVm.layoutModeFor(availableWidth)
    readonly property bool wide: layoutMode === "wide"
    readonly property bool compact: layoutMode === "compact"
    property var focusReturnItem: null
    property real bodyWidth: wide ? Math.min(availableWidth - 48, 1140)
                                  : Math.min(availableWidth - (compact ? 32 : 48), 780)

    // ---- выбор задачи живёт в ViewModel (todayVm.selectedUid/selectedTask) ----
    readonly property var selTask: todayVm.selectedTask
    readonly property bool dialogsOpen: editorDialog.visible
                                        || confirmDeleteDialog.visible
                                        || confirmBulkDeleteDialog.visible
                                        || dailyDialog.visible
                                        || snoozeMenu.visible
                                        || newTaskMenu.visible
                                        || inspectorDrawer.visible

    function restoreFocus() {
        var item = focusReturnItem
        focusReturnItem = null
        if (item && item.visible && item.enabled)
            item.forceActiveFocus()
        else
            quickAddItem.focusInput()
    }

    function selectTask(uid, ctrl, shift) {
        todayVm.selectTaskWithModifiers(uid, !!ctrl, !!shift)
        if (!page.wide && todayVm.selectedCount === 1 && !ctrl && !shift)
            inspectorDrawer.open()
        else if (todayVm.selectedCount > 1)
            inspectorDrawer.close()
    }

    // Ближайшая невыполненная задача на сегодня (для карточки «Дальше»).
    property var nextTask: {
        var list = todayVm.todayTasks
        for (var i = 0; i < list.length; i++)
            if (!list[i].completed) return list[i]
        return null
    }

    // ---- функции для клавиатурных сокращений (вызываются из Main.qml) ----
    function focusQuickAdd() { quickAddItem.focusInput() }
    function newTask() { editorDialog.openForCreate("") }
    function newScheduledTask() { editorDialog.openForCreateScheduled() }
    // Ctrl+Alt+N: новая задача из шаблона (редактор + выбор шаблона).
    function newTaskFromTemplate() {
        editorDialog.openForCreate("")
        editorDialog.openTemplatePicker()
    }
    function openSelected() {
        if (todayVm.selectedUid !== "")
            editorDialog.openForEdit(todayVm.selectedUid)
    }
    function toggleSelected() {
        if (todayVm.selectedUid !== "")
            todayVm.toggleCompleted(todayVm.selectedUid)
    }
    function deleteSelected() {
        if (todayVm.selectedCount > 1)
            confirmBulkDeleteDialog.openFor("bulk")
        else if (todayVm.selectedUid !== "")
            confirmDeleteDialog.openFor(todayVm.selectedUid)
    }
    function duplicateSelected() {
        if (todayVm.selectedCount === 1)
            todayVm.duplicateTask(todayVm.selectedUids[0])
    }
    function clearSelection() {
        inspectorDrawer.close()
        todayVm.clearSelection()
    }

    // Ежедневный чек-лист — переиспользуется в основной колонке (узкое окно)
    // и в правой сводке (широкое окно).
    component DailySection: ColumnLayout {
        spacing: Theme.spacingSm
        SectionHeader {
            title: "Ежедневные"
            count: todayVm.dailyTotalCount
            Layout.fillWidth: true
            IconButton {
                iconName: "settings"
                tip: "Управлять ежедневными задачами"
                onClicked: dailyDialog.openList()
            }
        }
        Flow {
            spacing: Theme.spacingSm
            Layout.fillWidth: true
            visible: todayVm.dailyTasks.length > 0
            Repeater {
                model: todayVm.dailyTasks
                delegate: Rectangle {
                    required property var modelData
                    radius: Theme.radiusPill
                    implicitHeight: 32
                    implicitWidth: dailyRow.implicitWidth + 26
                    color: modelData.done ? Theme.successSoft : Theme.surface
                    border.color: modelData.done ? Theme.successSoftBorder : Theme.border
                    border.width: 1
                    Behavior on color { ColorAnimation { duration: 120 } }

                    Row {
                        id: dailyRow
                        anchors.centerIn: parent
                        spacing: 6
                        AppIcon {
                            anchors.verticalCenter: parent.verticalCenter
                            name: modelData.done ? "check" : "circle"
                            size: 14
                            color: modelData.done ? Theme.success : Theme.textMuted
                        }
                        Label {
                            anchors.verticalCenter: parent.verticalCenter
                            text: (modelData.timeLabel && modelData.timeLabel.length > 0
                                   ? modelData.timeLabel + " · " : "") + modelData.title
                            font.pixelSize: 13
                            font.family: Theme.fontFamily
                            font.strikeout: modelData.done
                            color: modelData.done ? Theme.success : Theme.textSecondary
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: todayVm.toggleDaily(modelData.uid)
                    }
                }
            }
        }
        RowLayout {
            visible: todayVm.dailyTasks.length === 0
            Layout.fillWidth: true
            spacing: Theme.spacingSm
            Label {
                text: "На сегодня ежедневных нет"
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textMuted
            }
            AppButton {
                text: "Настроить"
                variant: "ghost"
                iconName: "plus"
                onClicked: dailyDialog.openList()
            }
            Item { Layout.fillWidth: true }
        }
    }

    Item {
        id: contentRoot
        implicitWidth: page.availableWidth
        implicitHeight: body.implicitHeight + 48

        RowLayout {
            id: body
            anchors.top: parent.top
            anchors.topMargin: page.compact ? 16 : 24
            x: Math.max(page.compact ? 16 : 24, (contentRoot.width - width) / 2)
            width: page.bodyWidth
            spacing: Theme.spacingXl

            // ================= ОСНОВНАЯ КОЛОНКА =================
            ColumnLayout {
                id: mainCol
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: page.compact ? Theme.spacingMd : Theme.spacingLg

                PageHeader {
                    title: "Сегодня"
                    subtitle: todayVm.headerDateText
                    Layout.fillWidth: true

                    AppButton {
                        id: newTaskButton
                        text: page.compact ? "" : "Новая задача"
                        variant: "primary"
                        iconName: "plus"
                        onClicked: {
                            page.focusReturnItem = newTaskButton
                            newTaskMenu.open()
                        }
                        ToolTip.visible: page.compact && hovered
                        ToolTip.text: "Новая задача (Ctrl+N)"

                        Menu {
                            id: newTaskMenu
                            objectName: "todayNewTaskMenu"
                            y: parent.height
                            MenuItem {
                                text: "Обычная задача"
                                onTriggered: page.newTask()
                            }
                            MenuItem {
                                text: "Запланированная задача"
                                onTriggered: page.newScheduledTask()
                            }
                            MenuSeparator {}
                            MenuItem {
                                text: "Из шаблона…"
                                onTriggered: page.newTaskFromTemplate()
                            }
                        }
                    }
                }

                // ---- KPI-плитки ----
                Flow {
                    spacing: Theme.spacingMd
                    Layout.fillWidth: true

                    StatTile {
                        value: todayVm.todayCount
                        caption: "на сегодня"
                        iconName: "today"
                        accentColor: Theme.accent
                        tintColor: Theme.accentSoft
                    }
                    StatTile {
                        value: todayVm.completedTodayCount
                        caption: "выполнено"
                        iconName: "check"
                        accentColor: Theme.success
                        tintColor: Theme.successSoft
                    }
                    StatTile {
                        value: todayVm.undatedCount
                        caption: "без даты"
                        iconName: "inbox"
                        accentColor: Theme.textSecondary
                        tintColor: Theme.surfacePressed
                    }
                    StatTile {
                        visible: todayVm.hasSyncQueue
                        value: todayVm.pendingSyncCount
                        caption: "ждёт синка"
                        iconName: "refresh"
                        accentColor: Theme.warningText
                        tintColor: Theme.warningSoft
                    }
                }

                // ---- Быстрое добавление ----
                SectionHeader { title: "Быстрое добавление"; Layout.fillWidth: true }
                QuickAdd {
                    id: quickAddItem
                    objectName: "quickAdd"
                    compact: page.compact
                    Layout.fillWidth: true
                }

                BulkActionToolbar {
                    objectName: "todayBulkToolbar"
                    visible: todayVm.selectedCount > 1
                    vm: todayVm
                    compact: page.compact
                    Layout.fillWidth: true
                    onDeleteRequested: confirmBulkDeleteDialog.openFor("bulk")
                }

                // ---- Задачи на сегодня ----
                SectionHeader {
                    title: "Задачи на сегодня"
                    count: todayVm.todayTasks.length
                    Layout.fillWidth: true
                }
                ColumnLayout {
                    spacing: Theme.spacingSm
                    Layout.fillWidth: true

                    Repeater {
                        model: todayVm.todayTasks
                        delegate: TaskCard {
                            id: todayCard
                            required property var modelData
                            Layout.fillWidth: true
                            uid: modelData.uid
                            title: modelData.title
                            notes: modelData.notes
                            timeLabel: modelData.timeLabel
                            isAllDay: modelData.isAllDay
                            priority: modelData.priority
                            completed: modelData.completed
                            hasPendingSync: modelData.hasPendingSync
                            isLinked: modelData.isLinked
                            isScheduled: modelData.isScheduled
                            isRecurring: modelData.isRecurring
                            isSeriesOccurrence: !!modelData.isSeriesOccurrence
                            isSeriesException: !!modelData.isSeriesException
                            tags: modelData.tags || []
                            tagOverflow: modelData.tagOverflow || 0
                            actionsEnabled: !todayVm.busy
                            selected: todayVm.isTaskSelected(modelData.uid)
                            onSelectionRequested: (uid, ctrl, shift) => page.selectTask(uid, ctrl, shift)
                            onToggled: uid => todayVm.toggleCompleted(uid)
                            onDuplicateRequested: uid => todayVm.duplicateTask(uid)
                            onTagClicked: name => {
                                searchVm.toggleTagFilter(name)
                                searchVm.openSearch()
                            }
                            onEditRequested: uid => {
                                page.focusReturnItem = todayCard
                                editorDialog.openForEdit(uid)
                            }
                            onDeleteRequested: uid => {
                                page.focusReturnItem = todayCard
                                confirmDeleteDialog.openFor(uid)
                            }
                            onSnoozeRequested: uid => {
                                page.focusReturnItem = todayCard
                                snoozeMenu.openFor(uid)
                            }
                        }
                    }
                    EmptyState {
                        visible: todayVm.todayTasks.length === 0
                        iconName: "sun"
                        text: "На сегодня задач нет"
                        hint: "Начните день с одной задачи — впишите её выше или создайте кнопкой ниже"
                        actionText: "Создать первую задачу"
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.spacingSm
                        onActionClicked: editorDialog.openForCreate("")
                    }
                }

                // ---- Без даты ----
                SectionHeader {
                    title: "Без даты"
                    count: todayVm.undatedTasks.length
                    Layout.fillWidth: true
                }
                ColumnLayout {
                    spacing: Theme.spacingSm
                    Layout.fillWidth: true

                    Repeater {
                        model: todayVm.undatedTasks
                        delegate: TaskCard {
                            id: undatedCard
                            required property var modelData
                            Layout.fillWidth: true
                            uid: modelData.uid
                            title: modelData.title
                            notes: modelData.notes
                            timeLabel: modelData.timeLabel
                            isAllDay: modelData.isAllDay
                            priority: modelData.priority
                            completed: modelData.completed
                            hasPendingSync: modelData.hasPendingSync
                            isLinked: modelData.isLinked
                            isScheduled: modelData.isScheduled
                            isRecurring: modelData.isRecurring
                            isSeriesOccurrence: !!modelData.isSeriesOccurrence
                            isSeriesException: !!modelData.isSeriesException
                            tags: modelData.tags || []
                            tagOverflow: modelData.tagOverflow || 0
                            actionsEnabled: !todayVm.busy
                            selected: todayVm.isTaskSelected(modelData.uid)
                            onSelectionRequested: (uid, ctrl, shift) => page.selectTask(uid, ctrl, shift)
                            onToggled: uid => todayVm.toggleCompleted(uid)
                            onDuplicateRequested: uid => todayVm.duplicateTask(uid)
                            onTagClicked: name => {
                                searchVm.toggleTagFilter(name)
                                searchVm.openSearch()
                            }
                            onEditRequested: uid => {
                                page.focusReturnItem = undatedCard
                                editorDialog.openForEdit(uid)
                            }
                            onDeleteRequested: uid => {
                                page.focusReturnItem = undatedCard
                                confirmDeleteDialog.openFor(uid)
                            }
                            onSnoozeRequested: uid => {
                                page.focusReturnItem = undatedCard
                                snoozeMenu.openFor(uid)
                            }
                        }
                    }
                    EmptyState {
                        visible: todayVm.undatedTasks.length === 0
                        iconName: "inbox"
                        text: "Задач без даты нет"
                        hint: "Идеи без времени можно копить здесь — добавьте одну через «Детали» без даты"
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.spacingSm
                    }
                }

                // ---- Ежедневные (в основной колонке только на узком окне) ----
                DailySection {
                    visible: !page.wide
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingSm
                }

                Item { implicitHeight: Theme.spacingSm }
            }

            // ================= ПРАВАЯ СВОДКА / ИНСПЕКТОР (wide) =================
            ColumnLayout {
                id: rail
                visible: page.wide
                Layout.preferredWidth: 320
                Layout.maximumWidth: 320
                Layout.alignment: Qt.AlignTop
                spacing: Theme.spacingLg

                // ---- инспектор выбранной задачи ----
                TaskInspector {
                    visible: todayVm.selectedCount === 1
                             && page.selTask !== null && page.selTask !== undefined
                    task: page.selTask
                    busy: todayVm.busy
                    snoozeActions: page.selTask ? todayVm.snoozeActionsFor(page.selTask.uid) : []
                    taskPresets: page.selTask ? todayVm.taskPresetsFor(page.selTask.uid) : []
                    Layout.fillWidth: true
                    onEditRequested: uid => editorDialog.openForEdit(uid)
                    onToggleRequested: uid => todayVm.toggleCompleted(uid)
                    onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
                    onDuplicateRequested: uid => todayVm.duplicateTask(uid)
                    onPostponeRequested: (uid, action) => todayVm.postponeTask(uid, action)
                    seriesSummary: page.selTask && page.selTask.isSeriesOccurrence
                                   ? todayVm.seriesSummaryFor(page.selTask.uid) : ""
                    onDuplicateSeriesRequested: seriesUid => todayVm.duplicateSeries(seriesUid)
                    onPresetRequested: (uid, presetId) => todayVm.applyTaskPreset(uid, presetId)
                    onPickRequested: uid => editorDialog.openForEdit(uid)
                    onCloseRequested: page.clearSelection()
                }

                // ---- сводка дня (когда ничего не выбрано) ----
                ColumnLayout {
                    id: summary
                    visible: todayVm.selectedCount === 0 && !page.selTask
                    Layout.fillWidth: true
                    spacing: Theme.spacingLg

                    // прогресс дня
                    Panel {
                        Layout.fillWidth: true
                        implicitHeight: progressCol.implicitHeight + 2 * Theme.spacingLg

                        ColumnLayout {
                            id: progressCol
                            anchors.fill: parent
                            anchors.margins: Theme.spacingLg
                            spacing: Theme.spacingMd

                            Label {
                                text: "Прогресс дня"
                                font.pixelSize: Theme.fontSubtitle
                                font.family: Theme.fontFamily
                                font.weight: Font.DemiBold
                                color: Theme.textPrimary
                            }

                            Item {
                                Layout.alignment: Qt.AlignHCenter
                                implicitWidth: 132
                                implicitHeight: 132

                                ProgressRing {
                                    anchors.fill: parent
                                    value: todayVm.completedTodayCount
                                    total: todayVm.todayCount
                                    barColor: Theme.success
                                }
                                ColumnLayout {
                                    anchors.centerIn: parent
                                    spacing: 0
                                    Label {
                                        Layout.alignment: Qt.AlignHCenter
                                        text: todayVm.completedTodayCount + " / " + todayVm.todayCount
                                        font.pixelSize: Theme.fontTitle
                                        font.family: Theme.fontFamily
                                        font.weight: Font.DemiBold
                                        color: Theme.textPrimary
                                    }
                                    Label {
                                        Layout.alignment: Qt.AlignHCenter
                                        text: "выполнено"
                                        font.pixelSize: Theme.fontCaption
                                        font.family: Theme.fontFamily
                                        color: Theme.textMuted
                                    }
                                }
                            }
                        }
                    }

                    // следующая задача
                    Panel {
                        Layout.fillWidth: true
                        implicitHeight: nextCol.implicitHeight + 2 * Theme.spacingLg

                        ColumnLayout {
                            id: nextCol
                            anchors.fill: parent
                            anchors.margins: Theme.spacingLg
                            spacing: Theme.spacingSm

                            RowLayout {
                                spacing: Theme.spacingSm
                                Layout.fillWidth: true
                                AppIcon { name: "sparkle"; size: 16; color: Theme.accent }
                                Label {
                                    text: "Дальше"
                                    font.pixelSize: Theme.fontSubtitle
                                    font.family: Theme.fontFamily
                                    font.weight: Font.DemiBold
                                    color: Theme.textPrimary
                                }
                            }

                            Label {
                                visible: page.nextTask !== null
                                text: page.nextTask ? page.nextTask.title : ""
                                font.pixelSize: Theme.fontBody
                                font.family: Theme.fontFamily
                                font.weight: Font.Medium
                                color: Theme.textPrimary
                                wrapMode: Text.WordWrap
                                maximumLineCount: 2
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            RowLayout {
                                visible: page.nextTask !== null
                                spacing: Theme.spacingSm
                                Badge {
                                    text: page.nextTask ? page.nextTask.timeLabel : ""
                                    fg: (page.nextTask && page.nextTask.isAllDay) ? Theme.success : Theme.accent
                                    bg: (page.nextTask && page.nextTask.isAllDay) ? Theme.successSoft : Theme.accentSoft
                                }
                                Badge {
                                    visible: page.nextTask && page.nextTask.priority > 0
                                    text: page.nextTask ? Theme.priorityName(page.nextTask.priority) : ""
                                    fg: page.nextTask ? Theme.priorityColor(page.nextTask.priority) : Theme.textSecondary
                                    bg: page.nextTask ? Theme.priorityBgColor(page.nextTask.priority) : Theme.surfaceHover
                                }
                            }

                            RowLayout {
                                visible: page.nextTask === null
                                spacing: Theme.spacingSm
                                AppIcon { name: "check"; size: 16; color: Theme.success }
                                Label {
                                    text: todayVm.todayCount > 0
                                          ? "Всё на сегодня выполнено" : "Задач на сегодня ещё нет"
                                    font.pixelSize: Theme.fontBody
                                    font.family: Theme.fontFamily
                                    color: Theme.textMuted
                                }
                            }
                        }
                    }

                    // ежедневные
                    Panel {
                        Layout.fillWidth: true
                        implicitHeight: dailyWrap.implicitHeight + 2 * Theme.spacingLg

                        DailySection {
                            id: dailyWrap
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: Theme.spacingLg
                        }
                    }
                }

                Item { Layout.fillHeight: true }
            }
        }
    }

    // ---- инспектор-панель для normal/compact ----
    Drawer {
        id: inspectorDrawer
        edge: Qt.RightEdge
        width: page.compact ? Math.min(page.width - 40, 360) : 360
        height: page.height
        interactive: visible

        onClosed: todayVm.clearSelection()

        background: Rectangle {
            color: Theme.surface
            border.color: Theme.border
            border.width: 1
        }

        TaskInspector {
            anchors.fill: parent
            anchors.margins: Theme.spacingSm
            visible: page.selTask !== null && page.selTask !== undefined
            task: page.selTask
            busy: todayVm.busy
            snoozeActions: page.selTask ? todayVm.snoozeActionsFor(page.selTask.uid) : []
            taskPresets: page.selTask ? todayVm.taskPresetsFor(page.selTask.uid) : []
            elevationOpacity: 0
            borderWidth: 0
            onEditRequested: uid => { inspectorDrawer.close(); editorDialog.openForEdit(uid) }
            onToggleRequested: uid => todayVm.toggleCompleted(uid)
            onDeleteRequested: uid => { inspectorDrawer.close(); confirmDeleteDialog.openFor(uid) }
            onDuplicateRequested: uid => todayVm.duplicateTask(uid)
            onPostponeRequested: (uid, action) => todayVm.postponeTask(uid, action)
            seriesSummary: page.selTask && page.selTask.isSeriesOccurrence
                           ? todayVm.seriesSummaryFor(page.selTask.uid) : ""
            onDuplicateSeriesRequested: seriesUid => todayVm.duplicateSeries(seriesUid)
            onPresetRequested: (uid, presetId) => todayVm.applyTaskPreset(uid, presetId)
            onPickRequested: uid => {
                inspectorDrawer.close()
                editorDialog.openForEdit(uid)
            }
            onCloseRequested: inspectorDrawer.close()
        }
    }

    SnoozeMenu {
        id: snoozeMenu
        vm: todayVm
        onPickRequested: uid => editorDialog.openForEdit(uid)
        onClosed: {
            if (!editorDialog.visible)
                page.restoreFocus()
        }
    }

    TaskEditorDialog {
        id: editorDialog
        objectName: "todayEditorDialog"
        vm: todayVm
        onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
        // после закрытия диалога фокус возвращается к вызвавшему контролу
        onClosed: {
            if (!confirmDeleteDialog.visible)
                page.restoreFocus()
        }
    }

    DailyTasksDialog {
        id: dailyDialog
        objectName: "dailyTasksDialog"
    }

    ConfirmDialog {
        id: confirmDeleteDialog
        headerText: "Удалить задачу?"
        message: "Задача будет помечена удалённой; если её событие уже есть "
                 + "в календаре, удаление события встанет в очередь синка."
        onConfirmed: uid => todayVm.deleteTask(uid)
        onClosed: page.restoreFocus()
    }
    ConfirmDialog {
        id: confirmBulkDeleteDialog
        headerText: "Удалить выбранные задачи?"
        message: "Будут удалены только выбранные видимые задачи. Связанные события попадут в очередь следующего ручного синка."
        onConfirmed: todayVm.bulkDelete()
        onClosed: page.restoreFocus()
    }
}
