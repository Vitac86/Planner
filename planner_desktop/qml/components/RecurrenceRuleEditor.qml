import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Редактор правила повторения локальной серии (Phase 3.2A).
//
// Выдаёт наружу ruleMap() — словарь для Python (viewmodels/series_rows.py):
// {frequency, interval, weekdays, monthDay, yearlyMonth, yearlyDay,
//  endMode, untilDate, occurrenceCount}. Семантика правил — в
// domain/recurrence.py; здесь только ввод. Сводку и валидацию считает
// Python (vm.recurrenceSummary), QML лишь показывает результат.
ColumnLayout {
    id: editor

    // [{id, label}] — vm.recurrencePresets.
    property var presets: []
    property string currentPreset: "every_day"

    property string frequency: "daily"
    property int interval: 1
    property var weekdays: []      // [0..6], 0 = понедельник
    property int monthDay: 0       // 0 = «как у даты начала»
    property string endMode: "never"
    property string untilDateText: ""
    property int occurrenceCount: 10
    property bool customVisible: false

    // Человекочитаемая сводка/ошибка (заполняет родитель через Python).
    property string summaryText: ""
    property string errorText: ""

    signal ruleEdited()

    readonly property var weekdayLabels: ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

    spacing: Theme.spacingSm

    function ruleMap() {
        return {
            frequency: editor.frequency,
            interval: Math.max(1, editor.interval),
            weekdays: editor.weekdays,
            monthDay: editor.monthDay,
            yearlyMonth: 0,
            yearlyDay: 0,
            endMode: editor.endMode,
            untilDate: editor.endMode === "until" ? editor.untilDateText : "",
            occurrenceCount: editor.endMode === "count" ? editor.occurrenceCount : 0
        }
    }

    function reset(rule, preset) {
        rule = rule || {}
        editor.frequency = rule.frequency || "daily"
        editor.interval = Math.max(1, rule.interval || 1)
        editor.weekdays = (rule.weekdays || []).slice()
        editor.monthDay = rule.monthDay || 0
        editor.endMode = rule.endMode || "never"
        editor.untilDateText = rule.untilDate || ""
        editor.occurrenceCount = rule.occurrenceCount || 10
        editor.currentPreset = preset || ""
        editor.customVisible = (preset === "custom")
        untilField.dateText = editor.untilDateText
        editor.ruleEdited()
    }

    function applyPreset(presetId) {
        editor.currentPreset = presetId
        editor.customVisible = presetId === "custom"
        if (presetId === "every_day") {
            editor.frequency = "daily"; editor.interval = 1
        } else if (presetId === "weekdays") {
            editor.frequency = "weekly"; editor.interval = 1
            editor.weekdays = [0, 1, 2, 3, 4]
        } else if (presetId === "weekly_same_day") {
            editor.frequency = "weekly"; editor.interval = 1
            editor.weekdays = []   // Python подставит день даты начала
        } else if (presetId === "monthly_same_day") {
            editor.frequency = "monthly"; editor.interval = 1
            editor.monthDay = 0    // «как у даты начала»
        } else if (presetId === "yearly") {
            editor.frequency = "yearly"; editor.interval = 1
        }
        editor.ruleEdited()
    }

    function _toggleWeekday(index) {
        var current = editor.weekdays.slice()
        var at = current.indexOf(index)
        if (at >= 0)
            current.splice(at, 1)
        else
            current.push(index)
        current.sort()
        editor.weekdays = current
        editor.ruleEdited()
    }

    RecurrencePresetBar {
        presets: editor.presets
        currentPreset: editor.currentPreset
        Layout.fillWidth: true
        onTriggered: presetId => editor.applyPreset(presetId)
    }

    // ---- настраиваемое правило ----
    ColumnLayout {
        visible: editor.customVisible
        spacing: Theme.spacingSm
        Layout.fillWidth: true

        RowLayout {
            spacing: Theme.spacingSm

            Label {
                text: "Повторять каждые"
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textSecondary
            }
            SpinBox {
                id: intervalSpin
                from: 1; to: 999
                value: editor.interval
                editable: true
                Accessible.name: "Интервал повторения"
                onValueModified: {
                    editor.interval = value
                    editor.ruleEdited()
                }
            }
            ComboBox {
                id: freqCombo
                model: [
                    { text: "дней", value: "daily" },
                    { text: "недель", value: "weekly" },
                    { text: "месяцев", value: "monthly" },
                    { text: "лет", value: "yearly" }
                ]
                textRole: "text"
                valueRole: "value"
                Accessible.name: "Частота повторения"
                currentIndex: {
                    var values = ["daily", "weekly", "monthly", "yearly"]
                    return Math.max(0, values.indexOf(editor.frequency))
                }
                onActivated: {
                    editor.frequency = currentValue
                    editor.ruleEdited()
                }
            }
        }

        // Дни недели (weekly).
        Flow {
            visible: editor.frequency === "weekly"
            spacing: Theme.spacingXs
            Layout.fillWidth: true

            Repeater {
                model: 7
                delegate: Rectangle {
                    id: dayChip
                    required property int index
                    readonly property bool active: editor.weekdays.indexOf(index) >= 0
                    implicitWidth: 38
                    implicitHeight: 28
                    radius: Theme.radiusPill
                    color: dayChip.active ? Theme.accentSoft : Theme.surface
                    border.color: dayChip.active ? Theme.accent : Theme.border
                    border.width: 1
                    activeFocusOnTab: true
                    Accessible.role: Accessible.CheckBox
                    Accessible.name: "День недели " + editor.weekdayLabels[index]
                    Accessible.checked: dayChip.active
                    Accessible.focusable: true

                    Label {
                        anchors.centerIn: parent
                        text: editor.weekdayLabels[dayChip.index]
                        font.pixelSize: Theme.fontCaption
                        font.family: Theme.fontFamily
                        font.weight: dayChip.active ? Font.DemiBold : Font.Medium
                        color: dayChip.active ? Theme.accent : Theme.textSecondary
                    }
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    TapHandler {
                        onTapped: {
                            dayChip.forceActiveFocus()
                            editor._toggleWeekday(dayChip.index)
                        }
                    }
                    Keys.onSpacePressed: editor._toggleWeekday(dayChip.index)
                    Keys.onReturnPressed: editor._toggleWeekday(dayChip.index)

                    Rectangle {
                        anchors.fill: parent
                        anchors.margins: -2
                        radius: parent.radius
                        color: "transparent"
                        border.color: Theme.focusRing
                        border.width: 2
                        visible: dayChip.activeFocus
                    }
                }
            }
        }

        // Число месяца (monthly). 0 = «как у даты начала».
        RowLayout {
            visible: editor.frequency === "monthly"
            spacing: Theme.spacingSm

            Label {
                text: "Число месяца"
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textSecondary
            }
            SpinBox {
                from: 0; to: 31
                value: editor.monthDay
                editable: true
                Accessible.name: "Число месяца (0 — как у даты начала)"
                onValueModified: {
                    editor.monthDay = value
                    editor.ruleEdited()
                }
            }
            Label {
                text: "0 — как у даты начала; 29–31 бывают не в каждом месяце"
                font.pixelSize: Theme.fontCaption - 1
                font.family: Theme.fontFamily
                color: Theme.textMuted
                Layout.fillWidth: true
                elide: Text.ElideRight
            }
        }
    }

    // ---- окончание серии ----
    RowLayout {
        spacing: Theme.spacingSm
        Layout.fillWidth: true

        Label {
            text: "Окончание"
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textSecondary
        }
        ComboBox {
            id: endCombo
            model: [
                { text: "Никогда", value: "never" },
                { text: "До даты", value: "until" },
                { text: "После N раз", value: "count" }
            ]
            textRole: "text"
            valueRole: "value"
            Accessible.name: "Окончание серии"
            currentIndex: {
                var values = ["never", "until", "count"]
                return Math.max(0, values.indexOf(editor.endMode))
            }
            onActivated: {
                editor.endMode = currentValue
                editor.ruleEdited()
            }
        }
        DatePickerField {
            id: untilField
            visible: editor.endMode === "until"
            Layout.preferredWidth: 170
            onDateTextChanged: {
                if (editor.endMode === "until"
                        && editor.untilDateText !== dateText) {
                    editor.untilDateText = dateText
                    editor.ruleEdited()
                }
            }
        }
        SpinBox {
            visible: editor.endMode === "count"
            from: 1; to: 999
            value: editor.occurrenceCount
            editable: true
            Accessible.name: "Число повторений"
            onValueModified: {
                editor.occurrenceCount = value
                editor.ruleEdited()
            }
        }
        Item { Layout.fillWidth: true }
    }

    // ---- сводка / ошибка ----
    SeriesSummary {
        summary: editor.errorText.length > 0 ? editor.errorText : editor.summaryText
        isError: editor.errorText.length > 0
        Layout.fillWidth: true
    }
}
