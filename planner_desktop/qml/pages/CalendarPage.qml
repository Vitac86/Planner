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

    property bool wide: width >= 1040

    // ---- выбор задачи (для инспектора в правой сводке) ----
    property string selectedUid: ""
    property var selectedTask: {
        if (selectedUid === "" || calendarVm.filterMode === "daily")
            return null
        var list = calendarVm.selectedDayTasks
        for (var i = 0; i < list.length; i++)
            if (list[i].uid === selectedUid)
                return list[i]
        return null  // выбранная задача исчезла (удалена/выполнена/скрыта фильтром)
    }

    // Для клавиатурного Ctrl+N: новая задача на выбранный день.
    function newTask() { editorDialog.openForCreate(calendarVm.selectedDateText) }

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
        anchors.margins: Theme.spacingXl
        spacing: Theme.spacingLg

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
                text: "Задача"
                variant: "primary"
                iconName: "plus"
                onClicked: page.newTask()
            }
        }

        // ---- Недельная полоса ----
        RowLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true

            Repeater {
                model: calendarVm.weekDays
                delegate: Panel {
                    id: dayCell
                    required property var modelData
                    required property int index

                    Layout.fillWidth: true
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
                        onClicked: page.newTask()
                    }
                }

                SegmentedControl {
                    Layout.fillWidth: false
                    current: calendarVm.filterMode
                    options: [
                        { label: "Все", value: "all", count: calendarVm.selectedTaskTotal },
                        { label: "Активные", value: "active", count: calendarVm.selectedActiveCount },
                        { label: "Выполненные", value: "completed", count: calendarVm.selectedCompletedCount },
                        { label: "Ежедневные", value: "daily", count: calendarVm.selectedDailyCount }
                    ]
                    onSelected: value => {
                        calendarVm.setFilter(value)
                        page.selectedUid = ""
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

                        readonly property string startText: modelData.isAllDay
                            ? "весь день"
                            : (modelData.timeLabel ? modelData.timeLabel.split("–")[0] : "")

                        Rectangle {
                            x: 47
                            width: 2
                            y: 0
                            height: agendaRow.height + dayList.spacing
                            color: Theme.border
                            opacity: 0.7
                        }
                        Rectangle {
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
                            x: 66
                            width: agendaRow.width - 66
                            uid: agendaRow.modelData.uid
                            title: agendaRow.modelData.title
                            notes: agendaRow.modelData.notes
                            timeLabel: agendaRow.modelData.timeLabel
                            isAllDay: agendaRow.modelData.isAllDay
                            priority: agendaRow.modelData.priority
                            completed: agendaRow.modelData.completed
                            hasPendingSync: agendaRow.modelData.hasPendingSync
                            isLinked: agendaRow.modelData.isLinked
                            selected: page.selectedUid === agendaRow.modelData.uid
                            onSelectRequested: uid => page.selectedUid = uid
                            onToggled: uid => calendarVm.toggleCompleted(uid)
                            onEditRequested: uid => editorDialog.openForEdit(uid)
                            onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
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
                    glyph: "🔁"
                    text: "На этот день ежедневных нет"
                    hint: "Ежедневные задачи настраиваются на «Сегодня» и появляются здесь по маске дней недели"
                }
                EmptyState {
                    anchors.centerIn: parent
                    width: parent.width - 2 * Theme.spacingXl
                    visible: calendarVm.filterMode !== "daily"
                             && calendarVm.selectedDayTasks.length === 0
                             && calendarVm.selectedTaskTotal === 0
                    glyph: "📅"
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
                    glyph: "🔎"
                    text: "Нет задач в этом фильтре"
                    hint: "Переключите фильтр выше, чтобы увидеть остальные задачи дня"
                }
            }

            // ---- правый столбец: инспектор задачи / сводка дня ----
            ColumnLayout {
                id: rail
                visible: page.wide
                Layout.preferredWidth: 320
                Layout.maximumWidth: 320
                Layout.fillHeight: true
                Layout.alignment: Qt.AlignTop
                spacing: Theme.spacingLg

                TaskInspector {
                    visible: page.selectedTask !== null
                    task: page.selectedTask
                    Layout.fillWidth: true
                    onEditRequested: uid => editorDialog.openForEdit(uid)
                    onToggleRequested: uid => calendarVm.toggleCompleted(uid)
                    onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
                    onCloseRequested: page.selectedUid = ""
                }

                // сводка дня, когда ничего не выбрано
                Panel {
                    visible: page.selectedTask === null
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

                Item { Layout.fillHeight: true }
            }
        }
    }

    TaskEditorDialog {
        id: editorDialog
        objectName: "calendarEditorDialog"
        vm: calendarVm
    }

    ConfirmDialog {
        id: confirmDeleteDialog
        headerText: "Удалить задачу?"
        message: "Задача будет помечена удалённой; если её событие уже есть "
                 + "в календаре, удаление события встанет в очередь синка."
        onConfirmed: uid => {
            calendarVm.deleteTask(uid)
            if (page.selectedUid === uid)
                page.selectedUid = ""
        }
    }
}
