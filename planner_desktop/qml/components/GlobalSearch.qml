import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects

import "../theme"

Popup {
    id: search
    objectName: "globalSearchOverlay"
    property var vm: null
    readonly property bool blocksWindow: visible || editorDialog.visible
                                        || deleteDialog.visible
                                        || bulkDeleteDialog.visible

    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    focus: true
    width: Math.min(920, Math.max(320, (parent ? parent.width : 920) - 24))
    height: Math.min(720, Math.max(420, (parent ? parent.height : 720) - 24))
    padding: Theme.spacingLg
    closePolicy: Popup.NoAutoClose
    visible: vm ? vm.isOpen : false

    readonly property bool compact: width < 700

    Overlay.modal: Rectangle { color: Qt.rgba(0.09, 0.10, 0.16, 0.48) }
    background: Rectangle {
        radius: Theme.radiusLarge
        color: Theme.surface
        border.color: Theme.border
        border.width: 1
        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: Theme.shadowColor
            blurMax: Theme.shadowBlurMax
            shadowBlur: Theme.elevDialogBlur
            shadowVerticalOffset: Theme.elevDialogY
            shadowOpacity: Theme.elevDialogOpacity
            autoPaddingEnabled: true
        }
    }

    function focusSearch() {
        Qt.callLater(function() {
            searchField.forceActiveFocus()
            searchField.selectAll()
        })
    }
    function escapeStep() {
        if (editorDialog.visible || deleteDialog.visible || bulkDeleteDialog.visible)
            return
        if (vm.selectedCount > 0) {
            vm.clearSelection()
        } else if (searchField.text.length > 0) {
            searchField.clear()
            vm.setQuery("")
        } else {
            vm.closeSearch()
        }
    }

    onVisibleChanged: if (visible) focusSearch()

    Connections {
        target: search.vm
        function onFocusSearchRequested() { if (search.visible) search.focusSearch() }
        function onEditRequested(uid) { editorDialog.openForEdit(uid) }
    }

    contentItem: ColumnLayout {
        spacing: Theme.spacingMd

        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm
            AppIcon { name: "search"; size: 22; color: Theme.accent }
            AppTextField {
                id: searchField
                objectName: "globalSearchField"
                Layout.fillWidth: true
                placeholderText: "Поиск по названию, заметкам и тегам"
                Accessible.name: "Глобальный поиск задач"
                text: search.vm ? search.vm.query : ""
                onTextEdited: debounce.restart()
                onAccepted: search.vm.openSelectedResult()
                Keys.onDownPressed: {
                    search.vm.moveResultSelection(1)
                    resultList.forceActiveFocus()
                }
                Keys.onUpPressed: {
                    search.vm.moveResultSelection(-1)
                    resultList.forceActiveFocus()
                }
            }
            Timer {
                id: debounce
                interval: 90
                repeat: false
                onTriggered: search.vm.setQuery(searchField.text)
            }
            IconButton {
                iconName: "close"
                tip: "Закрыть поиск (Esc)"
                Accessible.name: "Закрыть глобальный поиск"
                onClicked: search.vm.closeSearch()
            }
        }

        SearchFilterBar {
            Layout.fillWidth: true
            vm: search.vm
            compact: search.compact
        }

        Flow {
            Layout.fillWidth: true
            spacing: Theme.spacingXs
            visible: search.vm && search.vm.tagFilters.length > 0
            Repeater {
                model: search.vm ? search.vm.tagFilters : []
                delegate: TagChip {
                    required property var modelData
                    name: String(modelData)
                    selected: true
                    removable: true
                    onRemoveRequested: search.vm.toggleTagFilter(modelData)
                }
            }
        }

        RowLayout {
            Layout.fillWidth: true
            Label {
                text: search.vm.resultCount + " "
                      + Theme.plural(search.vm.resultCount, "результат", "результата", "результатов")
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textMuted
                Accessible.role: Accessible.StatusBar
                Accessible.name: text
            }
            Item { Layout.fillWidth: true }
            AppButton {
                visible: search.vm.resultCount > 0
                text: search.compact ? "Все" : "Выбрать всё"
                variant: "ghost"
                Accessible.name: "Выбрать все видимые результаты"
                onClicked: search.vm.selectAllVisible()
            }
        }

        BulkActionToolbar {
            objectName: "globalSearchBulkToolbar"
            Layout.fillWidth: true
            visible: search.vm && search.vm.selectedCount > 1
            vm: search.vm
            compact: search.compact
            onDeleteRequested: bulkDeleteDialog.openFor("bulk")
        }

        // ---- определения локальных серий (отдельная группа, Phase 3.2A) ----
        ColumnLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingXs
            visible: search.vm && search.vm.seriesResultCount > 0

            RowLayout {
                spacing: Theme.spacingXs
                AppIcon { name: "repeat"; size: 13; color: Theme.accent }
                Label {
                    text: "Локальные серии (" + search.vm.seriesResultCount + ")"
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    font.weight: Font.DemiBold
                    color: Theme.textSecondary
                }
            }
            Repeater {
                model: search.vm ? search.vm.seriesResults : []
                delegate: Rectangle {
                    id: seriesRow
                    required property var modelData
                    Layout.fillWidth: true
                    implicitHeight: 44
                    radius: Theme.radiusSmall
                    color: Theme.surface
                    border.color: Theme.border
                    border.width: 1
                    Accessible.role: Accessible.ListItem
                    Accessible.name: "Локальная серия: " + modelData.title
                        + ", " + modelData.summary

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Theme.spacingMd
                        anchors.rightMargin: Theme.spacingMd
                        spacing: Theme.spacingSm

                        AppIcon { name: "repeat"; size: 14; color: Theme.accent }
                        ColumnLayout {
                            spacing: 0
                            Layout.fillWidth: true
                            Label {
                                text: seriesRow.modelData.title
                                font.pixelSize: Theme.fontBody
                                font.family: Theme.fontFamily
                                font.weight: Font.Medium
                                color: Theme.textPrimary
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Label {
                                text: seriesRow.modelData.summary
                                font.pixelSize: Theme.fontCaption - 1
                                font.family: Theme.fontFamily
                                color: Theme.textMuted
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                        Badge {
                            text: seriesRow.modelData.active
                                  ? "Локальная серия" : "Остановлена"
                            fg: Theme.accent
                            bg: Theme.accentSoft
                        }
                    }
                }
            }
        }

        Item {
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 180

            ListView {
                id: resultList
                objectName: "globalSearchResults"
                anchors.fill: parent
                clip: true
                spacing: Theme.spacingSm
                model: search.vm ? search.vm.results : []
                visible: count > 0
                activeFocusOnTab: true
                Accessible.role: Accessible.List
                Accessible.name: "Результаты глобального поиска"
                ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

                delegate: SearchResultRow {
                    required property var modelData
                    width: resultList.width
                    task: modelData
                    selected: search.vm.isTaskSelected(modelData.uid)
                    actionsEnabled: !search.vm.busy
                    onSelectionRequested: (uid, ctrl, shift) =>
                        search.vm.selectTaskWithModifiers(uid, ctrl, shift)
                    onOpenRequested: uid => editorDialog.openForEdit(uid)
                    onToggleRequested: uid => search.vm.toggleCompleted(uid)
                    onDeleteRequested: uid => deleteDialog.openFor(uid)
                    onDuplicateRequested: uid => search.vm.duplicateTask(uid)
                    onTagClicked: name => search.vm.toggleTagFilter(name)
                }
            }

            EmptyState {
                anchors.centerIn: parent
                width: parent.width - 2 * Theme.spacingLg
                visible: search.vm && search.vm.resultCount === 0
                iconName: "search"
                text: search.vm && search.vm.emptyQueryAndFilters
                      ? "Начните вводить запрос"
                      : "Ничего не найдено"
                hint: search.vm && search.vm.emptyQueryAndFilters
                      ? "Ищите по русским и английским названиям, заметкам и тегам"
                      : "Измените запрос или сбросьте активные фильтры"
            }
        }
    }

    Shortcut {
        sequence: "Esc"
        enabled: search.visible
        onActivated: search.escapeStep()
    }
    Shortcut {
        sequence: "Ctrl+A"
        enabled: search.visible && resultList.activeFocus
        onActivated: search.vm.selectAllVisible()
    }
    Shortcut {
        sequence: "Ctrl+D"
        enabled: search.visible && !searchField.activeFocus
                 && search.vm.selectedCount === 1
        onActivated: search.vm.duplicateTask(search.vm.selectedUids[0])
    }
    Shortcut {
        sequence: "Delete"
        enabled: search.visible && resultList.activeFocus && search.vm.selectedCount > 0
        onActivated: {
            if (search.vm.selectedCount > 1) bulkDeleteDialog.openFor("bulk")
            else deleteDialog.openFor(search.vm.selectedUids[0])
        }
    }
    Shortcut {
        sequence: "Up"
        enabled: search.visible && resultList.activeFocus
        onActivated: search.vm.moveResultSelection(-1)
    }
    Shortcut {
        sequence: "Down"
        enabled: search.visible && resultList.activeFocus
        onActivated: search.vm.moveResultSelection(1)
    }
    Shortcut {
        sequence: "Return"
        enabled: search.visible && resultList.activeFocus
        onActivated: search.vm.openSelectedResult()
    }

    TaskEditorDialog {
        id: editorDialog
        objectName: "globalSearchEditorDialog"
        vm: search.vm
        onDeleteRequested: uid => deleteDialog.openFor(uid)
    }
    ConfirmDialog {
        id: deleteDialog
        headerText: "Удалить задачу?"
        message: "Задача будет помечена удалённой; Calendar-операция будет поставлена безопасно."
        onConfirmed: uid => search.vm.deleteTask(uid)
    }
    ConfirmDialog {
        id: bulkDeleteDialog
        headerText: "Удалить выбранные задачи?"
        message: "Будут удалены только выбранные видимые задачи. Для связанных событий операции попадут в очередь ручного синка."
        onConfirmed: search.vm.bulkDelete()
    }
}
