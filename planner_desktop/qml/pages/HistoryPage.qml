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

    onVisibleChanged: if (visible) historyVm.refresh()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingXl
        spacing: Theme.spacingLg

        PageHeader {
            title: "История"
            subtitle: historyVm.totalCount > 0
                      ? (historyVm.totalCount + " выполнено за выбранный период")
                      : "Журнал выполненных задач · только локально"
            Layout.fillWidth: true

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
                            visible: group.modelData.relLabel.length > 0
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
                            Layout.fillWidth: true
                            implicitHeight: entryRow.implicitHeight + 2 * Theme.spacingMd
                            radius: Theme.radiusMedium
                            color: entryHover.hovered ? Theme.surfaceHover : Theme.surface
                            border.color: Theme.border
                            border.width: 1

                            HoverHandler { id: entryHover }

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
                                }

                                Badge {
                                    visible: entry.modelData.isDaily
                                    text: "ежедневная"
                                    fg: Theme.accent
                                    bg: Theme.accentSoft
                                    Layout.alignment: Qt.AlignVCenter
                                }
                                Badge {
                                    visible: entry.modelData.timeLabel.length > 0
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
                                    opacity: entryHover.hovered ? 1.0 : 0.0
                                    Behavior on opacity { NumberAnimation { duration: 130 } }

                                    IconButton {
                                        iconName: "edit"
                                        tip: "Подробнее"
                                        hoverGlyphColor: Theme.accent
                                        hoverBg: Theme.accentSoft
                                        onClicked: editorDialog.openForEdit(entry.modelData.uid)
                                    }
                                    IconButton {
                                        iconName: "refresh"
                                        tip: "Вернуть в работу"
                                        hoverGlyphColor: Theme.accent
                                        hoverBg: Theme.accentSoft
                                        onClicked: historyVm.reopenTask(entry.modelData.uid)
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
                glyph: "🕘"
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
    }
}
