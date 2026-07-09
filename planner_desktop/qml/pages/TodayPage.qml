import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"
import "../theme"

ScrollView {
    id: page
    contentWidth: availableWidth
    clip: true

    // Чип статистики в шапке страницы.
    component StatChip: Panel {
        property string value: "0"
        property string caption: ""
        property color valueColor: Theme.textPrimary

        implicitWidth: Math.max(chipColumn.implicitWidth + 36, 108)
        implicitHeight: chipColumn.implicitHeight + 22

        ColumnLayout {
            id: chipColumn
            anchors.centerIn: parent
            spacing: 0

            Label {
                text: value
                font.pixelSize: Theme.fontTitle
                font.weight: Font.DemiBold
                color: valueColor
                Layout.alignment: Qt.AlignHCenter
            }
            Label {
                text: caption
                font.pixelSize: Theme.fontCaption
                color: Theme.textMuted
                Layout.alignment: Qt.AlignHCenter
            }
        }
    }

    ColumnLayout {
        width: Math.min(page.availableWidth - 48, 900)
        x: 24
        spacing: Theme.spacingLg

        Item { implicitHeight: 4 }

        // ---- Шапка: заголовок, дата, «+ Новая задача» ----
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingMd

            ColumnLayout {
                spacing: 2

                Label {
                    text: "Сегодня"
                    font.pixelSize: Theme.fontDisplay
                    font.weight: Font.DemiBold
                    color: Theme.textPrimary
                }
                Label {
                    text: todayVm.headerDateText
                    font.pixelSize: Theme.fontBody
                    color: Theme.textMuted
                }
            }
            Item { Layout.fillWidth: true }
            AppButton {
                text: "＋ Новая задача"
                variant: "primary"
                onClicked: editorDialog.openForCreate("")
            }
        }

        // ---- Статистика ----
        RowLayout {
            spacing: Theme.spacingMd
            Layout.fillWidth: true

            StatChip { value: todayVm.todayCount; caption: "на сегодня" }
            StatChip {
                value: todayVm.completedTodayCount
                caption: "выполнено"
                valueColor: Theme.success
            }
            StatChip { value: todayVm.undatedCount; caption: "без даты" }
            StatChip {
                visible: todayVm.hasSyncQueue
                value: todayVm.pendingSyncCount
                caption: "ждёт синка"
                valueColor: todayVm.pendingSyncCount > 0
                            ? Theme.warningText : Theme.textPrimary
            }
            Item { Layout.fillWidth: true }
        }

        // ---- Быстрое добавление ----
        SectionHeader { title: "Быстрое добавление" }
        QuickAdd { Layout.fillWidth: true }

        // ---- Задачи на сегодня ----
        SectionHeader {
            title: "Задачи на сегодня"
            count: todayVm.todayTasks.length
        }
        ColumnLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true

            Repeater {
                model: todayVm.todayTasks
                delegate: TaskCard {
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
                    onToggled: uid => todayVm.toggleCompleted(uid)
                    onEditRequested: uid => editorDialog.openForEdit(uid)
                    onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
                }
            }
            EmptyState {
                visible: todayVm.todayTasks.length === 0
                glyph: "☀️"
                text: "На сегодня задач нет"
                hint: "Добавьте задачу выше или кнопкой «＋ Новая задача»"
                Layout.fillWidth: true
                Layout.topMargin: Theme.spacingSm
            }
        }

        // ---- Без даты ----
        SectionHeader {
            title: "Без даты"
            count: todayVm.undatedTasks.length
        }
        ColumnLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true

            Repeater {
                model: todayVm.undatedTasks
                delegate: TaskCard {
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
                    onToggled: uid => todayVm.toggleCompleted(uid)
                    onEditRequested: uid => editorDialog.openForEdit(uid)
                    onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
                }
            }
            EmptyState {
                visible: todayVm.undatedTasks.length === 0
                glyph: "📥"
                text: "Задач без даты нет"
                Layout.fillWidth: true
                Layout.topMargin: Theme.spacingSm
            }
        }

        // ---- Ежедневные задачи (заглушка до полноценного порта) ----
        SectionHeader { title: "Ежедневные задачи" }
        Flow {
            spacing: Theme.spacingSm
            Layout.fillWidth: true

            Repeater {
                model: todayVm.dailyTasks
                delegate: Rectangle {
                    required property var modelData
                    radius: 16
                    implicitHeight: 32
                    implicitWidth: dailyRow.implicitWidth + 24
                    color: modelData.done ? Theme.successSoft : Theme.surface
                    border.color: modelData.done ? Theme.successSoftBorder : Theme.border
                    border.width: 1

                    Row {
                        id: dailyRow
                        anchors.centerIn: parent
                        spacing: 6
                        Label {
                            text: modelData.done ? "✓" : "○"
                            color: modelData.done ? Theme.success : Theme.textMuted
                            font.pixelSize: 13
                        }
                        Label {
                            text: modelData.title
                            font.pixelSize: 13
                            color: modelData.done ? Theme.success : Theme.textSecondary
                        }
                    }
                    MouseArea {
                        anchors.fill: parent
                        cursorShape: Qt.PointingHandCursor
                        onClicked: todayVm.toggleDaily(modelData.title)
                    }
                }
            }
        }
        Label {
            text: "Заглушка: полноценные ежедневные задачи (свои пункты, дни недели, "
                  + "перенос на завтра) появятся в следующей фазе."
            font.pixelSize: Theme.fontCaption
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Item { implicitHeight: Theme.spacingXl }
    }

    TaskEditorDialog {
        id: editorDialog
        objectName: "todayEditorDialog"
        vm: todayVm
    }

    ConfirmDialog {
        id: confirmDeleteDialog
        headerText: "Удалить задачу?"
        message: "Задача будет помечена удалённой; если её событие уже есть "
                 + "в календаре, удаление события встанет в очередь синка."
        onConfirmed: uid => todayVm.deleteTask(uid)
    }
}
