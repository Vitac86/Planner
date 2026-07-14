import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as B
import QtQuick.Layouts
import QtQuick.Effects

import "../theme"

// Единый диалог создания/редактирования задачи.
//
// vm — Today/Calendar/History ViewModel: общий контракт TaskActionsViewModel
// (saveEditor/editorDataFor/applyEditorPreset/editorError/busy).
//
// Планирование — сегментами «Без даты» / «Весь день» / «Со временем»
// с нативными контролами (DatePickerField/TimePickerField/DurationPicker)
// и пресетами (SchedulePresetBar, семантика в domain/scheduling.py).
// Сырые строки дат пользователь не видит; финальная валидация — Python:
// при ошибке диалог остаётся открытым и редактируемым.
//
// Клавиатура: Enter в полях названия/времени сохраняет, Ctrl+S сохраняет,
// Esc закрывает. Кнопки выключаются на время операции (vm.busy).
Dialog {
    id: dialog

    property var vm
    property bool isEdit: false
    property string taskUid: ""
    property bool recurringInstance: false

    // Режим планирования: "none" | "allday" | "timed" (domain/scheduling.py).
    property string schedMode: "none"

    signal deleteRequested(string uid)

    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    focus: true
    width: Math.min(600, (parent ? parent.width : 600) - 48)
    height: Math.min(implicitHeight,
                     Math.max(360, (parent ? parent.height : 720) - 32))
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

    // Ctrl+S сохраняет, пока диалог открыт (и не мешает окну, когда закрыт).
    Shortcut {
        sequence: "Ctrl+S"
        enabled: dialog.visible
        onActivated: dialog.submit()
    }

    // ---- открытие ----------------------------------------------------------

    function _resetForm(data) {
        titleField.text = data.title || ""
        notesArea.text = data.notes || ""
        dialog._setPriority(data.priority || 0)
        dialog.schedMode = data.mode || "none"
        dateField.dateText = data.dateText || ""
        timeField.timeText = data.timeText || ""
        durationPicker.reset(data.durationText || "")
        completedCheck.checked = !!data.completed
        vm.clearEditorError()
        Qt.callLater(function() {
            if (formScroll.contentItem)
                formScroll.contentItem.contentY = 0
        })
    }

    function openForCreate(prefillDateText) {
        isEdit = false
        taskUid = ""
        recurringInstance = false
        _resetForm({
            mode: (prefillDateText && prefillDateText.length > 0) ? "allday" : "none",
            dateText: prefillDateText || ""
        })
        open()
        titleField.forceActiveFocus()
    }

    // Ctrl+Shift+N: сразу запланированная задача (ближайший час; дата —
    // сегодня или переданная, например выбранный день календаря).
    function openForCreateScheduled(prefillDateText) {
        isEdit = false
        taskUid = ""
        recurringInstance = false
        var defaults = vm.newScheduledDefaults()
        _resetForm({
            mode: defaults.mode,
            dateText: (prefillDateText && prefillDateText.length > 0)
                      ? prefillDateText : defaults.dateText,
            timeText: defaults.timeText
        })
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
        _resetForm(data)
        open()
        titleField.forceActiveFocus()
    }

    // ---- режимы и пресеты -----------------------------------------------------

    function _setMode(value) {
        if (dialog.recurringInstance)
            return
        if (value === dialog.schedMode)
            return
        dialog.schedMode = value
        // Дозаполнение по умолчанию — из Python (DEFAULT_START_TIME и т.п.).
        if (value !== "none"
                && (dateField.dateText === ""
                    || (value === "timed" && timeField.timeText === ""))) {
            var res = vm.applyEditorPreset(
                "today", value, dateField.dateText, timeField.timeText)
            if (res.ok) {
                if (dateField.dateText === "")
                    dateField.dateText = res.dateText
                if (value === "timed" && timeField.timeText === "")
                    timeField.timeText = res.timeText
            }
        }
    }

    function _applyPreset(presetId) {
        if (dialog.recurringInstance)
            return
        var res = vm.applyEditorPreset(
            presetId, dialog.schedMode, dateField.dateText, timeField.timeText)
        if (!res.ok)
            return
        dialog.schedMode = res.mode
        dateField.dateText = res.dateText
        timeField.timeText = res.timeText
    }

    function _setPriority(p) {
        priorityRow.current = Math.max(0, Math.min(3, p))
    }

    function submit() {
        if (vm.busy)
            return
        var ok = vm.saveEditor(
            taskUid,
            titleField.text,
            notesArea.text,
            priorityRow.current,
            dialog.schedMode !== "none",
            dialog.schedMode === "allday",
            dialog.schedMode !== "none" ? dateField.dateText : "",
            dialog.schedMode === "timed" ? timeField.timeText : "",
            dialog.schedMode === "timed" ? durationPicker.durationText : "",
            completedCheck.checked
        )
        if (ok)
            dialog.close()
    }

    contentItem: ColumnLayout {
        spacing: Theme.spacingMd

        // Основная форма прокручивается, а действия остаются закреплены снизу.
        // На обычной высоте ScrollBar скрыт и внешний вид остаётся прежним;
        // при минимальном окне 680x560 footer всё равно всегда доступен.
        ScrollView {
            id: formScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            implicitHeight: formColumn.implicitHeight
            contentWidth: availableWidth
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff
            ScrollBar.vertical.policy: ScrollBar.AsNeeded

            ColumnLayout {
                id: formColumn
                width: formScroll.availableWidth
                spacing: Theme.spacingMd

        // ---- шапка ----
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
                tip: "Закрыть (Esc)"
                onClicked: dialog.close()
            }
        }

        // ---- название: главное поле ----
        AppTextField {
            id: titleField
            placeholderText: "Что нужно сделать?"
            font.pixelSize: Theme.fontSubtitle + 1
            Layout.fillWidth: true
            onAccepted: dialog.submit()
        }

        // ---- заметки ----
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 74
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

        // ---- планирование ----
        Label {
            text: "Планирование"
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            font.weight: Font.DemiBold
            font.letterSpacing: 0.4
            color: Theme.textMuted
            Layout.topMargin: Theme.spacingXs
        }

        SegmentedControl {
            current: dialog.schedMode
            enabled: !dialog.recurringInstance
            options: [
                { label: "Без даты", value: "none" },
                { label: "Весь день", value: "allday" },
                { label: "Со временем", value: "timed" }
            ]
            onSelected: value => dialog._setMode(value)
        }

        GridLayout {
            visible: dialog.schedMode !== "none"
            columns: 2
            columnSpacing: Theme.spacingSm
            rowSpacing: Theme.spacingSm
            Layout.fillWidth: true

            Label {
                text: "Дата"
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textSecondary
            }
            DatePickerField {
                id: dateField
                enabled: !dialog.recurringInstance
                Layout.fillWidth: true
                Layout.maximumWidth: 300
            }

            Label {
                visible: dialog.schedMode === "timed"
                text: "Время"
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textSecondary
            }
            TimePickerField {
                id: timeField
                visible: dialog.schedMode === "timed"
                enabled: !dialog.recurringInstance
                onAccepted: dialog.submit()
            }

            Label {
                visible: dialog.schedMode === "timed"
                text: "Длительность"
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textSecondary
                Layout.alignment: Qt.AlignTop
                Layout.topMargin: 6
            }
            DurationPicker {
                id: durationPicker
                visible: dialog.schedMode === "timed"
                enabled: !dialog.recurringInstance
                presets: dialog.vm ? dialog.vm.durationPresets : []
                Layout.fillWidth: true
            }
        }

        SchedulePresetBar {
            presets: dialog.vm ? dialog.vm.editorPresets : []
            enabled: !dialog.recurringInstance
            plusHourEnabled: dialog.schedMode === "timed"
                             && timeField.timeText.length > 0
            onTriggered: presetId => dialog._applyPreset(presetId)
            Layout.fillWidth: true
        }

        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

        // ---- приоритет и «выполнено» ----
        RowLayout {
            spacing: Theme.spacingMd
            Layout.fillWidth: true

            Label {
                text: "Приоритет"
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
                color: Theme.textSecondary
            }
            Row {
                id: priorityRow
                property int current: 0
                spacing: Theme.spacingXs

                Repeater {
                    model: ["Нет", "Низкий", "Средний", "Высокий"]
                    delegate: Rectangle {
                        id: prioChip
                        required property string modelData
                        required property int index

                        readonly property bool active: priorityRow.current === index
                        implicitHeight: 30
                        implicitWidth: prioRow.implicitWidth + 20
                        radius: Theme.radiusPill
                        color: active ? Theme.priorityBgColor(index)
                             : prioHover.hovered ? Theme.surfaceHover : Theme.surface
                        border.color: active ? Theme.priorityColor(index) : Theme.border
                        border.width: 1
                        Behavior on color { ColorAnimation { duration: 90 } }

                        Row {
                            id: prioRow
                            anchors.centerIn: parent
                            spacing: 5
                            Rectangle {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 8; height: 8; radius: 4
                                color: prioChip.index > 0
                                       ? Theme.priorityColor(prioChip.index)
                                       : Theme.textMuted
                            }
                            Label {
                                text: prioChip.modelData
                                font.pixelSize: Theme.fontCaption + 1
                                font.family: Theme.fontFamily
                                font.weight: prioChip.active ? Font.DemiBold : Font.Medium
                                color: prioChip.active
                                       ? Theme.priorityColor(prioChip.index)
                                       : Theme.textSecondary
                            }
                        }
                        HoverHandler { id: prioHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            onTapped: {
                                prioChip.forceActiveFocus()
                                priorityRow.current = prioChip.index
                            }
                        }
                        activeFocusOnTab: true
                        Keys.onReturnPressed: priorityRow.current = prioChip.index
                        Keys.onSpacePressed: priorityRow.current = prioChip.index
                        Accessible.role: Accessible.RadioButton
                        Accessible.name: "Приоритет: " + prioChip.modelData
                        Accessible.checked: prioChip.active
                        Accessible.focusable: true

                        Rectangle {
                            anchors.fill: parent
                            anchors.margins: -2
                            radius: parent.radius
                            color: "transparent"
                            border.color: Theme.focusRing
                            border.width: 2
                            visible: prioChip.activeFocus
                        }
                    }
                }
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

        // ---- предупреждение о повторяющемся событии ----
        RowLayout {
            visible: dialog.recurringInstance
            spacing: Theme.spacingSm
            Layout.fillWidth: true

            AppIcon { name: "info"; size: 15; color: Theme.warningText }
            Label {
                text: "Это экземпляр повторяющегося события Google Calendar: "
                      + "снять дату или перенести его нельзя."
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.warningText
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
        }

        // ---- инлайн-ошибка валидации ----
        Rectangle {
            visible: errorLabel.text.length > 0
            Layout.fillWidth: true
            implicitHeight: errorLabel.implicitHeight + 2 * Theme.spacingSm
            radius: Theme.radiusSmall
            color: Theme.dangerSoft
            border.color: Qt.alpha(Theme.danger, 0.35)
            border.width: 1

            Label {
                id: errorLabel
                anchors.fill: parent
                anchors.margins: Theme.spacingSm
                text: dialog.vm ? dialog.vm.editorError : ""
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.danger
                wrapMode: Text.WordWrap
                verticalAlignment: Text.AlignVCenter
            }
        }

            }
        }

        // ---- действия: удаление отдельно слева, сохранение справа ----
        RowLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true
            Layout.topMargin: Theme.spacingXs

            AppButton {
                visible: dialog.isEdit
                text: "Удалить"
                variant: "ghost"
                iconName: "trash"
                enabled: dialog.vm ? !dialog.vm.busy : true
                onClicked: {
                    var uid = dialog.taskUid
                    dialog.close()
                    dialog.deleteRequested(uid)
                }
            }
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
                enabled: dialog.vm ? !dialog.vm.busy : true
                onClicked: dialog.submit()
            }
        }
    }
}
