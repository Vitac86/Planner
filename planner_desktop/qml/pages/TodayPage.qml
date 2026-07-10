import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"
import "../theme"

ScrollView {
    id: page
    contentWidth: availableWidth
    clip: true

    // Двухколоночная раскладка на широком окне: список задач слева, правая
    // сводка/инспектор справа. На узком окне правый столбец скрыт, контент
    // центрируется.
    property bool wide: availableWidth >= 980
    property real bodyWidth: wide ? Math.min(availableWidth - 48, 1140)
                                  : Math.min(availableWidth - 48, 780)

    // ---- выбор задачи (для инспектора в правой сводке) ----
    property string selectedUid: ""
    property var selectedTask: {
        if (selectedUid === "")
            return null
        var lists = [todayVm.todayTasks, todayVm.undatedTasks]
        for (var l = 0; l < lists.length; l++)
            for (var i = 0; i < lists[l].length; i++)
                if (lists[l][i].uid === selectedUid)
                    return lists[l][i]
        return null  // выбранная задача исчезла (удалена/выполнена вне списка)
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
    function deleteSelected() {
        if (page.selectedUid !== "")
            confirmDeleteDialog.openFor(page.selectedUid)
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
                QuickAdd { id: quickAddItem; objectName: "quickAdd"; Layout.fillWidth: true }

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
                            selected: page.selectedUid === modelData.uid
                            onSelectRequested: uid => page.selectedUid = uid
                            onToggled: uid => todayVm.toggleCompleted(uid)
                            onEditRequested: uid => editorDialog.openForEdit(uid)
                            onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
                        }
                    }
                    EmptyState {
                        visible: todayVm.todayTasks.length === 0
                        glyph: "☀️"
                        text: "На сегодня задач нет"
                        hint: "Начните день с одной задачи — впишите её выше или создайте кнопкой ниже"
                        actionText: "Создать первую задачу"
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.spacingSm
                        onActionClicked: editorDialog.openForCreate(quickAddItem.todayText())
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
                            selected: page.selectedUid === modelData.uid
                            onSelectRequested: uid => page.selectedUid = uid
                            onToggled: uid => todayVm.toggleCompleted(uid)
                            onEditRequested: uid => editorDialog.openForEdit(uid)
                            onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
                        }
                    }
                    EmptyState {
                        visible: todayVm.undatedTasks.length === 0
                        glyph: "📥"
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

            // ================= ПРАВАЯ СВОДКА / ИНСПЕКТОР =================
            ColumnLayout {
                id: rail
                visible: page.wide
                Layout.preferredWidth: 320
                Layout.maximumWidth: 320
                Layout.alignment: Qt.AlignTop
                spacing: Theme.spacingLg

                // ---- инспектор выбранной задачи ----
                TaskInspector {
                    visible: page.selectedTask !== null
                    task: page.selectedTask
                    Layout.fillWidth: true
                    onEditRequested: uid => editorDialog.openForEdit(uid)
                    onToggleRequested: uid => todayVm.toggleCompleted(uid)
                    onDeleteRequested: uid => confirmDeleteDialog.openFor(uid)
                    onCloseRequested: page.selectedUid = ""
                }

                // ---- сводка дня (когда ничего не выбрано) ----
                ColumnLayout {
                    id: summary
                    visible: page.selectedTask === null
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

    TaskEditorDialog {
        id: editorDialog
        objectName: "todayEditorDialog"
        vm: todayVm
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
        onConfirmed: uid => {
            todayVm.deleteTask(uid)
            if (page.selectedUid === uid)
                page.selectedUid = ""
        }
    }
}
