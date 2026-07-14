import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"
import "../theme"

// Phase 2.1 Calendar: hourly day/work-week/week grid plus the preserved agenda,
// daily checklist, shared editor/actions, day summary, and inspector.
Item {
    id: page

    readonly property string layoutMode: uiVm.layoutModeFor(width)
    readonly property bool wide: layoutMode === "wide"
    readonly property bool compact: layoutMode === "compact"
    readonly property var selTask: calendarVm.selectedTask
    readonly property bool dialogsOpen: editorDialog.visible
                                        || confirmDeleteDialog.visible
                                        || confirmBulkDeleteDialog.visible
                                        || snoozeMenu.visible
                                        || undatedDrawer.visible
    readonly property bool gridFocused: timeGrid.gridFocused
    property bool agendaExpanded: false
    property var focusReturnItem: null
    property string selectedGridDate: ""
    property int selectedGridMinute: -1

    onLayoutModeChanged: calendarVm.setResponsiveMode(layoutMode)
    Component.onCompleted: calendarVm.setResponsiveMode(layoutMode)
    onVisibleChanged: if (visible) timeGrid.ensureInitialScroll(false)

    Timer {
        interval: 60000
        repeat: true
        running: page.visible
        onTriggered: calendarVm.refreshCurrentTime()
    }

    function restoreFocus() {
        var item = focusReturnItem
        focusReturnItem = null
        if (item && item.visible && item.enabled)
            item.forceActiveFocus()
        else
            timeGrid.forceActiveFocus()
    }

    function selectTask(uid, ctrl, shift) {
        calendarVm.selectTaskWithModifiers(uid, !!ctrl, !!shift)
        if (!page.wide && calendarVm.selectedCount === 1 && !ctrl && !shift)
            inspectorDrawer.open()
        else if (calendarVm.selectedCount > 1)
            inspectorDrawer.close()
    }

    function editEvent(uid) {
        if (inspectorDrawer.visible)
            inspectorDrawer.close()
        editorDialog.openForEdit(uid)
    }

    function newTask() { editorDialog.openForCreate(calendarVm.selectedDateText) }
    function newScheduledTask() {
        editorDialog.openForCreateScheduled(calendarVm.selectedDateText)
    }
    function openSelected() {
        if (calendarVm.selectedUid !== "")
            editEvent(calendarVm.selectedUid)
    }
    function toggleSelected() {
        if (calendarVm.selectedUid !== "")
            calendarVm.toggleCompleted(calendarVm.selectedUid)
    }
    function deleteSelected() {
        if (calendarVm.selectedCount > 1)
            confirmBulkDeleteDialog.openFor("bulk")
        else if (calendarVm.selectedUid !== "")
            confirmDeleteDialog.openFor(calendarVm.selectedUid)
    }
    function duplicateSelected() {
        if (calendarVm.selectedCount === 1)
            calendarVm.duplicateTask(calendarVm.selectedUids[0])
    }
    function clearSelection() {
        inspectorDrawer.close()
        calendarVm.clearSelection()
    }
    function cancelInteraction() {
        if (calendarVm.dragging || calendarVm.resizing) {
            calendarVm.cancelInteraction()
            timeGrid.forceActiveFocus()
            return true
        }
        return false
    }
    function moveSelectedMinutes(delta) { calendarVm.moveSelectedByMinutes(delta) }
    function moveSelectedDays(delta) { calendarVm.moveSelectedByDays(delta) }
    function resizeSelectedMinutes(delta) { calendarVm.resizeSelectedByMinutes(delta) }
    function convertSelectedToAllDay() { calendarVm.convertSelectedToAllDay() }
    function unscheduleSelected() { calendarVm.unscheduleSelected() }
    function _inside(item, point) {
        return item && item.visible && point.x >= 0 && point.y >= 0
               && point.x <= item.width && point.y <= item.height
    }
    function routeGridPointerToUndated(x, y, shift) {
        var target = page.wide ? undatedWide : undatedDrawerPanel
        if (!target || !target.visible)
            return
        var point = timeGrid.mapToItem(target, x, y)
        if (page._inside(target, point))
            calendarVm.updateDragTarget("undated_panel", "", 0, 0, shift)
    }
    function routeUndatedPointer(panel, x, y, shift) {
        var point = panel.mapToItem(timeGrid, x, y)
        timeGrid.updateInteractionPointer(point.x, point.y, shift)
    }
    function selectUndatedTask(uid, ctrl, shift) {
        if (undatedDrawer.visible)
            undatedDrawer.close()
        page.selectTask(uid, ctrl, shift)
    }
    function selectPrevDay() { calendarVm.previousDay() }
    function selectNextDay() { calendarVm.nextDay() }
    function selectPrevPeriod() { calendarVm.previousPeriod() }
    function selectNextPeriod() { calendarVm.nextPeriod() }
    function selectPrevEvent() { calendarVm.selectPreviousEvent() }
    function selectNextEvent() { calendarVm.selectNextEvent() }
    function goToToday() {
        calendarVm.goToToday()
        timeGrid.ensureInitialScroll(true)
    }

    Connections {
        target: calendarVm
        function onEditEventRequested(uid) { page.editEvent(uid) }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: page.compact ? Theme.spacingLg : Theme.spacingXl
        spacing: page.compact ? Theme.spacingSm : Theme.spacingMd

        PageHeader {
            title: "Календарь"
            subtitle: calendarVm.periodTitle
            stackActions: page.compact
            Layout.fillWidth: true

            AppButton {
                text: ""
                variant: "secondary"
                iconName: "chevron-left"
                Accessible.name: "Предыдущий период"
                onClicked: calendarVm.previousPeriod()
                ToolTip.visible: hovered
                ToolTip.text: "Предыдущий период · Page Up"
            }
            AppButton {
                text: "Сегодня"
                variant: calendarVm.isCurrentWeek ? "ghost" : "secondary"
                Accessible.name: "Перейти к сегодняшнему дню"
                onClicked: page.goToToday()
            }
            AppButton {
                text: ""
                variant: "secondary"
                iconName: "chevron-right"
                Accessible.name: "Следующий период"
                onClicked: calendarVm.nextPeriod()
                ToolTip.visible: hovered
                ToolTip.text: "Следующий период · Page Down"
            }
            AppButton {
                text: page.compact ? "" : "Задача"
                variant: "primary"
                iconName: "plus"
                Accessible.name: "Создать задачу на выбранный день"
                onClicked: page.newTask()
                ToolTip.visible: page.compact && hovered
                ToolTip.text: "Задача на выбранный день"
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: Theme.spacingSm

            CalendarViewModeSwitch {
                options: calendarVm.displayModeOptions
                current: calendarVm.displayMode
                compact: page.compact
                onSelected: value => calendarVm.setDisplayMode(value)
            }
            Item { Layout.fillWidth: true }
            Badge {
                visible: page.selectedGridMinute >= 0 && !page.compact
                text: "Выбрано " + String(Math.floor(page.selectedGridMinute / 60)).padStart(2, "0")
                      + ":" + String(page.selectedGridMinute % 60).padStart(2, "0")
                fg: Theme.textSecondary
                bg: Theme.surfacePressed
            }
            InteractionHint {
                visible: calendarVm.dragging || calendarVm.resizing
                text: calendarVm.proposalMessage
                valid: calendarVm.proposalValid
                Layout.maximumWidth: page.compact ? 180 : 320
            }
            AppButton {
                visible: !page.wide
                text: page.compact ? "" : "Без даты (" + calendarVm.undatedTaskCount + ")"
                iconName: "calendar"
                variant: undatedDrawer.visible ? "secondary" : "ghost"
                Accessible.name: "Открыть задачи без даты, "
                                 + calendarVm.undatedTaskCount
                onClicked: {
                    inspectorDrawer.close()
                    undatedDrawer.open()
                }
                ToolTip.visible: page.compact && hovered
                ToolTip.text: "Без даты (" + calendarVm.undatedTaskCount + ")"
            }
            AppButton {
                objectName: "calendarAgendaToggle"
                text: page.compact ? "" : (page.agendaExpanded ? "Скрыть агенду" : "Агенда")
                iconName: "note"
                variant: page.agendaExpanded ? "secondary" : "ghost"
                Accessible.name: page.agendaExpanded ? "Скрыть агенду" : "Показать агенду"
                onClicked: page.agendaExpanded = !page.agendaExpanded
                ToolTip.visible: page.compact && hovered
                ToolTip.text: page.agendaExpanded ? "Скрыть агенду" : "Агенда и ежедневные"
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 0
            spacing: Theme.spacingLg

            ColumnLayout {
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumWidth: 0
                Layout.minimumHeight: 0
                spacing: Theme.spacingSm

                Panel {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    Layout.minimumHeight: page.compact ? 300 : 360
                    clip: true
                    elevationOpacity: 0.08

                    CalendarTimeGrid {
                        id: timeGrid
                        anchors.fill: parent
                        anchors.margins: 1
                        days: calendarVm.gridDays
                        currentTimeData: calendarVm.currentTimeIndicator
                        selectedUid: calendarVm.selectedUid
                        visibleStartHour: calendarVm.visibleStartHour
                        visibleEndHour: calendarVm.visibleEndHour
                        initialScrollMinute: calendarVm.initialScrollMinute
                        compact: page.compact
                        actionsEnabled: !calendarVm.busy
                        dragging: calendarVm.dragging
                        resizing: calendarVm.resizing
                        dropPreview: calendarVm.dropPreviewGeometry
                        resizePreview: calendarVm.resizePreview
                        onDaySelected: index => calendarVm.selectDay(index)
                        onEventSelected: uid => page.selectTask(uid)
                        onEventEditRequested: uid => page.editEvent(uid)
                        onEmptyTimeSelected: (dateText, minute) => {
                            page.selectedGridDate = dateText
                            page.selectedGridMinute = minute
                        }
                        onDragStarted: (uid, sourceKind) =>
                            calendarVm.beginDrag(uid, sourceKind)
                        onDragTargetUpdated: (kind, x, y, width, height, shift) =>
                            calendarVm.updateDragPointer(kind, x, y, width, height, shift)
                        onDragFinished: calendarVm.commitDrop()
                        onDragCanceled: calendarVm.cancelDrag()
                        onInteractionPointerObserved: (x, y, shift) =>
                            page.routeGridPointerToUndated(x, y, shift)
                        onResizeStarted: (uid, edge) => calendarVm.beginResize(uid, edge)
                        onResizeTargetUpdated: (dateText, y, height, shift) =>
                            calendarVm.updateResize(dateText, y, height, shift)
                        onResizeFinished: calendarVm.commitResize()
                        onResizeCanceled: calendarVm.cancelResize()
                    }
                }

                // Existing agenda/checklist survives as a collapsible lower section.
                Panel {
                    id: agendaPanel
                    objectName: "calendarAgendaPanel"
                    visible: page.agendaExpanded
                    Layout.fillWidth: true
                    Layout.preferredHeight: page.compact ? 245 : 275
                    Layout.minimumHeight: page.compact ? 220 : 250
                    elevationOpacity: 0.07

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: Theme.spacingMd
                        spacing: Theme.spacingSm

                        RowLayout {
                            Layout.fillWidth: true
                            Layout.minimumWidth: 0
                            spacing: Theme.spacingSm

                            ColumnLayout {
                                Layout.fillWidth: true
                                Layout.minimumWidth: 0
                                spacing: 1
                                Label {
                                    text: calendarVm.selectedDayTitle
                                    font.pixelSize: Theme.fontSubtitle
                                    font.family: Theme.fontFamily
                                    font.weight: Font.DemiBold
                                    color: Theme.textPrimary
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                                Label {
                                    text: calendarVm.selectedTaskTotal + " задач · "
                                          + calendarVm.selectedCompletedCount + " выполнено · "
                                          + calendarVm.selectedDailyCount + " ежедневных"
                                    font.pixelSize: Theme.fontCaption
                                    font.family: Theme.fontFamily
                                    color: Theme.textMuted
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                }
                            }
                            AppButton {
                                text: ""
                                iconName: "plus"
                                variant: "ghost"
                                Accessible.name: "Создать задачу на выбранный день"
                                onClicked: page.newTask()
                            }
                        }

                        Flickable {
                            Layout.fillWidth: true
                            implicitHeight: 38
                            contentWidth: agendaFilters.implicitWidth
                            clip: true
                            boundsBehavior: Flickable.StopAtBounds
                            flickableDirection: Flickable.HorizontalFlick

                            SegmentedControl {
                                id: agendaFilters
                                current: calendarVm.filterMode
                                options: page.compact
                                    ? [
                                        { label: "Все", value: "all" },
                                        { label: "Актив.", value: "active" },
                                        { label: "Готовые", value: "completed" },
                                        { label: "Ежедн.", value: "daily" }
                                      ]
                                    : [
                                        { label: "Все", value: "all", count: calendarVm.selectedTaskTotal },
                                        { label: "Активные", value: "active", count: calendarVm.selectedActiveCount },
                                        { label: "Выполненные", value: "completed", count: calendarVm.selectedCompletedCount },
                                        { label: "Ежедневные", value: "daily", count: calendarVm.selectedDailyCount }
                                      ]
                                onSelected: value => calendarVm.setFilter(value)
                            }
                        }

                        BulkActionToolbar {
                            objectName: "calendarBulkToolbar"
                            visible: calendarVm.selectedCount > 1
                            vm: calendarVm
                            compact: page.compact
                            Layout.fillWidth: true
                            onDeleteRequested: confirmBulkDeleteDialog.openFor("bulk")
                        }

                        Item {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            Layout.minimumHeight: 80

                            ListView {
                                id: dayList
                                anchors.fill: parent
                                clip: true
                                spacing: Theme.spacingSm
                                visible: calendarVm.filterMode !== "daily"
                                         && calendarVm.selectedDayTasks.length > 0
                                model: calendarVm.selectedDayTasks
                                boundsBehavior: Flickable.StopAtBounds
                                ScrollBar.vertical: ScrollBar {}

                                delegate: TaskCard {
                                    id: agendaCard
                                    required property var modelData
                                    width: dayList.width
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
                                    tags: modelData.tags || []
                                    tagOverflow: modelData.tagOverflow || 0
                                    actionsEnabled: !calendarVm.busy
                                    selected: calendarVm.isTaskSelected(modelData.uid)
                                    onSelectionRequested: (uid, ctrl, shift) => page.selectTask(uid, ctrl, shift)
                                    onToggled: uid => calendarVm.toggleCompleted(uid)
                                    onDuplicateRequested: uid => calendarVm.duplicateTask(uid)
                                    onTagClicked: name => {
                                        searchVm.toggleTagFilter(name)
                                        searchVm.openSearch()
                                    }
                                    onEditRequested: uid => page.editEvent(uid)
                                    onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
                                    onSnoozeRequested: uid => snoozeMenu.openFor(uid)
                                }
                            }

                            ListView {
                                id: dailyList
                                anchors.fill: parent
                                clip: true
                                spacing: Theme.spacingSm
                                visible: calendarVm.filterMode === "daily"
                                         && calendarVm.selectedDayDailyTasks.length > 0
                                model: calendarVm.selectedDayDailyTasks
                                boundsBehavior: Flickable.StopAtBounds
                                ScrollBar.vertical: ScrollBar {}

                                delegate: Rectangle {
                                    required property var modelData
                                    width: dailyList.width
                                    implicitHeight: 48
                                    radius: Theme.radiusSmall
                                    color: modelData.done ? Theme.successSoft : Theme.surfaceMuted
                                    border.color: modelData.done
                                                  ? Theme.successSoftBorder : Theme.border
                                    border.width: 1

                                    RowLayout {
                                        anchors.fill: parent
                                        anchors.leftMargin: Theme.spacingMd
                                        anchors.rightMargin: Theme.spacingMd
                                        spacing: Theme.spacingSm
                                        Rectangle {
                                            implicitWidth: 22
                                            implicitHeight: 22
                                            radius: 7
                                            color: modelData.done ? Theme.success : "transparent"
                                            border.color: modelData.done ? Theme.success : Theme.borderStrong
                                            AppIcon {
                                                anchors.centerIn: parent
                                                visible: modelData.done
                                                name: "check"
                                                size: 14
                                                color: Theme.textOnAccent
                                            }
                                        }
                                        Label {
                                            text: (modelData.timeLabel ? modelData.timeLabel + " · " : "")
                                                  + modelData.title
                                            font.pixelSize: Theme.fontBody
                                            font.family: Theme.fontFamily
                                            font.strikeout: modelData.done
                                            color: modelData.done ? Theme.success : Theme.textPrimary
                                            elide: Text.ElideRight
                                            Layout.fillWidth: true
                                        }
                                        Badge { text: "ежедневная"; fg: Theme.accent; bg: Theme.accentSoft }
                                    }
                                    MouseArea {
                                        anchors.fill: parent
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: calendarVm.toggleDailyCompleted(modelData.uid)
                                    }
                                    Accessible.role: Accessible.CheckBox
                                    Accessible.name: modelData.title
                                    Accessible.checked: modelData.done
                                }
                            }

                            EmptyState {
                                anchors.centerIn: parent
                                width: parent.width - Theme.spacingXl
                                visible: calendarVm.filterMode === "daily"
                                         && calendarVm.selectedDayDailyTasks.length === 0
                                iconName: "refresh"
                                text: "На этот день ежедневных нет"
                                hint: "Маска дней недели не включает выбранную дату"
                            }
                            EmptyState {
                                anchors.centerIn: parent
                                width: parent.width - Theme.spacingXl
                                visible: calendarVm.filterMode !== "daily"
                                         && calendarVm.selectedDayTasks.length === 0
                                         && calendarVm.selectedTaskTotal === 0
                                iconName: "calendar"
                                text: "На этот день задач нет"
                                hint: "Почасовая сетка остаётся доступной для навигации"
                                actionText: "Создать задачу"
                                onActionClicked: page.newTask()
                            }
                            EmptyState {
                                anchors.centerIn: parent
                                width: parent.width - Theme.spacingXl
                                visible: calendarVm.filterMode !== "daily"
                                         && calendarVm.selectedDayTasks.length === 0
                                         && calendarVm.selectedTaskTotal > 0
                                iconName: "search"
                                text: "Нет задач в этом фильтре"
                                hint: "Выберите другой фильтр"
                            }
                        }
                    }
                }
            }

            UndatedTaskPanel {
                id: undatedWide
                objectName: "calendarUndatedWidePanel"
                visible: page.wide
                Layout.preferredWidth: 250
                Layout.maximumWidth: 270
                Layout.fillHeight: true
                tasks: calendarVm.undatedTasks
                selectedUid: calendarVm.selectedUid
                selectedUids: calendarVm.selectedUids
                actionsEnabled: !calendarVm.busy
                persistent: true
                onTaskSelectionRequested: (uid, ctrl, shift) =>
                    page.selectUndatedTask(uid, ctrl, shift)
                onDragStarted: (uid, sourceKind) => calendarVm.beginDrag(uid, sourceKind)
                onDragPointer: (uid, x, y, shift) =>
                    page.routeUndatedPointer(undatedWide, x, y, shift)
                onDragFinished: calendarVm.commitDrop()
                onDragCanceled: calendarVm.cancelDrag()
            }

            // Wide layout keeps the inspector as a stable side rail.
            ColumnLayout {
                visible: page.wide
                Layout.preferredWidth: 320
                Layout.maximumWidth: 320
                Layout.fillHeight: true
                Layout.minimumHeight: 0
                spacing: Theme.spacingMd

                TaskInspector {
                    visible: calendarVm.selectedCount === 1
                             && page.selTask !== null && page.selTask !== undefined
                    task: page.selTask
                    busy: calendarVm.busy
                    snoozeActions: page.selTask
                                   ? calendarVm.snoozeActionsFor(page.selTask.uid) : []
                    taskPresets: page.selTask
                                 ? calendarVm.taskPresetsFor(page.selTask.uid) : []
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    onEditRequested: uid => page.editEvent(uid)
                    onToggleRequested: uid => calendarVm.toggleCompleted(uid)
                    onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
                    onDuplicateRequested: uid => calendarVm.duplicateTask(uid)
                    onPostponeRequested: (uid, action) => calendarVm.postponeTask(uid, action)
                    onPresetRequested: (uid, presetId) => calendarVm.applyTaskPreset(uid, presetId)
                    onPickRequested: uid => page.editEvent(uid)
                    onCloseRequested: page.clearSelection()
                }

                Panel {
                    visible: calendarVm.selectedCount === 0 && !page.selTask
                    Layout.fillWidth: true
                    implicitHeight: summaryColumn.implicitHeight + 2 * Theme.spacingLg

                    ColumnLayout {
                        id: summaryColumn
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: Theme.spacingLg
                        spacing: Theme.spacingMd
                        Label {
                            text: "Сводка дня"
                            font.pixelSize: Theme.fontSubtitle
                            font.family: Theme.fontFamily
                            font.weight: Font.DemiBold
                            color: Theme.textPrimary
                        }
                        Label {
                            text: calendarVm.selectedDayTitle
                            font.pixelSize: Theme.fontBody
                            font.family: Theme.fontFamily
                            color: Theme.textSecondary
                        }
                        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                        Label {
                            text: "Всего " + calendarVm.selectedTaskTotal
                                  + " · активных " + calendarVm.selectedActiveCount
                                  + " · выполнено " + calendarVm.selectedCompletedCount
                            font.pixelSize: Theme.fontBody
                            font.family: Theme.fontFamily
                            color: Theme.textSecondary
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                        Label {
                            text: "Выберите событие в сетке, чтобы открыть инспектор"
                            font.pixelSize: Theme.fontCaption
                            font.family: Theme.fontFamily
                            color: Theme.textMuted
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                }
                Item { visible: !page.selTask; Layout.fillHeight: true }
            }
        }
    }

    Drawer {
        id: undatedDrawer
        edge: page.compact ? Qt.BottomEdge : Qt.LeftEdge
        width: page.compact ? page.width : Math.min(320, page.width - 40)
        height: page.compact ? Math.min(430, page.height * 0.58) : page.height
        interactive: visible
        background: Rectangle {
            color: Theme.surface
            border.color: Theme.border
            border.width: 1
        }
        UndatedTaskPanel {
            id: undatedDrawerPanel
            anchors.fill: parent
            anchors.margins: Theme.spacingSm
            tasks: calendarVm.undatedTasks
            selectedUid: calendarVm.selectedUid
            selectedUids: calendarVm.selectedUids
            actionsEnabled: !calendarVm.busy
            onTaskSelectionRequested: (uid, ctrl, shift) =>
                page.selectUndatedTask(uid, ctrl, shift)
            onDragStarted: (uid, sourceKind) => calendarVm.beginDrag(uid, sourceKind)
            onDragPointer: (uid, x, y, shift) =>
                page.routeUndatedPointer(undatedDrawerPanel, x, y, shift)
            onDragFinished: {
                if (calendarVm.commitDrop())
                    undatedDrawer.close()
            }
            onDragCanceled: calendarVm.cancelDrag()
        }
    }

    Drawer {
        id: inspectorDrawer
        edge: Qt.RightEdge
        width: page.compact ? Math.min(page.width - 40, 360) : 360
        height: page.height
        interactive: visible

        onClosed: {
            if (!editorDialog.visible && !confirmDeleteDialog.visible)
                calendarVm.clearSelection()
        }
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
            busy: calendarVm.busy
            snoozeActions: page.selTask
                           ? calendarVm.snoozeActionsFor(page.selTask.uid) : []
            taskPresets: page.selTask
                         ? calendarVm.taskPresetsFor(page.selTask.uid) : []
            elevationOpacity: 0
            borderWidth: 0
            onEditRequested: uid => page.editEvent(uid)
            onToggleRequested: uid => calendarVm.toggleCompleted(uid)
            onDeleteRequested: uid => {
                inspectorDrawer.close()
                confirmDeleteDialog.openFor(uid)
            }
            onDuplicateRequested: uid => calendarVm.duplicateTask(uid)
            onPostponeRequested: (uid, action) => calendarVm.postponeTask(uid, action)
            onPresetRequested: (uid, presetId) => calendarVm.applyTaskPreset(uid, presetId)
            onPickRequested: uid => page.editEvent(uid)
            onCloseRequested: {
                inspectorDrawer.close()
                calendarVm.clearSelection()
            }
        }
    }

    SnoozeMenu {
        id: snoozeMenu
        vm: calendarVm
        onPickRequested: uid => page.editEvent(uid)
        onClosed: if (!editorDialog.visible) page.restoreFocus()
    }

    TaskEditorDialog {
        id: editorDialog
        objectName: "calendarEditorDialog"
        vm: calendarVm
        onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
        onClosed: if (!confirmDeleteDialog.visible) page.restoreFocus()
    }

    ConfirmDialog {
        id: confirmDeleteDialog
        headerText: "Удалить задачу?"
        message: "Задача будет помечена удалённой; связанное событие будет "
                 + "удалено только при следующей ручной синхронизации."
        onConfirmed: uid => calendarVm.deleteTask(uid)
        onClosed: page.restoreFocus()
    }
    ConfirmDialog {
        id: confirmBulkDeleteDialog
        headerText: "Удалить выбранные задачи?"
        message: "Будут удалены только выбранные задачи из текущей агенды и панели без даты. Связанные события попадут в очередь ручного синка."
        onConfirmed: calendarVm.bulkDelete()
        onClosed: page.restoreFocus()
    }
}
