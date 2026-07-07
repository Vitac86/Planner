import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

// Быстрое добавление задачи. Вся валидация — в Python (todayVm.addTask):
// невалидный ввод даёт видимую ошибку и никогда не «вешает» интерфейс.
Rectangle {
    id: quickAdd
    radius: 12
    color: "#FFFFFF"
    border.color: "#E6E8F0"
    border.width: 1
    implicitHeight: layout.implicitHeight + 32

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
        anchors.margins: 16
        spacing: 8

        RowLayout {
            spacing: 8
            Layout.fillWidth: true

            TextField {
                id: titleField
                placeholderText: "Новая задача…"
                Layout.fillWidth: true
                onAccepted: quickAdd.submit()
            }
            Button {
                text: "Добавить"
                highlighted: true
                onClicked: quickAdd.submit()
            }
        }

        TextField {
            id: notesField
            placeholderText: "Заметка (необязательно)"
            Layout.fillWidth: true
        }

        RowLayout {
            spacing: 12
            Layout.fillWidth: true

            CheckBox {
                id: calendarCheck
                text: "Добавить в календарь"
            }
            CheckBox {
                id: allDayCheck
                text: "Весь день"
                visible: calendarCheck.checked
            }
            Item { Layout.fillWidth: true }
        }

        RowLayout {
            spacing: 8
            visible: calendarCheck.checked
            Layout.fillWidth: true

            TextField {
                id: dateField
                placeholderText: "Дата: ГГГГ-ММ-ДД"
                Layout.preferredWidth: 170
            }
            TextField {
                id: timeField
                placeholderText: "Время: ЧЧ:ММ"
                enabled: !allDayCheck.checked
                Layout.preferredWidth: 140
            }
            TextField {
                id: durationField
                placeholderText: "Длительность, мин"
                enabled: !allDayCheck.checked
                Layout.preferredWidth: 170
            }
            Item { Layout.fillWidth: true }
        }

        Label {
            text: todayVm.errorMessage
            visible: todayVm.errorMessage.length > 0
            color: "#C62828"
            font.pixelSize: 12
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
}
