import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Быстрое добавление задачи. Компактная строка по умолчанию: название +
// приоритет + «Детали». В компактном режиме название разбирается лёгким
// парсером (todayVm.addQuick): «Отчёт завтра 15:00» → дата/время сами.
// Раскрытые «Детали» дают явные поля (todayVm.addTaskDetailed).
// Enter создаёт задачу, Escape очищает/сворачивает. Вся валидация — в
// Python: невалидный ввод даёт видимую ошибку и не «вешает» интерфейс.
Panel {
    id: quickAdd

    property bool expanded: false
    property int priority: 0

    implicitHeight: layout.implicitHeight + 2 * Theme.spacingLg

    function focusInput() {
        titleField.forceActiveFocus()
        titleField.selectAll()
    }
    function todayText() { return Qt.formatDate(new Date(), "yyyy-MM-dd") }
    function tomorrowText() {
        var d = new Date()
        d.setDate(d.getDate() + 1)
        return Qt.formatDate(d, "yyyy-MM-dd")
    }

    function clearForm() {
        titleField.text = ""
        notesField.text = ""
        dateField.text = ""
        timeField.text = ""
        durationField.text = ""
        calendarCheck.checked = false
        allDayCheck.checked = false
        quickAdd.priority = 0
    }

    function submit() {
        var ok
        if (quickAdd.expanded) {
            ok = todayVm.addTaskDetailed(
                titleField.text, notesField.text, quickAdd.priority,
                calendarCheck.checked, allDayCheck.checked,
                dateField.text, timeField.text, durationField.text)
        } else {
            ok = todayVm.addQuick(titleField.text, quickAdd.priority)
        }
        if (ok) {
            clearForm()
            titleField.forceActiveFocus()
        }
    }

    // Меню выбора приоритета — держит компактную строку лёгкой.
    Menu {
        id: priorityMenu
        MenuItem { text: "Без приоритета"; onTriggered: quickAdd.priority = 0 }
        MenuItem { text: "Низкий";        onTriggered: quickAdd.priority = 1 }
        MenuItem { text: "Средний";       onTriggered: quickAdd.priority = 2 }
        MenuItem { text: "Высокий";       onTriggered: quickAdd.priority = 3 }
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
                placeholderText: "Новая задача…  напр. «Отчёт завтра 15:00»"
                Layout.fillWidth: true
                onAccepted: quickAdd.submit()
                Keys.onEscapePressed: {
                    if (text.length > 0) quickAdd.clearForm()
                    else quickAdd.expanded = false
                }
            }

            // компактный выбор приоритета
            Rectangle {
                id: prioChip
                Layout.alignment: Qt.AlignVCenter
                implicitHeight: 34
                implicitWidth: prioRow.implicitWidth + 20
                radius: Theme.radiusSmall
                color: prioMouse.containsMouse ? Theme.surfaceHover : Theme.surface
                border.color: quickAdd.priority > 0 ? Theme.priorityColor(quickAdd.priority) : Theme.border
                border.width: 1
                Behavior on border.color { ColorAnimation { duration: 100 } }

                Row {
                    id: prioRow
                    anchors.centerIn: parent
                    spacing: 6
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 10; height: 10; radius: 5
                        color: quickAdd.priority > 0 ? Theme.priorityColor(quickAdd.priority) : Theme.textMuted
                    }
                    Label {
                        anchors.verticalCenter: parent.verticalCenter
                        text: quickAdd.priority > 0 ? Theme.priorityName(quickAdd.priority) : "Приоритет"
                        font.pixelSize: Theme.fontBody
                        font.family: Theme.fontFamily
                        color: Theme.textSecondary
                    }
                    AppIcon {
                        anchors.verticalCenter: parent.verticalCenter
                        name: "chevron-down"; size: 13; color: Theme.textMuted
                    }
                }
                MouseArea {
                    id: prioMouse
                    anchors.fill: parent
                    hoverEnabled: true
                    cursorShape: Qt.PointingHandCursor
                    onClicked: priorityMenu.popup()
                }
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
