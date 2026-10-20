import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Быстрое добавление задачи. Компактная строка по умолчанию: название +
// приоритет + «Детали». В компактном режиме название разбирается лёгким
// парсером (todayVm.addQuick): «Отчёт завтра 15:00» → дата/время сами.
// Раскрытые «Детали» дают явные поля с нативными пикерами даты/времени
// (todayVm.addTaskDetailed) — сырые строки дат пользователь не видит.
// Enter создаёт задачу, Escape сворачивает детали, сохраняя черновик. Вся валидация — в
// Python: невалидный ввод даёт видимую ошибку и не «вешает» интерфейс.
Panel {
    id: quickAdd

    property bool expanded: false
    property int priority: 0
    readonly property bool hasDetails: notesField.text.length > 0 || calendarCheck.checked
    // Компактная раскладка окна: кнопки без подписей, чтобы строка не ломалась.
    property bool compact: false
    property var draftStore: typeof draftsVm !== "undefined" ? draftsVm : null
    property var preferences: typeof prefsVm !== "undefined" ? prefsVm : null
    property bool draftRestored: false
    property bool _draftReady: false
    property bool _draftHydrating: false
    property bool _draftTouched: false
    property bool _durationCustomized: false

    Connections {
        target: quickAdd.preferences
        function onSettingsChanged() {
            if (quickAdd._durationCustomized || quickAdd.draftRestored) return
            quickAdd._draftHydrating = true
            durationPicker.reset(String(quickAdd.preferences.defaultDurationMinutes))
            quickAdd._draftHydrating = false
            if (quickAdd._draftTouched) quickAdd.persistDraft()
        }
    }

    onPriorityChanged: persistDraft()
    onExpandedChanged: persistDraft()
    Component.onCompleted: restoreDraft()
    Component.onDestruction: {
        if (_draftTouched || draftRestored) persistDraft()
        if (draftStore) draftStore.flush()
    }

    implicitHeight: layout.implicitHeight + 2 * Theme.spacingLg

    function focusInput() {
        titleField.forceActiveFocus()
        titleField.selectAll()
    }

    function clearForm() {
        _draftHydrating = true
        titleField.text = ""
        notesField.text = ""
        dateField.dateText = ""
        timeField.timeText = ""
        durationPicker.reset(preferences ? String(preferences.defaultDurationMinutes) : "")
        calendarCheck.checked = false
        allDayCheck.checked = false
        quickAdd.priority = 0
        _draftHydrating = false
        _draftTouched = false
        _durationCustomized = false
        draftRestored = false
        if (draftStore) draftStore.discardDraft("quick-add", "")
    }

    function persistDraft() {
        if (!_draftReady || _draftHydrating || !draftStore) return
        if (!_draftTouched && !draftRestored && titleField.text.length === 0
                && notesField.text.length === 0 && priority === 0
                && !calendarCheck.checked) return
        _draftTouched = true
        draftStore.saveDraft("quick-add", "", {
            title: titleField.text, notes: notesField.text,
            priority: priority, expanded: expanded,
            calendarChecked: calendarCheck.checked, allDay: allDayCheck.checked,
            dateText: dateField.dateText, timeText: timeField.timeText,
            durationText: durationPicker.durationText
        })
    }

    function restoreDraft() {
        _draftHydrating = true
        durationPicker.reset(preferences ? String(preferences.defaultDurationMinutes) : "")
        var data = draftStore ? draftStore.loadDraft("quick-add", "") : ({})
        if (data && Object.keys(data).length > 0) {
            titleField.text = data.title || ""
            notesField.text = data.notes || ""
            priority = data.priority || 0
            expanded = !!data.expanded
            calendarCheck.checked = !!data.calendarChecked
            allDayCheck.checked = !!data.allDay
            dateField.dateText = data.dateText || ""
            timeField.timeText = data.timeText || ""
            durationPicker.reset(data.durationText || "")
            draftRestored = true
        }
        _draftHydrating = false
        _draftReady = true
    }

    function submit() {
        if (todayVm.busy)
            return
        var ok
        // Collapsing the controls only hides them; it must not discard notes
        // or an explicitly selected schedule when the task is submitted.
        if (quickAdd.expanded || quickAdd.hasDetails) {
            ok = todayVm.addTaskDetailed(
                titleField.text, notesField.text, quickAdd.priority,
                calendarCheck.checked, allDayCheck.checked,
                dateField.dateText, timeField.timeText,
                allDayCheck.checked ? "" : durationPicker.durationText)
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
                objectName: "quickAddTitle"
                placeholderText: quickAdd.compact
                    ? "Новая задача…"
                    : "Новая задача…  напр. «Отчёт завтра 15:00»"
                Layout.fillWidth: true
                onAccepted: quickAdd.submit()
                Keys.onEscapePressed: {
                    quickAdd.expanded = false
                }
                onTextChanged: quickAdd.persistDraft()
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
                        visible: !quickAdd.compact
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
                text: quickAdd.compact ? "" : (quickAdd.expanded ? "Свернуть" : "Детали")
                variant: "ghost"
                iconName: quickAdd.expanded ? "chevron-up" : "note"
                onClicked: quickAdd.expanded = !quickAdd.expanded
                ToolTip.visible: quickAdd.compact && hovered
                ToolTip.text: quickAdd.expanded ? "Свернуть" : "Детали"
            }
            AppButton {
                text: quickAdd.compact ? "" : "Добавить"
                variant: "primary"
                iconName: "plus"
                enabled: !todayVm.busy
                onClicked: quickAdd.submit()
                ToolTip.visible: quickAdd.compact && hovered
                ToolTip.text: "Добавить"
            }
        }

        RowLayout {
            visible: quickAdd.draftRestored || quickAdd._draftTouched
            Layout.fillWidth: true
            Label {
                objectName: "quickAddDraftNotice"
                text: quickAdd.draftRestored
                      ? "Черновик восстановлен" : "Несохранённый черновик"
                color: Theme.textMuted
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                Layout.fillWidth: true
            }
            AppButton {
                objectName: "quickAddDiscardDraft"
                text: "Сбросить черновик"
                variant: "ghost"
                onClicked: quickAdd.clearForm()
            }
        }
        Label {
            text: quickAdd.draftStore ? quickAdd.draftStore.storageError : ""
            visible: text.length > 0
            color: Theme.warningText
            font.pixelSize: Theme.fontCaption
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
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
                objectName: "quickAddNotes"
                placeholderText: "Заметка (необязательно)"
                Layout.fillWidth: true
                onTextChanged: quickAdd.persistDraft()
            }

            RowLayout {
                spacing: Theme.spacingMd
                Layout.fillWidth: true

                CheckBox {
                    id: calendarCheck
                    objectName: "quickAddCalendar"
                    text: "Добавить в календарь"
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                    onCheckedChanged: {
                        // дата по умолчанию — сегодня (правило в Python)
                        if (checked && dateField.dateText === "") {
                            var res = todayVm.applyEditorPreset("today", "allday", "", "")
                            if (res.ok) dateField.dateText = res.dateText
                        }
                        quickAdd.persistDraft()
                    }
                }
                CheckBox {
                    id: allDayCheck
                    objectName: "quickAddAllDay"
                    onCheckedChanged: quickAdd.persistDraft()
                    text: "Весь день"
                    visible: calendarCheck.checked
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                }
                Item { Layout.fillWidth: true }
            }

            Flow {
                spacing: Theme.spacingSm
                visible: calendarCheck.checked
                Layout.fillWidth: true

                DatePickerField {
                    id: dateField
                    objectName: "quickAddDate"
                    width: 230
                    onDateTextChanged: quickAdd.persistDraft()
                }
                TimePickerField {
                    id: timeField
                    objectName: "quickAddTime"
                    visible: !allDayCheck.checked
                    onAccepted: quickAdd.submit()
                    onTimeTextChanged: quickAdd.persistDraft()
                }
            }

            DurationPicker {
                id: durationPicker
                objectName: "quickAddDuration"
                visible: calendarCheck.checked && !allDayCheck.checked
                presets: todayVm.durationPresets
                Layout.fillWidth: true
                onDurationTextChanged: {
                    if (quickAdd._draftReady && !quickAdd._draftHydrating)
                        quickAdd._durationCustomized = true
                    quickAdd.persistDraft()
                }
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
