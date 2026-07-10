import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"
import "../theme"

ScrollView {
    id: page
    contentWidth: availableWidth
    clip: true

    // Двухколоночная раскладка на широком окне: список задач слева,
    // «сводка дня» справа. На узком окне правый столбец скрыт, а контент
    // центрируется — так справа не остаётся пустоты.
    property bool wide: availableWidth >= 980
    property real bodyWidth: wide ? Math.min(availableWidth - 48, 1140)
                                  : Math.min(availableWidth - 48, 780)

    // Ближайшая невыполненная задача на сегодня (для карточки «Дальше»).
    property var nextTask: {
        var list = todayVm.todayTasks
        for (var i = 0; i < list.length; i++)
            if (!list[i].completed) return list[i]
        return null
    }

    // Ежедневные чек-пункты (заглушка) — переиспользуется в основной
    // колонке (узкое окно) и в правой сводке (широкое окно).
    component DailySection: ColumnLayout {
        spacing: Theme.spacingSm
        SectionHeader { title: "Ежедневные"; Layout.fillWidth: true }
        Flow {
            spacing: Theme.spacingSm
            Layout.fillWidth: true
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
                            text: modelData.title
                            font.pixelSize: 13
                            font.family: Theme.fontFamily
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
    }

    Item {
        id: contentRoot
        implicitWidth: page.availableWidth
        implicitHeight: body.implicitHeight + 48

        RowLayout {
            id: body
            anchors.top: parent.top
            anchors.topMargin: 24
            x: Math.max(24, (contentRoot.width - width) / 2)
            width: page.bodyWidth
            spacing: Theme.spacingXl

            // ================= ОСНОВНАЯ КОЛОНКА =================
            ColumnLayout {
                id: mainCol
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                spacing: Theme.spacingLg

                PageHeader {
                    title: "Сегодня"
                    subtitle: todayVm.headerDateText
                    Layout.fillWidth: true

                    AppButton {
                        text: "Новая задача"
                        variant: "primary"
                        iconName: "plus"
                        onClicked: editorDialog.openForCreate("")
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
                QuickAdd { Layout.fillWidth: true }

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
                        hint: "Добавьте задачу выше или кнопкой «Новая задача»"
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.spacingSm
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

                // ---- Ежедневные (в основной колонке только на узком окне) ----
                DailySection {
                    visible: !page.wide
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingSm
                }

                Item { implicitHeight: Theme.spacingSm }
            }

            // ================= ПРАВАЯ СВОДКА =================
            ColumnLayout {
                id: rail
                visible: page.wide
                Layout.preferredWidth: 320
                Layout.maximumWidth: 320
                Layout.alignment: Qt.AlignTop
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
                                text: "Всё на сегодня выполнено"
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

                Item { Layout.fillHeight: true }
            }
        }
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
