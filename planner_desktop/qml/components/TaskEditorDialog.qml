import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as B
import QtQuick.Layouts
import QtQuick.Effects

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

    enter: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 0.0; to: 1.0; duration: 150 }
            NumberAnimation { property: "scale"; from: 0.96; to: 1.0; duration: 180; easing.type: Easing.OutCubic }
        }
    }
    exit: Transition {
        ParallelAnimation {
            NumberAnimation { property: "opacity"; from: 1.0; to: 0.0; duration: 120 }
            NumberAnimation { property: "scale"; from: 1.0; to: 0.98; duration: 120; easing.type: Easing.InCubic }
        }
    }

    Overlay.modal: Rectangle {
        color: Qt.rgba(0.09, 0.10, 0.16, 0.42)
        Behavior on opacity { NumberAnimation { duration: 140 } }
    }

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

        RowLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true

            Rectangle {
                implicitWidth: 34
                implicitHeight: 34
                radius: Theme.radiusSmall
                color: Theme.accentSoft
                AppIcon {
                    anchors.centerIn: parent
                    name: dialog.isEdit ? "edit" : "plus"
                    color: Theme.accent
                    size: 19
                }
            }
            Label {
                text: dialog.isEdit ? "Редактировать задачу" : "Новая задача"
                font.pixelSize: Theme.fontTitle
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textPrimary
                Layout.alignment: Qt.AlignVCenter
            }
            Item { Layout.fillWidth: true }
            IconButton {
                iconName: "close"
                tip: "Закрыть"
                onClicked: dialog.close()
            }
        }

        AppTextField {
            id: titleField
            placeholderText: "Название задачи"
            Layout.fillWidth: true
            onAccepted: dialog.submit()
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 82
            radius: Theme.radiusSmall
            color: Theme.surface
            border.color: notesArea.activeFocus ? Theme.accent : Theme.border
            border.width: notesArea.activeFocus ? 1.6 : 1
            Behavior on border.color { ColorAnimation { duration: 100 } }

            ScrollView {
                anchors.fill: parent
                anchors.margins: 3
                clip: true

                B.TextArea {
                    id: notesArea
                    placeholderText: "Заметки (необязательно)"
                    placeholderTextColor: Theme.textMuted
                    wrapMode: TextArea.Wrap
                    selectByMouse: true
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                    color: Theme.textPrimary
                    background: null
                }
            }
        }

        RowLayout {
            spacing: Theme.spacingMd
            Layout.fillWidth: true

            Label {
                text: "Приоритет"
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
                color: Theme.textSecondary
            }
            ComboBox {
                id: priorityBox
                model: Theme.priorityNames
                Layout.preferredWidth: 190
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
            }
            Item { Layout.fillWidth: true }
            CheckBox {
                id: completedCheck
                text: "Выполнено"
                visible: dialog.isEdit
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
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
                font.family: Theme.fontFamily
            }
            CheckBox {
                id: allDayCheck
                text: "Весь день"
                visible: scheduledCheck.checked
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
            }
            Item { Layout.fillWidth: true }
        }

        GridLayout {
            visible: scheduledCheck.checked
            columns: 3
            columnSpacing: Theme.spacingSm
            rowSpacing: Theme.spacingSm
            Layout.fillWidth: true

            AppTextField {
                id: dateField
                placeholderText: "Дата: ГГГГ-ММ-ДД"
                Layout.preferredWidth: 170
            }
            AppButton {
                text: "Сегодня"
                variant: "secondary"
                onClicked: dateField.text = dialog.todayText()
            }
            AppButton {
                text: "Завтра"
                variant: "secondary"
                onClicked: dateField.text = dialog.tomorrowText()
            }

            AppTextField {
                id: timeField
                placeholderText: "Время: ЧЧ:ММ"
                enabled: !allDayCheck.checked
                Layout.preferredWidth: 170
            }
            AppTextField {
                id: durationField
                placeholderText: "Длительность, мин"
                enabled: !allDayCheck.checked
                Layout.preferredWidth: 170
                Layout.columnSpan: 2
            }
        }

        Label {
            visible: dialog.recurringInstance
            text: "⚠ Это экземпляр повторяющегося события Google Calendar: "
                  + "снять дату (отвязать от календаря) у него нельзя."
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.warningText
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        Label {
            text: dialog.vm ? dialog.vm.editorError : ""
            visible: text.length > 0
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
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
                iconName: dialog.isEdit ? "check" : "plus"
                onClicked: dialog.submit()
            }
        }
    }
}
