import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Инспектор выбранной задачи для правой сводки «Сегодня»/«Календаря».
// Показывает подробности (название, заметки, дата/время, приоритет,
// выполнение, статус синка) и быстрые действия. Работает по строке-словарю
// задачи (task): {uid,title,notes,timeLabel,isAllDay,priority,completed,
// hasPendingSync,isLinked}.
Panel {
    id: inspector

    property var task: null
    // Пункты снуза с флагом enabled (vm.snoozeActionsFor) и занятость VM:
    // кнопки выключаются на время операции, дубль-клики не проходят.
    property var snoozeActions: []
    // Шесть быстрых пресетов планирования из vm.taskPresetsFor(uid).
    property var taskPresets: []
    property bool busy: false

    signal editRequested(string uid)
    signal toggleRequested(string uid)
    signal deleteRequested(string uid)
    signal postponeRequested(string uid, string action)
    signal presetRequested(string uid, string presetId)
    signal pickRequested(string uid)
    signal closeRequested()

    readonly property string _uid: task ? task.uid : ""
    readonly property bool _completed: task ? task.completed : false

    implicitHeight: col.implicitHeight + 2 * Theme.spacingLg

    function _triggerPreset(action) {
        if (!busy && action.enabled)
            presetRequested(_uid, action.id)
    }
    function _triggerSnooze(action) {
        if (busy || !action.enabled)
            return
        if (action.id === "pick")
            pickRequested(_uid)
        else
            postponeRequested(_uid, action.id)
    }

    function _whenText() {
        if (!task) return ""
        if (task.isAllDay) return "Весь день"
        if (task.timeLabel && task.timeLabel.length > 0) return task.timeLabel
        return "Без даты"
    }
    function _syncText() {
        if (!task) return ""
        if (task.hasPendingSync) return "Ждёт синхронизации с Google"
        if (task.isLinked) return "Связано с Google Calendar"
        return "Локальная задача"
    }

    ScrollView {
        id: inspectorScroll
        anchors.fill: parent
        leftPadding: Theme.spacingLg
        rightPadding: Theme.spacingLg
        topPadding: Theme.spacingLg
        bottomPadding: Theme.spacingLg
        contentWidth: availableWidth
        clip: true
        ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
        ScrollBar.vertical.policy: ScrollBar.AsNeeded

        ColumnLayout {
            id: col
            width: inspectorScroll.availableWidth
            spacing: Theme.spacingMd

        // ---- шапка ----
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm

            Rectangle {
                implicitWidth: 30
                implicitHeight: 30
                radius: Theme.radiusSmall
                color: Theme.accentSoft
                AppIcon { anchors.centerIn: parent; name: "note"; size: 17; color: Theme.accent }
            }
            Label {
                text: "Детали задачи"
                font.pixelSize: Theme.fontSubtitle
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textPrimary
                Layout.alignment: Qt.AlignVCenter
            }
            Item { Layout.fillWidth: true }
            IconButton {
                iconName: "close"
                tip: "Закрыть детали"
                onClicked: inspector.closeRequested()
            }
        }

        // ---- название ----
        Label {
            text: inspector.task ? inspector.task.title : ""
            font.pixelSize: Theme.fontTitle - 2
            font.family: Theme.fontFamily
            font.weight: Font.DemiBold
            font.strikeout: inspector._completed
            color: inspector._completed ? Theme.textMuted : Theme.textPrimary
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        // ---- бейджи: время + приоритет ----
        Flow {
            Layout.fillWidth: true
            spacing: Theme.spacingSm

            Badge {
                text: inspector._whenText()
                fg: (inspector.task && inspector.task.isAllDay) ? Theme.success : Theme.accent
                bg: (inspector.task && inspector.task.isAllDay) ? Theme.successSoft : Theme.accentSoft
            }
            Badge {
                visible: !!(inspector.task && inspector.task.priority > 0)
                text: inspector.task ? Theme.priorityName(inspector.task.priority) : ""
                fg: inspector.task ? Theme.priorityColor(inspector.task.priority) : Theme.textSecondary
                bg: inspector.task ? Theme.priorityBgColor(inspector.task.priority) : Theme.surfaceHover
            }
            Badge {
                text: inspector._completed ? "Выполнено" : "Не выполнено"
                fg: inspector._completed ? Theme.success : Theme.textSecondary
                bg: inspector._completed ? Theme.successSoft : Theme.surfacePressed
            }
        }

        // ---- заметки ----
        Label {
            text: (inspector.task && inspector.task.notes.length > 0)
                  ? inspector.task.notes : "Без заметок"
            font.pixelSize: Theme.fontBody
            font.family: Theme.fontFamily
            color: (inspector.task && inspector.task.notes.length > 0)
                   ? Theme.textSecondary : Theme.textMuted
            font.italic: !(inspector.task && inspector.task.notes.length > 0)
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        // ---- статус синка ----
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm
            AppIcon {
                name: (inspector.task && inspector.task.isLinked) ? "link" : "circle"
                size: 14
                color: (inspector.task && inspector.task.hasPendingSync)
                       ? Theme.warningText : Theme.textMuted
            }
            Label {
                text: inspector._syncText()
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: (inspector.task && inspector.task.hasPendingSync)
                       ? Theme.warningText : Theme.textMuted
                Layout.fillWidth: true
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

        // ---- быстрые пресеты планирования (отдельно от snooze) ----
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm
            visible: inspector.taskPresets.length > 0

            AppIcon { name: "calendar"; size: 15; color: Theme.textSecondary }
            Label {
                text: "Быстро запланировать"
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textSecondary
            }
        }
        Flow {
            Layout.fillWidth: true
            spacing: Theme.spacingXs + 2
            visible: inspector.taskPresets.length > 0

            Repeater {
                model: inspector.taskPresets
                delegate: Rectangle {
                    id: presetChip
                    required property var modelData

                    readonly property bool chipEnabled:
                        modelData.enabled && !inspector.busy
                    implicitHeight: 30
                    implicitWidth: presetLabel.implicitWidth + 22
                    radius: Theme.radiusPill
                    color: presetHover.hovered && chipEnabled
                           ? Theme.accentSoft : Theme.surface
                    border.color: presetHover.hovered && chipEnabled
                                  ? Theme.accentSoftBorder : Theme.border
                    border.width: 1
                    opacity: chipEnabled ? 1.0 : 0.45
                    activeFocusOnTab: chipEnabled

                    Label {
                        id: presetLabel
                        anchors.centerIn: parent
                        text: presetChip.modelData.label
                        font.pixelSize: Theme.fontCaption + 1
                        font.family: Theme.fontFamily
                        font.weight: Font.Medium
                        color: presetHover.hovered && presetChip.chipEnabled
                               ? Theme.accent : Theme.textSecondary
                    }
                    HoverHandler {
                        id: presetHover
                        cursorShape: presetChip.chipEnabled
                                     ? Qt.PointingHandCursor : Qt.ArrowCursor
                    }
                    TapHandler {
                        enabled: presetChip.chipEnabled
                        onTapped: {
                            presetChip.forceActiveFocus()
                            inspector._triggerPreset(presetChip.modelData)
                        }
                    }
                    Keys.onReturnPressed:
                        inspector._triggerPreset(presetChip.modelData)
                    Keys.onSpacePressed:
                        inspector._triggerPreset(presetChip.modelData)
                    Accessible.role: Accessible.Button
                    Accessible.name: presetChip.modelData.label
                    Accessible.focusable: presetChip.chipEnabled

                    Rectangle {
                        anchors.fill: parent
                        anchors.margins: -2
                        radius: parent.radius
                        color: "transparent"
                        border.color: Theme.focusRing
                        border.width: 2
                        visible: presetChip.activeFocus
                    }
                }
            }
        }

        Rectangle {
            visible: inspector.taskPresets.length > 0
            Layout.fillWidth: true
            height: 1
            color: Theme.border
        }

        // ---- перенести (снуз) ----
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm
            visible: snoozeFlow.visibleChildrenCount > 0

            AppIcon { name: "snooze"; size: 15; color: Theme.textSecondary }
            Label {
                text: "Перенести"
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textSecondary
            }
        }
        Flow {
            id: snoozeFlow
            Layout.fillWidth: true
            spacing: Theme.spacingXs + 2

            readonly property int visibleChildrenCount: inspector.snoozeActions.length

            Repeater {
                model: inspector.snoozeActions
                delegate: Rectangle {
                    id: snoozeChip
                    required property var modelData

                    readonly property bool chipEnabled:
                        modelData.enabled && !inspector.busy

                    implicitHeight: 30
                    implicitWidth: snoozeLabel.implicitWidth + 22
                    radius: Theme.radiusPill
                    color: snoozeHover.hovered && chipEnabled
                           ? Theme.accentSoft : Theme.surface
                    border.color: snoozeHover.hovered && chipEnabled
                                  ? Theme.accentSoftBorder : Theme.border
                    border.width: 1
                    opacity: chipEnabled ? 1.0 : 0.45
                    activeFocusOnTab: chipEnabled

                    Label {
                        id: snoozeLabel
                        anchors.centerIn: parent
                        text: snoozeChip.modelData.label
                        font.pixelSize: Theme.fontCaption + 1
                        font.family: Theme.fontFamily
                        font.weight: Font.Medium
                        color: snoozeHover.hovered && snoozeChip.chipEnabled
                               ? Theme.accent : Theme.textSecondary
                    }
                    HoverHandler {
                        id: snoozeHover
                        cursorShape: snoozeChip.chipEnabled
                                     ? Qt.PointingHandCursor : Qt.ArrowCursor
                    }
                    TapHandler {
                        enabled: snoozeChip.chipEnabled
                        onTapped: {
                            snoozeChip.forceActiveFocus()
                            inspector._triggerSnooze(snoozeChip.modelData)
                        }
                    }
                    Keys.onReturnPressed:
                        inspector._triggerSnooze(snoozeChip.modelData)
                    Keys.onSpacePressed:
                        inspector._triggerSnooze(snoozeChip.modelData)
                    Accessible.role: Accessible.Button
                    Accessible.name: snoozeChip.modelData.label
                    Accessible.focusable: snoozeChip.chipEnabled

                    Rectangle {
                        anchors.fill: parent
                        anchors.margins: -2
                        radius: parent.radius
                        color: "transparent"
                        border.color: Theme.focusRing
                        border.width: 2
                        visible: snoozeChip.activeFocus
                    }
                }
            }
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

        // ---- действия ----
        AppButton {
            Layout.fillWidth: true
            text: inspector._completed ? "Снять отметку выполнения" : "Отметить выполненной"
            variant: inspector._completed ? "secondary" : "primary"
            iconName: "check"
            enabled: !inspector.busy
            onClicked: inspector.toggleRequested(inspector._uid)
        }
        RowLayout {
            Layout.fillWidth: true
            spacing: Theme.spacingSm
            AppButton {
                Layout.fillWidth: true
                text: "Изменить"
                variant: "secondary"
                iconName: "edit"
                enabled: !inspector.busy
                onClicked: inspector.editRequested(inspector._uid)
            }
            AppButton {
                text: "Удалить"
                variant: "ghost"
                iconName: "trash"
                enabled: !inspector.busy
                onClicked: inspector.deleteRequested(inspector._uid)
            }
        }
        }
    }
}
