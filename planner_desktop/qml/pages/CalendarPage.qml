import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"
import "../theme"

// Календарь: недельная полоса + агенда выбранного дня с фильтрами
// (все / активные / выполненные / ежедневные), сводкой дня и инспектором
// выбранной задачи (тот же TaskInspector, что на «Сегодня»). Почасовая сетка
// и drag-and-drop — следующая фаза (см. FEATURE_PARITY.md).
Item {
    id: page

    // Режим раскладки считает Python (domain/layout.py).
    readonly property string layoutMode: uiVm.layoutModeFor(width)
    readonly property bool wide: layoutMode === "wide"
    readonly property bool compact: layoutMode === "compact"
    property var focusReturnItem: null

    // ---- выбор задачи живёт в ViewModel ----
    readonly property var selTask: calendarVm.selectedTask
    readonly property bool dialogsOpen: editorDialog.visible
                                        || confirmDeleteDialog.visible
                                        || snoozeMenu.visible
                                        || inspectorDrawer.visible

    function restoreFocus() {
        var item = focusReturnItem
        focusReturnItem = null
        if (item && item.visible && item.enabled)
            item.forceActiveFocus()
        else if (dayList.visible)
            dayList.forceActiveFocus()
        else
            page.forceActiveFocus()
    }

    function selectTask(uid) {
        calendarVm.selectTask(uid)
        if (!page.wide && calendarVm.selectedUid !== "")
            inspectorDrawer.open()
    }

    // Для клавиатурного Ctrl+N / Ctrl+Shift+N: задача на выбранный день.
    function newTask() { editorDialog.openForCreate(calendarVm.selectedDateText) }
    function newScheduledTask() {
        editorDialog.openForCreateScheduled(calendarVm.selectedDateText)
    }
    function openSelected() {
        if (calendarVm.selectedUid !== "")
            editorDialog.openForEdit(calendarVm.selectedUid)
    }
    function toggleSelected() {
        if (calendarVm.selectedUid !== "")
            calendarVm.toggleCompleted(calendarVm.selectedUid)
    }
    function deleteSelected() {
        if (calendarVm.selectedUid !== "")
            confirmDeleteDialog.openFor(calendarVm.selectedUid)
    }
    function clearSelection() {
        inspectorDrawer.close()
        calendarVm.clearSelection()
    }

    // Клавиатурная навигация по дням (Main.qml зовёт при стрелках влево/вправо).
    function selectPrevDay() {
        if (calendarVm.selectedIndex > 0)
            calendarVm.selectDay(calendarVm.selectedIndex - 1)
        else { calendarVm.previousWeek(); calendarVm.selectDay(6) }
    }
    function selectNextDay() {
        if (calendarVm.selectedIndex < 6)
            calendarVm.selectDay(calendarVm.selectedIndex + 1)
        else { calendarVm.nextWeek(); calendarVm.selectDay(0) }
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: page.compact ? Theme.spacingLg : Theme.spacingXl
        spacing: page.compact ? Theme.spacingMd : Theme.spacingLg

        // ---- Шапка: заголовок недели + навигация ----
        PageHeader {
            title: "Календарь"
            subtitle: calendarVm.weekTitle
            Layout.fillWidth: true

            AppButton {
                variant: "secondary"
                iconName: "chevron-left"
                onClicked: calendarVm.previousWeek()
                ToolTip.visible: hovered
                ToolTip.text: "Предыдущая неделя"
                ToolTip.delay: 500
            }
            AppButton {
                text: "Сегодня"
                variant: calendarVm.isCurrentWeek ? "ghost" : "secondary"
                onClicked: calendarVm.goToToday()
            }
            AppButton {
                variant: "secondary"
                iconName: "chevron-right"
                onClicked: calendarVm.nextWeek()
                ToolTip.visible: hovered
                ToolTip.text: "Следующая неделя"
                ToolTip.delay: 500
            }
            AppButton {
                text: page.compact ? "" : "Задача"
                variant: "primary"
                iconName: "plus"
                onClicked: page.newTask()
                ToolTip.visible: page.compact && hovered
                ToolTip.text: "Задача на выбранный день"
            }
        }

        // ---- Недельная полоса (в компактном режиме прокручивается) ----
        Flickable {
            id: weekStrip
            Layout.fillWidth: true
            implicitHeight: weekRow.implicitHeight
            contentWidth: weekRow.width
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            flickableDirection: Flickable.HorizontalFlick

            RowLayout {
                id: weekRow
                width: Math.max(page.compact ? 7 * 104 + 6 * Theme.spacingSm : 0,
                                weekStrip.width)
                spacing: Theme.spacingSm

                Repeater {
                    model: calendarVm.weekDays
                    delegate: Panel {
                        id: dayCell
                        required property var modelData
                        required property int index

                        Layout.fillWidth: true
                        Layout.minimumWidth: 96
                        implicitHeight: 96
                        radius: Theme.radiusMedium
                        color: modelData.isSelected ? Theme.accentSoft
                             : dayMouse.containsMouse ? Theme.surfaceHover : Theme.surface
                        borderColor: modelData.isToday ? Theme.accent
                                   : modelData.isSelected ? Theme.accentSoftBorder : Theme.border
                        borderWidth: modelData.isToday ? 2 : 1
                        elevationOpacity: modelData.isSelected ? 0.16 : Theme.elevCardOpacity
                        elevationY: modelData.isSelected ? 8 : Theme.elevCardY

                        ColumnLayout {
                            anchors.fill: parent
                            anchors.margins: Theme.spacingMd
                            spacing: 2

                            Label {
                                text: dayCell.modelData.label.toUpperCase()
                                font.pixelSize: Theme.fontCaption
                                font.family: Theme.fontFamily
                                font.weight: Font.DemiBold
                                font.letterSpacing: 0.5
                                color: dayCell.modelData.isSelected ? Theme.accent : Theme.textMuted
                            }
                            Label {
                                text: dayCell.modelData.dateText
                                font.pixelSize: 17
                                font.family: Theme.fontFamily
                                font.weight: Font.DemiBold
                                color: dayCell.modelData.isToday ? Theme.accent : Theme.textPrimary
                            }
                            Item { Layout.fillHeight: true }
                            Badge {
                                visible: dayCell.modelData.taskCount > 0
                                text: String(dayCell.modelData.taskCount)
                                fg: dayCell.modelData.isSelected ? Theme.accent : Theme.textSecondary
                                bg: dayCell.modelData.isSelected ? Theme.surface : Theme.surfacePressed
                            }
                            Label {
                                visible: dayCell.modelData.taskCount === 0
                                text: "—"
                                font.pixelSize: Theme.fontCaption
                                font.family: Theme.fontFamily
                                color: Theme.textMuted
                                opacity: 0.6
                            }
                        }

                        MouseArea {
                            id: dayMouse
                            anchors.fill: parent
                            hoverEnabled: true
                            cursorShape: Qt.PointingHandCursor
                            onClicked: calendarVm.selectDay(dayCell.index)
                        }
                    }
                }
            }
        }

        // ---- Заголовок выбранного дня: дата + счётчики + фильтры ----
        Panel {
            Layout.fillWidth: true
            implicitHeight: headerCol.implicitHeight + 2 * Theme.spacingLg

            ColumnLayout {
                id: headerCol
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.margins: Theme.spacingLg
                spacing: Theme.spacingMd

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingSm

                    ColumnLayout {
                        spacing: 2
                        Layout.fillWidth: true
                        Label {
                            text: calendarVm.selectedDayTitle
                            font.pixelSize: Theme.fontSubtitle
                            font.family: Theme.fontFamily
                            font.weight: Font.DemiBold
                            color: Theme.textPrimary
                        }
                        RowLayout {
                            spacing: Theme.spacingSm
                            Label {
                                text: calendarVm.selectedTaskTotal + " "
                                      + Theme.plural(calendarVm.selectedTaskTotal, "задача", "задачи", "задач")
                                font.pixelSize: Theme.fontCaption
                                font.family: Theme.fontFamily
                                color: Theme.textMuted
                            }
                            Label { text: "·"; color: Theme.textMuted; font.pixelSize: Theme.fontCaption }
                            Label {
                                text: calendarVm.selectedCompletedCount + " выполнено"
                                font.pixelSize: Theme.fontCaption
                                font.family: Theme.fontFamily
                                color: Theme.success
                            }
                            Label { text: "·"; color: Theme.textMuted; font.pixelSize: Theme.fontCaption }
                            Label {
                                text: calendarVm.selectedDailyCount + " ежедневных"
                                font.pixelSize: Theme.fontCaption
                                font.family: Theme.fontFamily
                                color: Theme.accent
                            }
                        }
                    }
                    AppButton {
                        text: "Задача"
                        variant: "ghost"
                        iconName: "plus"
                        visible: !page.compact
                        onClicked: page.newTask()
                    }
                }

                SegmentedControl {
                    Layout.fillWidth: false
                    current: calendarVm.filterMode
                    options: page.compact
                        ? [
                            { label: "Все", value: "all" },
                            { label: "Активные", value: "active" },
                            { label: "Готовые", value: "completed" },
                            { label: "Ежедн.", value: "daily" }
                        ]
                        : [
                            { label: "Все", value: "all", count: calendarVm.selectedTaskTotal },
                            { label: "Активные", value: "active", count: calendarVm.selectedActiveCount },
                            { label: "Выполненные", value: "completed", count: calendarVm.selectedCompletedCount },
                            { label: "Ежедневные", value: "daily", count: calendarVm.selectedDailyCount }
                        ]
                    onSelected: value => {
                        calendarVm.setFilter(value)
                        page.clearSelection()
                    }
                }
            }
        }

        // ---- Агенда выбранного дня + правый инспектор/сводка ----
        RowLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: Theme.spacingLg

            Panel {
                Layout.fillWidth: true
                Layout.fillHeight: true

                // === агенда задач (фильтры all/active/completed) ===
                ListView {
                    id: dayList
                    anchors.fill: parent
                    anchors.margins: Theme.spacingLg
                    clip: true
                    spacing: Theme.spacingSm
                    visible: calendarVm.filterMode !== "daily"
                                && calendarVm.selectedDayTasks.length > 0
                    model: calendarVm.selectedDayTasks
                    boundsBehavior: Flickable.StopAtBounds
                    ScrollBar.vertical: ScrollBar {}

                    delegate: Item {
                        id: agendaRow
                        required property var modelData
                        width: dayList.width
                        implicitHeight: Math.max(agendaCard.implicitHeight, 56)

                        // В компактном режиме временная колонка прячется —
                        // время остаётся в бейдже карточки.
                        readonly property int gutter: page.compact ? 0 : 66
                        readonly property string startText: modelData.isAllDay
                            ? "весь день"
                            : (modelData.timeLabel ? modelData.timeLabel.split("–")[0] : "")

                        Rectangle {
                            visible: !page.compact
                            x: 47
                            width: 2
                            y: 0
                            height: agendaRow.height + dayList.spacing
                            color: Theme.border
                            opacity: 0.7
                        }
                        Rectangle {
                            visible: !page.compact
                            x: 43
                            y: 8
                            width: 10
                            height: 10
                            radius: 5
                            color: agendaRow.modelData.completed ? Theme.success
                                 : agendaRow.modelData.isAllDay ? Theme.accentSoftBorder : Theme.accent
                            border.color: Theme.surface
                            border.width: 2
                        }
                        Label {
                            visible: !page.compact
                            width: 40
                            x: 0
                            y: 4
                            text: agendaRow.startText
                            horizontalAlignment: Text.AlignRight
                            wrapMode: Text.WordWrap
                            font.pixelSize: Theme.fontCaption
                            font.family: Theme.fontFamily
                            font.weight: Font.DemiBold
                            color: Theme.textSecondary
                        }

                        TaskCard {
                            id: agendaCard
                            x: agendaRow.gutter
                            width: agendaRow.width - agendaRow.gutter
                            uid: agendaRow.modelData.uid
                            title: agendaRow.modelData.title
                            notes: agendaRow.modelData.notes
                            timeLabel: agendaRow.modelData.timeLabel
                            isAllDay: agendaRow.modelData.isAllDay
                            priority: agendaRow.modelData.priority
                            completed: agendaRow.modelData.completed
                            hasPendingSync: agendaRow.modelData.hasPendingSync
                            isLinked: agendaRow.modelData.isLinked
                            isScheduled: agendaRow.modelData.isScheduled
                            isRecurring: agendaRow.modelData.isRecurring
                            actionsEnabled: !calendarVm.busy
                            selected: calendarVm.selectedUid === agendaRow.modelData.uid
                            onSelectRequested: uid => page.selectTask(uid)
                            onToggled: uid => calendarVm.toggleCompleted(uid)
                            onEditRequested: uid => {
                                page.focusReturnItem = agendaCard
                                editorDialog.openForEdit(uid)
                            }
                            onDeleteRequested: uid => {
                                page.focusReturnItem = agendaCard
                                confirmDeleteDialog.openFor(uid)
                            }
                            onSnoozeRequested: uid => {
                                page.focusReturnItem = agendaCard
                                snoozeMenu.openFor(uid)
                            }
                        }
                    }
                }

                // === ежедневный чек-лист выбранного дня (фильтр daily) ===
                ListView {
                    id: dailyList
                    anchors.fill: parent
                    anchors.margins: Theme.spacingLg
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
                        implicitHeight: 52
                        radius: Theme.radiusMedium
                        color: modelData.done ? Theme.successSoft
                             : dailyHover.hovered ? Theme.surfaceHover : Theme.surface
                        border.color: modelData.done ? Theme.successSoftBorder : Theme.border
                        border.width: 1
                        Behavior on color { ColorAnimation { duration: 120 } }

                        HoverHandler { id: dailyHover }

                        RowLayout {
                            anchors.fill: parent
                            anchors.leftMargin: Theme.spacingLg
                            anchors.rightMargin: Theme.spacingLg
                            spacing: Theme.spacingMd

                            Rectangle {
                                Layout.alignment: Qt.AlignVCenter
                                implicitWidth: 22
                                implicitHeight: 22
                                radius: 7
                                color: modelData.done ? Theme.success : "transparent"
                                border.color: modelData.done ? Theme.success : Theme.borderStrong
                                border.width: modelData.done ? 0 : 1.6
                                AppIcon {
                                    anchors.centerIn: parent
                                    name: "check"; size: 15; strokeWidth: 2.4
                                    color: Theme.textOnAccent
                                    visible: modelData.done
                                }
                            }
                            Label {
                                text: (modelData.timeLabel && modelData.timeLabel.length > 0
                                       ? modelData.timeLabel + " · " : "") + modelData.title
                                font.pixelSize: Theme.fontBody
                                font.family: Theme.fontFamily
                                font.strikeout: modelData.done
                                color: modelData.done ? Theme.success : Theme.textPrimary
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Badge {
                                text: "ежедневная"
                                fg: Theme.accent
                                bg: Theme.accentSoft
                                Layout.alignment: Qt.AlignVCenter
                            }
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: calendarVm.toggleDailyCompleted(modelData.uid)
                        }
                    }
                }

                // === пустые состояния ===
                EmptyState {
                    anchors.centerIn: parent
                    width: parent.width - 2 * Theme.spacingXl
                    visible: calendarVm.filterMode === "daily"
                             && calendarVm.selectedDayDailyTasks.length === 0
                    iconName: "refresh"
                    text: "На этот день ежедневных нет"
                    hint: "Ежедневные задачи настраиваются на «Сегодня» и появляются здесь по маске дней недели"
                }
                EmptyState {
                    anchors.centerIn: parent
                    width: parent.width - 2 * Theme.spacingXl
                    visible: calendarVm.filterMode !== "daily"
                             && calendarVm.selectedDayTasks.length === 0
                             && calendarVm.selectedTaskTotal === 0
                    iconName: "calendar"
                    text: "На этот день задач нет"
                    hint: "Запланируйте что-нибудь на " + calendarVm.selectedDayTitle
                    actionText: "Создать задачу на этот день"
                    onActionClicked: page.newTask()
                }
                EmptyState {
                    anchors.centerIn: parent
                    width: parent.width - 2 * Theme.spacingXl
                    visible: calendarVm.filterMode !== "daily"
                             && calendarVm.selectedDayTasks.length === 0
                             && calendarVm.selectedTaskTotal > 0
                    iconName: "search"
                    text: "Нет задач в этом фильтре"
                    hint: "Переключите фильтр выше, чтобы увидеть остальные задачи дня"
                }
            }

            // ---- правый столбец: инспектор задачи / сводка дня (wide) ----
            ColumnLayout {
                id: rail
                visible: page.wide
                Layout.preferredWidth: 320
                Layout.maximumWidth: 320
                Layout.fillHeight: true
                Layout.alignment: Qt.AlignTop
                spacing: Theme.spacingLg

                TaskInspector {
                    visible: page.selTask !== null && page.selTask !== undefined
                    task: page.selTask
                    busy: calendarVm.busy
                    snoozeActions: page.selTask
                                   ? calendarVm.snoozeActionsFor(page.selTask.uid) : []
                    taskPresets: page.selTask
                                 ? calendarVm.taskPresetsFor(page.selTask.uid) : []
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    onEditRequested: uid => editorDialog.openForEdit(uid)
                    onToggleRequested: uid => calendarVm.toggleCompleted(uid)
                    onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
                    onPostponeRequested: (uid, action) => calendarVm.postponeTask(uid, action)
                    onPresetRequested: (uid, presetId) => calendarVm.applyTaskPreset(uid, presetId)
                    onPickRequested: uid => editorDialog.openForEdit(uid)
                    onCloseRequested: page.clearSelection()
                }

                // сводка дня, когда ничего не выбрано
                Panel {
                    visible: !page.selTask
                    Layout.fillWidth: true
                    implicitHeight: summaryCol.implicitHeight + 2 * Theme.spacingLg

                    ColumnLayout {
                        id: summaryCol
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
                        component SummaryRow: RowLayout {
                            property string label: ""
                            property int value: 0
                            property color tint: Theme.textSecondary
                            Layout.fillWidth: true
                            spacing: Theme.spacingSm
                            Label {
                                text: parent.label
                                font.pixelSize: Theme.fontBody
                                font.family: Theme.fontFamily
                                color: Theme.textSecondary
                                Layout.fillWidth: true
                            }
                            Label {
                                text: String(parent.value)
                                font.pixelSize: Theme.fontBody
                                font.family: Theme.fontFamily
                                font.weight: Font.DemiBold
                                color: parent.tint
                            }
                        }
                        SummaryRow { label: "Всего задач"; value: calendarVm.selectedTaskTotal; tint: Theme.textPrimary }
                        SummaryRow { label: "Активные"; value: calendarVm.selectedActiveCount; tint: Theme.accent }
                        SummaryRow { label: "Выполнено"; value: calendarVm.selectedCompletedCount; tint: Theme.success }
                        SummaryRow { label: "Ежедневные"; value: calendarVm.selectedDailyCount; tint: Theme.textSecondary }

                        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

                        Label {
                            text: "Выберите задачу, чтобы увидеть подробности"
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

    // ---- инспектор-панель для normal/compact ----
    Drawer {
        id: inspectorDrawer
        edge: Qt.RightEdge
        width: page.compact ? Math.min(page.width - 40, 360) : 360
        height: page.height
        interactive: visible

        onClosed: calendarVm.clearSelection()

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
            onEditRequested: uid => { inspectorDrawer.close(); editorDialog.openForEdit(uid) }
            onToggleRequested: uid => calendarVm.toggleCompleted(uid)
            onDeleteRequested: uid => { inspectorDrawer.close(); confirmDeleteDialog.openFor(uid) }
            onPostponeRequested: (uid, action) => calendarVm.postponeTask(uid, action)
            onPresetRequested: (uid, presetId) => calendarVm.applyTaskPreset(uid, presetId)
            onPickRequested: uid => {
                inspectorDrawer.close()
                editorDialog.openForEdit(uid)
            }
            onCloseRequested: inspectorDrawer.close()
        }
    }

    SnoozeMenu {
        id: snoozeMenu
        vm: calendarVm
        onPickRequested: uid => editorDialog.openForEdit(uid)
        onClosed: {
            if (!editorDialog.visible)
                page.restoreFocus()
        }
    }

    TaskEditorDialog {
        id: editorDialog
        objectName: "calendarEditorDialog"
        vm: calendarVm
        onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
        onClosed: {
            if (!confirmDeleteDialog.visible)
                page.restoreFocus()
        }
    }

    ConfirmDialog {
        id: confirmDeleteDialog
        headerText: "Удалить задачу?"
        message: "Задача будет помечена удалённой; если её событие уже есть "
                 + "в календаре, удаление события встанет в очередь синка."
        onConfirmed: uid => calendarVm.deleteTask(uid)
        onClosed: page.restoreFocus()
    }
}
