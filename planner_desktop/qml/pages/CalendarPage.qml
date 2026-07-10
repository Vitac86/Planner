import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"
import "../theme"

// Календарь MVP: недельная полоса + список задач выбранного дня.
// Почасовая сетка и drag-and-drop — следующая фаза (см. FEATURE_PARITY.md).
Item {
    id: page

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
                onClicked: editorDialog.openForCreate(calendarVm.selectedDateText)
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

        // ---- Список выбранного дня ----
        SectionHeader {
            title: calendarVm.selectedDayTitle
            count: calendarVm.selectedDayTasks.length
            Layout.fillWidth: true
        }

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

                delegate: TaskCard {
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
                    onToggled: uid => calendarVm.toggleCompleted(uid)
                    onEditRequested: uid => editorDialog.openForEdit(uid)
                    onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
                }
            }

            EmptyState {
                anchors.centerIn: parent
                visible: calendarVm.selectedDayTasks.length === 0
                glyph: "📅"
                text: "На этот день задач нет"
                hint: "Нажмите «Задача», чтобы запланировать задачу на выбранный день"
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
