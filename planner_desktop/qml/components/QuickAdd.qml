import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Быстрое добавление задачи. Вся валидация — в Python (todayVm.addTask):
// невалидный ввод даёт видимую ошибку и никогда не «вешает» интерфейс.
Panel {
    id: quickAdd

    implicitHeight: layout.implicitHeight + 2 * Theme.spacingLg

    function todayText() { return Qt.formatDate(new Date(), "yyyy-MM-dd") }
    function tomorrowText() {
        var d = new Date()
        d.setDate(d.getDate() + 1)
        return Qt.formatDate(d, "yyyy-MM-dd")
    }

    function submit() {
        var ok = todayVm.addTask(
            titleField.text,
            notesField.text,
            calendarCheck.checked,
            allDayCheck.checked,
            dateField.text,
            timeField.text,
            durationField.text
        )
        if (ok) {
            titleField.text = ""
            notesField.text = ""
            dateField.text = ""
            timeField.text = ""
            durationField.text = ""
            calendarCheck.checked = false
            allDayCheck.checked = false
            titleField.forceActiveFocus()
        }
    }

    ColumnLayout {
        id: layout
        anchors.fill: parent
        anchors.margins: Theme.spacingLg
        spacing: Theme.spacingSm

        RowLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true

            TextField {
                id: titleField
                placeholderText: "Новая задача…"
                font.pixelSize: Theme.fontBody
                Layout.fillWidth: true
                onAccepted: quickAdd.submit()
            }
            AppButton {
                text: "Добавить"
                variant: "primary"
                onClicked: quickAdd.submit()
            }
        }

        TextField {
            id: notesField
            placeholderText: "Заметка (необязательно)"
            font.pixelSize: Theme.fontBody
            Layout.fillWidth: true
        }

        RowLayout {
            spacing: Theme.spacingMd
            Layout.fillWidth: true

            CheckBox {
                id: calendarCheck
                text: "Добавить в календарь"
                font.pixelSize: Theme.fontBody
            }
            CheckBox {
                id: allDayCheck
                text: "Весь день"
                visible: calendarCheck.checked
                font.pixelSize: Theme.fontBody
            }
            Item { Layout.fillWidth: true }
        }

        RowLayout {
            spacing: Theme.spacingSm
            visible: calendarCheck.checked
            Layout.fillWidth: true

            TextField {
                id: dateField
                placeholderText: "Дата: ГГГГ-ММ-ДД"
                font.pixelSize: Theme.fontBody
                Layout.preferredWidth: 165
            }
            AppButton {
                text: "Сегодня"
                variant: "ghost"
                onClicked: dateField.text = quickAdd.todayText()
            }
            AppButton {
                text: "Завтра"
                variant: "ghost"
                onClicked: dateField.text = quickAdd.tomorrowText()
            }
            TextField {
                id: timeField
                placeholderText: "Время: ЧЧ:ММ"
                enabled: !allDayCheck.checked
                font.pixelSize: Theme.fontBody
                Layout.preferredWidth: 135
            }
            TextField {
                id: durationField
                placeholderText: "Длит., мин"
                enabled: !allDayCheck.checked
                font.pixelSize: Theme.fontBody
                Layout.preferredWidth: 110
            }
            Item { Layout.fillWidth: true }
        }

        Label {
            text: todayVm.errorMessage
            visible: todayVm.errorMessage.length > 0
            color: Theme.danger
            font.pixelSize: Theme.fontCaption
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
}
