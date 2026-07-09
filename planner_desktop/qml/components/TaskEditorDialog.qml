import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Единый диалог создания/редактирования задачи.
// vm — TodayViewModel или CalendarViewModel: у обоих одинаковые
// слоты saveEditor/editorDataFor/clearEditorError и свойство editorError.
// Валидация — в Python; при ошибке диалог остаётся открытым.
Dialog {
    id: dialog

    property var vm
    property bool isEdit: false
    property string taskUid: ""
    property bool recurringInstance: false

    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    focus: true
    width: Math.min(560, (parent ? parent.width : 560) - 64)
    padding: Theme.spacingXl
    closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside

    background: Rectangle {
        radius: Theme.radiusLarge
        color: Theme.surface
        border.color: Theme.border
        border.width: 1
    }

    function todayText() { return Qt.formatDate(new Date(), "yyyy-MM-dd") }
    function tomorrowText() {
        var d = new Date()
        d.setDate(d.getDate() + 1)
        return Qt.formatDate(d, "yyyy-MM-dd")
    }

    function openForCreate(prefillDateText) {
        isEdit = false
        taskUid = ""
        recurringInstance = false
        titleField.text = ""
        notesArea.text = ""
        priorityBox.currentIndex = 0
        scheduledCheck.checked = !!prefillDateText && prefillDateText.length > 0
        allDayCheck.checked = false
        dateField.text = prefillDateText || ""
        timeField.text = ""
        durationField.text = ""
        completedCheck.checked = false
        vm.clearEditorError()
        open()
        titleField.forceActiveFocus()
    }

    function openForEdit(uid) {
        var data = vm.editorDataFor(uid)
        if (!data || !data.exists)
            return
        isEdit = true
        taskUid = uid
        recurringInstance = data.isRecurringInstance
        titleField.text = data.title
        notesArea.text = data.notes
        priorityBox.currentIndex = data.priority
        scheduledCheck.checked = data.scheduled
        allDayCheck.checked = data.isAllDay
        dateField.text = data.dateText
        timeField.text = data.timeText
        durationField.text = data.durationText
        completedCheck.checked = data.completed
        vm.clearEditorError()
        open()
        titleField.forceActiveFocus()
    }

    function submit() {
        var ok = vm.saveEditor(
            taskUid,
            titleField.text,
            notesArea.text,
            priorityBox.currentIndex,
            scheduledCheck.checked,
            allDayCheck.checked,
            dateField.text,
            timeField.text,
            durationField.text,
            completedCheck.checked
        )
        if (ok)
            dialog.close()
    }

    contentItem: ColumnLayout {
        spacing: Theme.spacingMd

        Label {
            text: dialog.isEdit ? "Редактировать задачу" : "Новая задача"
            font.pixelSize: Theme.fontTitle
            font.weight: Font.DemiBold
            color: Theme.textPrimary
        }

        TextField {
            id: titleField
            placeholderText: "Название задачи"
            font.pixelSize: Theme.fontBody
            Layout.fillWidth: true
            onAccepted: dialog.submit()
        }

        ScrollView {
            Layout.fillWidth: true
            Layout.preferredHeight: 76
            clip: true

            TextArea {
                id: notesArea
                placeholderText: "Заметки (необязательно)"
                wrapMode: TextArea.Wrap
                font.pixelSize: Theme.fontBody
            }
        }

        RowLayout {
            spacing: Theme.spacingMd
            Layout.fillWidth: true

            Label {
                text: "Приоритет:"
                font.pixelSize: Theme.fontBody
                color: Theme.textSecondary
            }
            ComboBox {
                id: priorityBox
                model: Theme.priorityNames
                Layout.preferredWidth: 190
                font.pixelSize: Theme.fontBody
            }
            Item { Layout.fillWidth: true }
            CheckBox {
                id: completedCheck
                text: "Выполнено"
                visible: dialog.isEdit
                font.pixelSize: Theme.fontBody
            }
        }

        Rectangle {
            Layout.fillWidth: true
            height: 1
            color: Theme.border
        }

        RowLayout {
            spacing: Theme.spacingMd
            Layout.fillWidth: true

            CheckBox {
                id: scheduledCheck
                text: "Запланировать (попадёт в календарь)"
                font.pixelSize: Theme.fontBody
            }
            CheckBox {
                id: allDayCheck
                text: "Весь день"
                visible: scheduledCheck.checked
                font.pixelSize: Theme.fontBody
            }
            Item { Layout.fillWidth: true }
        }

        GridLayout {
            visible: scheduledCheck.checked
            columns: 3
            columnSpacing: Theme.spacingSm
            rowSpacing: Theme.spacingSm
            Layout.fillWidth: true

            TextField {
                id: dateField
                placeholderText: "Дата: ГГГГ-ММ-ДД"
                font.pixelSize: Theme.fontBody
                Layout.preferredWidth: 170
            }
            AppButton {
                text: "Сегодня"
                variant: "ghost"
                onClicked: dateField.text = dialog.todayText()
            }
            AppButton {
                text: "Завтра"
                variant: "ghost"
                onClicked: dateField.text = dialog.tomorrowText()
            }

            TextField {
                id: timeField
                placeholderText: "Время: ЧЧ:ММ"
                enabled: !allDayCheck.checked
                font.pixelSize: Theme.fontBody
                Layout.preferredWidth: 170
            }
            TextField {
                id: durationField
                placeholderText: "Длительность, мин"
                enabled: !allDayCheck.checked
                font.pixelSize: Theme.fontBody
                Layout.preferredWidth: 170
                Layout.columnSpan: 2
            }
        }

        Label {
            visible: dialog.recurringInstance
            text: "⚠ Это экземпляр повторяющегося события Google Calendar: "
                  + "снять дату (отвязать от календаря) у него нельзя."
            font.pixelSize: Theme.fontCaption
            color: Theme.warningText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Label {
            text: dialog.vm ? dialog.vm.editorError : ""
            visible: text.length > 0
            font.pixelSize: Theme.fontCaption
            color: Theme.danger
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        RowLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true
            Layout.topMargin: Theme.spacingXs

            Item { Layout.fillWidth: true }
            AppButton {
                text: "Отмена"
                variant: "ghost"
                onClicked: dialog.close()
            }
            AppButton {
                text: dialog.isEdit ? "Сохранить" : "Создать"
                variant: "primary"
                onClicked: dialog.submit()
            }
        }
    }
}
