import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"
import "../theme"

// Календарь: недельная полоса + агенда выбранного дня (список задач с
// временной колонкой). Почасовая сетка и drag-and-drop — следующая фаза
// (см. FEATURE_PARITY.md).
Item {
    id: page

    // Сколько задач выбранного дня выполнено (для сводки дня).
    property int selectedCompleted: {
        var list = calendarVm.selectedDayTasks
        var n = 0
        for (var i = 0; i < list.length; i++)
            if (list[i].completed) n++
        return n
    }

    // Для клавиатурного Ctrl+N: новая задача на выбранный день.
    function newTask() { editorDialog.openForCreate(calendarVm.selectedDateText) }

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

        // ---- Сводка выбранного дня ----
        SectionHeader {
            title: calendarVm.selectedDayTitle
            count: calendarVm.selectedDayTasks.length
            Layout.fillWidth: true

            Badge {
                visible: calendarVm.selectedDayTasks.length > 0
                text: page.selectedCompleted + " из " + calendarVm.selectedDayTasks.length + " готово"
                fg: page.selectedCompleted === calendarVm.selectedDayTasks.length
                    ? Theme.success : Theme.textSecondary
                bg: page.selectedCompleted === calendarVm.selectedDayTasks.length
                    ? Theme.successSoft : Theme.surfacePressed
            }
            AppButton {
                text: "Задача"
                variant: "ghost"
                iconName: "plus"
                onClicked: page.newTask()
            }
        }

        // ---- Агенда выбранного дня ----
        Panel {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: dayList
                anchors.fill: parent
                anchors.margins: Theme.spacingLg
                clip: true
                spacing: Theme.spacingSm
                model: calendarVm.selectedDayTasks
                boundsBehavior: Flickable.StopAtBounds

                delegate: Item {
                    id: agendaRow
                    required property var modelData
                    width: dayList.width
                    implicitHeight: Math.max(agendaCard.implicitHeight, 56)

                    readonly property string startText: modelData.isAllDay
                        ? "весь день"
                        : (modelData.timeLabel ? modelData.timeLabel.split("–")[0] : "")

                    // временная колонка + вертикальная линия агенды
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
                        color: modelData.completed ? Theme.success
                             : modelData.isAllDay ? Theme.accentSoftBorder : Theme.accent
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
                        onToggled: uid => calendarVm.toggleCompleted(uid)
                        onEditRequested: uid => editorDialog.openForEdit(uid)
                        onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
                    }
                }
            }

            EmptyState {
                anchors.centerIn: parent
                width: parent.width - 2 * Theme.spacingXl
                visible: calendarVm.selectedDayTasks.length === 0
                glyph: "📅"
                text: "На этот день задач нет"
                hint: "Запланируйте что-нибудь на " + calendarVm.selectedDayTitle
                actionText: "Создать задачу на этот день"
                onActionClicked: page.newTask()
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
        onConfirmed: uid => calendarVm.deleteTask(uid)
    }
}
