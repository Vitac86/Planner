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
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm

            ColumnLayout {
                spacing: 2

                Label {
                    text: "Календарь"
                    font.pixelSize: Theme.fontDisplay
                    font.weight: Font.DemiBold
                    color: Theme.textPrimary
                }
                Label {
                    text: calendarVm.weekTitle
                    font.pixelSize: Theme.fontBody
                    color: Theme.textMuted
                }
            }
            Item { Layout.fillWidth: true }

            AppButton {
                text: "‹"
                variant: "secondary"
                onClicked: calendarVm.previousWeek()
                ToolTip.visible: hovered
                ToolTip.text: "Предыдущая неделя"
                ToolTip.delay: 600
            }
            AppButton {
                text: "Сегодня"
                variant: calendarVm.isCurrentWeek ? "ghost" : "secondary"
                onClicked: calendarVm.goToToday()
            }
            AppButton {
                text: "›"
                variant: "secondary"
                onClicked: calendarVm.nextWeek()
                ToolTip.visible: hovered
                ToolTip.text: "Следующая неделя"
                ToolTip.delay: 600
            }
            AppButton {
                text: "＋ Задача"
                variant: "primary"
                onClicked: editorDialog.openForCreate(calendarVm.selectedDateText)
            }
        }

        // ---- Недельная полоса ----
        RowLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true

            Repeater {
                model: calendarVm.weekDays
                delegate: Rectangle {
                    required property var modelData
                    required property int index

                    Layout.fillWidth: true
                    implicitHeight: 92
                    radius: Theme.radiusMedium
                    color: modelData.isSelected ? Theme.accentSoft
                         : dayMouse.containsMouse ? Theme.surfaceHover : Theme.surface
                    border.color: modelData.isToday ? Theme.accent
                                : modelData.isSelected ? Theme.accentSoftBorder : Theme.border
                    border.width: modelData.isToday ? 2 : 1

                    Behavior on color { ColorAnimation { duration: 90 } }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: Theme.spacingSm + 2
                        spacing: 2

                        Label {
                            text: modelData.label
                            font.pixelSize: Theme.fontCaption
                            color: modelData.isSelected ? Theme.accent : Theme.textMuted
                        }
                        Label {
                            text: modelData.dateText
                            font.pixelSize: 16
                            font.weight: Font.DemiBold
                            color: modelData.isToday ? Theme.accent : Theme.textPrimary
                        }
                        Item { Layout.fillHeight: true }
                        Badge {
                            visible: modelData.taskCount > 0
                            text: String(modelData.taskCount)
                            fg: modelData.isSelected ? Theme.accent : Theme.textSecondary
                            bg: modelData.isSelected ? Theme.surface : Theme.surfacePressed
                        }
                        Label {
                            visible: modelData.taskCount === 0
                            text: "—"
                            font.pixelSize: Theme.fontCaption
                            color: Theme.textMuted
                            opacity: 0.6
                        }
                    }

                    MouseArea {
                        id: dayMouse
                        anchors.fill: parent
                        hoverEnabled: true
                        cursorShape: Qt.PointingHandCursor
                        onClicked: calendarVm.selectDay(index)
                    }
                }
            }
        }

        // ---- Список выбранного дня ----
        SectionHeader {
            title: calendarVm.selectedDayTitle
            count: calendarVm.selectedDayTasks.length
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
                hint: "Нажмите «＋ Задача», чтобы запланировать задачу на выбранный день"
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
