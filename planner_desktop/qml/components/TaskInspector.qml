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

    signal editRequested(string uid)
    signal toggleRequested(string uid)
    signal deleteRequested(string uid)
    signal closeRequested()

    readonly property string _uid: task ? task.uid : ""
    readonly property bool _completed: task ? task.completed : false

    implicitHeight: col.implicitHeight + 2 * Theme.spacingLg

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

    ColumnLayout {
        id: col
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: Theme.spacingLg
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
                visible: inspector.task && inspector.task.priority > 0
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

        // ---- действия ----
        AppButton {
            Layout.fillWidth: true
            text: inspector._completed ? "Снять отметку выполнения" : "Отметить выполненной"
            variant: inspector._completed ? "secondary" : "primary"
            iconName: "check"
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
                onClicked: inspector.editRequested(inspector._uid)
            }
            AppButton {
                text: "Удалить"
                variant: "ghost"
                iconName: "trash"
                onClicked: inspector.deleteRequested(inspector._uid)
            }
        }
    }
}
