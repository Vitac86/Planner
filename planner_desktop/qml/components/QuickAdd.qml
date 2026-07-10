import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Быстрое добавление задачи. Компактная строка ввода по умолчанию;
// расширенные поля (заметка, календарь, дата/время) раскрываются по
// кнопке «Детали». Вся валидация — в Python (todayVm.addTask):
// невалидный ввод даёт видимую ошибку и никогда не «вешает» интерфейс.
Panel {
    id: quickAdd

    property bool expanded: false

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
        spacing: Theme.spacingMd

        // ---- компактная строка ----
        RowLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true

            Rectangle {
                Layout.alignment: Qt.AlignVCenter
                implicitWidth: 34
                implicitHeight: 34
                radius: Theme.radiusSmall
                color: Theme.accentSoft
                AppIcon {
                    anchors.centerIn: parent
                    name: "plus"
                    color: Theme.accent
                    size: 20
                }
            }

            AppTextField {
                id: titleField
                placeholderText: "Новая задача…"
                Layout.fillWidth: true
                onAccepted: quickAdd.submit()
            }

            AppButton {
                text: quickAdd.expanded ? "Свернуть" : "Детали"
                variant: "ghost"
                iconName: quickAdd.expanded ? "chevron-down" : "note"
                onClicked: quickAdd.expanded = !quickAdd.expanded
            }
            AppButton {
                text: "Добавить"
                variant: "primary"
                iconName: "plus"
                onClicked: quickAdd.submit()
            }
        }

        // ---- расширенные поля ----
        ColumnLayout {
            id: details
            visible: quickAdd.expanded
            spacing: Theme.spacingMd
            Layout.fillWidth: true
            opacity: visible ? 1 : 0
            Behavior on opacity { NumberAnimation { duration: 120 } }

            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

            AppTextField {
                id: notesField
                placeholderText: "Заметка (необязательно)"
                Layout.fillWidth: true
            }

            RowLayout {
                spacing: Theme.spacingMd
                Layout.fillWidth: true

                CheckBox {
                    id: calendarCheck
                    text: "Добавить в календарь"
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                }
                CheckBox {
                    id: allDayCheck
                    text: "Весь день"
                    visible: calendarCheck.checked
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                }
                Item { Layout.fillWidth: true }
            }

            RowLayout {
                spacing: Theme.spacingSm
                visible: calendarCheck.checked
                Layout.fillWidth: true

                AppTextField {
                    id: dateField
                    placeholderText: "ГГГГ-ММ-ДД"
                    Layout.preferredWidth: 150
                }
                AppButton {
                    text: "Сегодня"
                    variant: "secondary"
                    onClicked: dateField.text = quickAdd.todayText()
                }
                AppButton {
                    text: "Завтра"
                    variant: "secondary"
                    onClicked: dateField.text = quickAdd.tomorrowText()
                }
                AppTextField {
                    id: timeField
                    placeholderText: "ЧЧ:ММ"
                    enabled: !allDayCheck.checked
                    Layout.preferredWidth: 120
                }
                AppTextField {
                    id: durationField
                    placeholderText: "Длит., мин"
                    enabled: !allDayCheck.checked
                    Layout.preferredWidth: 110
                }
                Item { Layout.fillWidth: true }
            }
        }

        Label {
            text: todayVm.errorMessage
            visible: todayVm.errorMessage.length > 0
            color: Theme.danger
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
    }
}
