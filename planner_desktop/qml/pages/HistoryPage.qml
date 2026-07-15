import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../components"
import "../theme"

// История: локальный журнал выполненного (разовые задачи + отметки
// ежедневных), сгруппированный по датам. Фильтр диапазона (7 / 30 / всё),
// пустые состояния, безопасные действия (вернуть разовую в работу, открыть
// подробности в общем редакторе). Только локально, без сети.
Item {
    id: page

    readonly property string layoutMode: uiVm.layoutModeFor(width)
    readonly property bool compact: layoutMode === "compact"
    property var focusReturnItem: null

    readonly property bool dialogsOpen: editorDialog.visible
                                        || confirmDeleteDialog.visible
                                        || confirmBulkDeleteDialog.visible

    function openSelected() {
        if (historyVm.selectedUid !== "")
            editorDialog.openForEdit(historyVm.selectedUid)
    }
    function toggleSelected() {
        if (historyVm.selectedUid === "")
            return
        var uid = historyVm.selectedUid
        if (historyVm.restoreTask(uid)) {
            historyVm.clearSelection()
            Qt.callLater(function() { historyList.forceActiveFocus() })
        }
    }
    function deleteSelected() {
        if (historyVm.selectedCount > 1)
            confirmBulkDeleteDialog.openFor("bulk")
        else if (historyVm.selectedUid !== "")
            confirmDeleteDialog.openFor(historyVm.selectedUid)
    }
    function duplicateSelected() {
        if (historyVm.selectedCount === 1)
            historyVm.duplicateTask(historyVm.selectedUids[0])
    }
    function clearSelection() { historyVm.clearSelection() }
    function restoreFocus() {
        var item = focusReturnItem
        focusReturnItem = null
        if (item && item.visible && item.enabled)
            item.forceActiveFocus()
        else
            historyList.forceActiveFocus()
    }

    onVisibleChanged: if (visible) historyVm.refresh()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: page.compact ? Theme.spacingLg : Theme.spacingXl
        spacing: page.compact ? Theme.spacingMd : Theme.spacingLg

        PageHeader {
            title: "История"
            subtitle: historyVm.totalCount > 0
                      ? (historyVm.totalCount + " выполнено за выбранный период")
                      : "Журнал выполненных задач · только локально"
            Layout.fillWidth: true
            stackActions: page.compact

            SegmentedControl {
                id: rangeFilter
                current: String(historyVm.rangeDays)
                options: [
                    { label: "7 дней", value: "7" },
                    { label: "30 дней", value: "30" },
                    { label: "Всё", value: "0" }
                ]
                onSelected: value => historyVm.setRange(parseInt(value))
            }
        }

        BulkActionToolbar {
            objectName: "historyBulkToolbar"
            visible: historyVm.selectedCount > 1
            vm: historyVm
            compact: page.compact
            Layout.fillWidth: true
            onDeleteRequested: confirmBulkDeleteDialog.openFor("bulk")
        }

        // ---- Журнал ----
        Panel {
            Layout.fillWidth: true
            Layout.fillHeight: true

            ListView {
                id: historyList
                anchors.fill: parent
                anchors.margins: Theme.spacingLg
                clip: true
                spacing: Theme.spacingLg
                visible: !historyVm.isEmpty
                model: historyVm.groups
                boundsBehavior: Flickable.StopAtBounds
                focus: true
                ScrollBar.vertical: ScrollBar {}

                delegate: ColumnLayout {
                    id: group
                    required property var modelData
                    width: historyList.width
                    spacing: Theme.spacingSm

                    // заголовок-дата
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spacingSm

                        Label {
                            text: group.modelData.relLabel.length > 0
                                  ? group.modelData.relLabel : group.modelData.dateLabel
                            font.pixelSize: Theme.fontSubtitle
                            font.family: Theme.fontFamily
                            font.weight: Font.DemiBold
                            color: Theme.textPrimary
                        }
                        Label {
                            visible: group.modelData.relLabel.length > 0 && !page.compact
                            text: group.modelData.dateLabel
                            font.pixelSize: Theme.fontCaption
                            font.family: Theme.fontFamily
                            color: Theme.textMuted
                        }
                        Item { Layout.fillWidth: true }
                        Badge {
                            text: group.modelData.count + " "
                                  + Theme.plural(group.modelData.count, "задача", "задачи", "задач")
                            fg: Theme.textSecondary
                            bg: Theme.surfacePressed
                        }
                    }

                    // записи дня
                    Repeater {
                        model: group.modelData.entries
                        delegate: Rectangle {
                            id: entry
                            required property var modelData
                            readonly property bool selected:
                                !modelData.isDaily
                                && historyVm.isTaskSelected(modelData.uid)
                            readonly property bool keyboardFocusWithin:
                                entry.activeFocus || editAction.activeFocus
                                || duplicateAction.activeFocus
                                || restoreAction.activeFocus || deleteAction.activeFocus
                            Layout.fillWidth: true
                            implicitHeight: entryRow.implicitHeight + 2 * Theme.spacingMd
                            radius: Theme.radiusMedium
                            color: selected ? Theme.accentSoft
                                 : entryHover.hovered ? Theme.surfaceHover : Theme.surface
                            border.color: (selected || activeFocus)
                                          ? Theme.accent : Theme.border
                            border.width: (selected || activeFocus) ? 1.6 : 1
                            activeFocusOnTab: !modelData.isDaily

                            Accessible.role: Accessible.ListItem
                            Accessible.name: modelData.title
                            Accessible.description: modelData.isDaily
                                ? "Ежедневная запись истории, только чтение"
                                : "Выполненная задача. Space возвращает в работу"
                            Accessible.focusable: !modelData.isDaily
                            Accessible.selected: selected

                            HoverHandler { id: entryHover }
                            MouseArea {
                                anchors.fill: parent
                                enabled: !entry.modelData.isDaily
                                acceptedButtons: Qt.LeftButton
                                propagateComposedEvents: true
                                onClicked: mouse => {
                                    entry.forceActiveFocus()
                                    historyVm.selectTaskWithModifiers(
                                        entry.modelData.uid,
                                        (mouse.modifiers & Qt.ControlModifier) !== 0,
                                        (mouse.modifiers & Qt.ShiftModifier) !== 0
                                    )
                                }
                                onDoubleClicked: {
                                    page.focusReturnItem = entry
                                    historyVm.selectTask(entry.modelData.uid)
                                    editorDialog.openForEdit(entry.modelData.uid)
                                }
                            }
                            Keys.onPressed: event => {
                                if (entry.modelData.isDaily)
                                    return
                                if (event.key === Qt.Key_Delete) {
                                    page.focusReturnItem = entry
                                    page.deleteSelected()
                                    event.accepted = true
                                    return
                                }
                                if (!historyVm.isTaskSelected(entry.modelData.uid))
                                    historyVm.selectTask(entry.modelData.uid)
                                if (event.key === Qt.Key_Return
                                        || event.key === Qt.Key_Enter) {
                                    page.focusReturnItem = entry
                                    editorDialog.openForEdit(entry.modelData.uid)
                                    event.accepted = true
                                } else if (event.key === Qt.Key_Space) {
                                    page.toggleSelected()
                                    event.accepted = true
                                } else if (event.key === Qt.Key_Escape) {
                                    page.clearSelection()
                                    event.accepted = true
                                }
                            }

                            RowLayout {
                                id: entryRow
                                anchors.fill: parent
                                anchors.leftMargin: Theme.spacingLg
                                anchors.rightMargin: Theme.spacingMd
                                anchors.topMargin: Theme.spacingMd
                                anchors.bottomMargin: Theme.spacingMd
                                spacing: Theme.spacingMd

                                // маркер выполнения
                                Rectangle {
                                    Layout.alignment: Qt.AlignVCenter
                                    implicitWidth: 22
                                    implicitHeight: 22
                                    radius: entry.modelData.isDaily ? height / 2 : 7
                                    color: entry.modelData.isDaily ? Theme.accentSoft : Theme.successSoft
                                    AppIcon {
                                        anchors.centerIn: parent
                                        name: entry.modelData.isDaily ? "refresh" : "check"
                                        size: 13
                                        color: entry.modelData.isDaily ? Theme.accent : Theme.success
                                    }
                                }

                                // цветная полоса приоритета
                                Rectangle {
                                    visible: entry.modelData.priority > 0
                                    Layout.fillHeight: true
                                    Layout.topMargin: 2
                                    Layout.bottomMargin: 2
                                    implicitWidth: 3
                                    radius: 2
                                    color: Theme.priorityColor(entry.modelData.priority)
                                }

                                ColumnLayout {
                                    Layout.fillWidth: true
                                    spacing: 3

                                    Label {
                                        text: entry.modelData.title
                                        font.pixelSize: Theme.fontBody
                                        font.family: Theme.fontFamily
                                        font.weight: Font.Medium
                                        color: Theme.textPrimary
                                        elide: Text.ElideRight
                                        Layout.fillWidth: true
                                    }
                                    RowLayout {
                                        spacing: Theme.spacingSm
                                        visible: entry.modelData.notes.length > 0
                                        Label {
                                            text: entry.modelData.notes
                                            font.pixelSize: Theme.fontCaption
                                            font.family: Theme.fontFamily
                                            color: Theme.textMuted
                                            elide: Text.ElideRight
                                            maximumLineCount: 1
                                            Layout.fillWidth: true
                                        }
                                    }
                                    Flow {
                                        Layout.fillWidth: true
                                        spacing: Theme.spacingXs
                                        visible: entry.modelData.tags
                                                 && entry.modelData.tags.length > 0
                                        Repeater {
                                            model: entry.modelData.tags || []
                                            delegate: TagChip {
                                                required property var modelData
                                                name: String(modelData)
                                                compact: true
                                                onClicked: name => {
                                                    searchVm.toggleTagFilter(name)
                                                    searchVm.openSearch()
                                                }
                                            }
                                        }
                                        TagChip {
                                            visible: entry.modelData.tagOverflow > 0
                                            name: "+" + entry.modelData.tagOverflow
                                            compact: true
                                        }
                                    }
                                }

                                Badge {
                                    visible: entry.modelData.isDaily
                                    text: page.compact ? "ежедн." : "ежедневная"
                                    fg: Theme.accent
                                    bg: Theme.accentSoft
                                    Layout.alignment: Qt.AlignVCenter
                                }
                                SeriesBadge {
                                    isLocalSeries: !!entry.modelData.isSeries
                                    compact: page.compact
                                    Layout.alignment: Qt.AlignVCenter
                                }
                                Badge {
                                    visible: entry.modelData.timeLabel.length > 0
                                             && !page.compact
                                    text: entry.modelData.timeLabel
                                    fg: entry.modelData.isAllDay ? Theme.success : Theme.textSecondary
                                    bg: entry.modelData.isAllDay ? Theme.successSoft : Theme.surfacePressed
                                    Layout.alignment: Qt.AlignVCenter
                                }
                                Label {
                                    visible: entry.modelData.doneAt.length > 0
                                    text: entry.modelData.doneAt
                                    font.pixelSize: Theme.fontCaption
                                    font.family: Theme.fontFamily
                                    color: Theme.textMuted
                                    Layout.alignment: Qt.AlignVCenter
                                }

                                // действия (только для разовых задач)
                                RowLayout {
                                    spacing: 2
                                    Layout.alignment: Qt.AlignVCenter
                                    visible: !entry.modelData.isDaily
                                    opacity: (entryHover.hovered || entry.selected
                                              || entry.keyboardFocusWithin) ? 1.0 : 0.0
                                    Behavior on opacity { NumberAnimation { duration: 130 } }

                                    IconButton {
                                        id: editAction
                                        iconName: "edit"
                                        tip: "Подробнее"
                                        hoverGlyphColor: Theme.accent
                                        hoverBg: Theme.accentSoft
                                        enabled: !historyVm.busy
                                        onClicked: {
                                            page.focusReturnItem = entry
                                            historyVm.selectTask(entry.modelData.uid)
                                            editorDialog.openForEdit(entry.modelData.uid)
                                        }
                                    }
                                    IconButton {
                                        id: duplicateAction
                                        iconName: "plus"
                                        tip: "Дублировать"
                                        hoverGlyphColor: Theme.accent
                                        hoverBg: Theme.accentSoft
                                        enabled: !historyVm.busy
                                        onClicked: historyVm.duplicateTask(entry.modelData.uid)
                                    }
                                    IconButton {
                                        id: restoreAction
                                        iconName: "refresh"
                                        tip: "Вернуть в работу"
                                        hoverGlyphColor: Theme.accent
                                        hoverBg: Theme.accentSoft
                                        enabled: !historyVm.busy
                                        onClicked: {
                                            historyVm.selectTask(entry.modelData.uid)
                                            page.toggleSelected()
                                        }
                                    }
                                    IconButton {
                                        id: deleteAction
                                        iconName: "trash"
                                        tip: "Удалить"
                                        hoverGlyphColor: Theme.danger
                                        hoverBg: Theme.dangerSoft
                                        enabled: !historyVm.busy
                                        onClicked: {
                                            page.focusReturnItem = entry
                                            historyVm.selectTask(entry.modelData.uid)
                                            confirmDeleteDialog.openFor(entry.modelData.uid)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            EmptyState {
                anchors.centerIn: parent
                width: parent.width - 2 * Theme.spacingXl
                visible: historyVm.isEmpty
                iconName: "history"
                text: historyVm.rangeDays === 0
                      ? "Выполненных задач пока нет"
                      : "За этот период нет выполненных задач"
                hint: historyVm.rangeDays === 0
                      ? "Отмечайте задачи выполненными — они появятся здесь, сгруппированные по дням"
                      : "Попробуйте расширить диапазон или отметьте задачу выполненной на «Сегодня»"
            }
        }
    }

    // «Подробнее» открывает общий редактор (тот же контракт, что Today/Calendar).
    TaskEditorDialog {
        id: editorDialog
        objectName: "historyEditorDialog"
        vm: historyVm
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
        onConfirmed: uid => historyVm.deleteTask(uid)
        onClosed: page.restoreFocus()
    }
    ConfirmDialog {
        id: confirmBulkDeleteDialog
        headerText: "Удалить выбранные задачи?"
        message: "Удаление затронет только выбранные разовые задачи. Ежедневные строки истории никогда не входят в выбор."
        onConfirmed: historyVm.bulkDelete()
        onClosed: page.restoreFocus()
    }
}
