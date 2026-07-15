import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as B
import QtQuick.Layouts
import QtQuick.Effects

import "../theme"

// Создание/правка локального шаблона задачи (страница «Настройки»).
//
// settingsVm — SettingsViewModel: templateDataFor / createTemplate /
// updateTemplate / templateError. actionsVm — любой TaskActionsViewModel
// (для recurrencePresets/recurrenceSummary/availableTags/createTag).
// Шаблон локален: Google-метаданные в него не попадают.
Dialog {
    id: dialog

    property var settingsVm
    property var actionsVm
    property bool isEdit: false
    property string templateUid: ""
    property string templateKind: "ordinary"
    property string scheduleMode: "none"

    parent: Overlay.overlay
    anchors.centerIn: parent
    modal: true
    focus: true
    width: Math.min(600, (parent ? parent.width : 600) - 48)
    height: Math.min(implicitHeight,
                     Math.max(380, (parent ? parent.height : 720) - 32))
    padding: Theme.spacingXl
    closePolicy: Popup.CloseOnEscape

    Overlay.modal: Rectangle {
        color: Qt.rgba(0.09, 0.10, 0.16, 0.42)
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

    Shortcut {
        sequence: "Ctrl+S"
        enabled: dialog.visible
        onActivated: dialog.submit()
    }

    function _resetForm(data) {
        nameField.text = data.name || ""
        titleField.text = data.title || ""
        notesArea.text = data.notes || ""
        priorityCombo.currentIndex = Math.max(0, Math.min(3, data.priority || 0))
        dialog.templateKind = data.kind || "ordinary"
        dialog.scheduleMode = data.scheduleMode || "none"
        timeField.timeText = data.timeText || ""
        durationField.text = data.durationText || ""
        tagPicker.reset(data.tagIds || [])
        ruleEditor.reset(data.rule || {}, "custom")
        dialog._updateRuleSummary()
        if (dialog.settingsVm)
            dialog.settingsVm.clearTemplateError()
    }

    function openForCreate() {
        isEdit = false
        templateUid = ""
        _resetForm(settingsVm.templateDataFor(""))
        open()
        nameField.forceActiveFocus()
    }

    function openForEdit(uid) {
        var data = settingsVm.templateDataFor(uid)
        if (!data || !data.exists)
            return
        isEdit = true
        templateUid = uid
        _resetForm(data)
        open()
        nameField.forceActiveFocus()
    }

    function _payload() {
        return {
            name: nameField.text,
            kind: dialog.templateKind,
            title: titleField.text,
            notes: notesArea.text,
            priority: priorityCombo.currentIndex,
            scheduleMode: dialog.scheduleMode,
            timeText: dialog.scheduleMode === "timed" ? timeField.timeText : "",
            durationText: dialog.scheduleMode === "timed" ? durationField.text : "",
            tagIds: tagPicker.selectedIds,
            rule: ruleEditor.ruleMap()
        }
    }

    function _updateRuleSummary() {
        if (!dialog.actionsVm || dialog.templateKind !== "recurring") {
            ruleEditor.summaryText = ""
            ruleEditor.errorText = ""
            return
        }
        // Сводка считается от «сегодня»: шаблон не хранит дату начала.
        var today = new Date()
        var iso = today.getFullYear() + "-"
                + String(today.getMonth() + 1).padStart(2, "0") + "-"
                + String(today.getDate()).padStart(2, "0")
        var res = dialog.actionsVm.recurrenceSummary({
            dateText: iso,
            isAllDay: dialog.scheduleMode !== "timed",
            timeText: timeField.timeText,
            durationText: durationField.text,
            rule: ruleEditor.ruleMap()
        })
        ruleEditor.summaryText = res.ok ? res.summary : ""
        ruleEditor.errorText = res.ok ? "" : res.error
    }

    function submit() {
        if (!settingsVm || settingsVm.templateBusy)
            return
        var ok = dialog.isEdit
                 ? settingsVm.updateTemplate(dialog.templateUid, dialog._payload())
                 : settingsVm.createTemplate(dialog._payload())
        if (ok)
            dialog.close()
    }

    contentItem: ColumnLayout {
        spacing: Theme.spacingMd

        ScrollView {
            id: formScroll
            Layout.fillWidth: true
            Layout.fillHeight: true
            implicitHeight: formColumn.implicitHeight
            contentWidth: availableWidth
            clip: true
            ScrollBar.horizontal.policy: ScrollBar.AlwaysOff

            ColumnLayout {
                id: formColumn
                width: formScroll.availableWidth
                spacing: Theme.spacingMd

                RowLayout {
                    spacing: Theme.spacingSm
                    Layout.fillWidth: true

                    AppIcon { name: "template"; size: 19; color: Theme.accent }
                    Label {
                        text: dialog.isEdit ? "Редактировать шаблон" : "Новый шаблон"
                        font.pixelSize: Theme.fontTitle
                        font.family: Theme.fontFamily
                        font.weight: Font.DemiBold
                        color: Theme.textPrimary
                        Layout.fillWidth: true
                    }
                    IconButton {
                        iconName: "close"
                        tip: "Закрыть (Esc)"
                        onClicked: dialog.close()
                    }
                }

                AppTextField {
                    id: nameField
                    placeholderText: "Название шаблона (до 60 символов)"
                    maximumLength: 60
                    Layout.fillWidth: true
                }

                SegmentedControl {
                    current: dialog.templateKind
                    options: [
                        { label: "Обычная задача", value: "ordinary" },
                        { label: "Повторяющаяся серия", value: "recurring" }
                    ]
                    onSelected: value => {
                        dialog.templateKind = value
                        dialog._updateRuleSummary()
                    }
                }

                AppTextField {
                    id: titleField
                    placeholderText: "Заголовок задачи"
                    Layout.fillWidth: true
                }

                Rectangle {
                    Layout.fillWidth: true
                    Layout.preferredHeight: 64
                    radius: Theme.radiusSmall
                    color: Theme.surface
                    border.color: notesArea.activeFocus ? Theme.accent : Theme.border
                    border.width: notesArea.activeFocus ? 1.6 : 1

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
                    spacing: Theme.spacingSm
                    Layout.fillWidth: true

                    Label {
                        text: "Приоритет"
                        font.pixelSize: Theme.fontCaption
                        font.family: Theme.fontFamily
                        color: Theme.textSecondary
                    }
                    ComboBox {
                        id: priorityCombo
                        model: ["Нет", "Низкий", "Средний", "Высокий"]
                        Accessible.name: "Приоритет шаблона"
                    }
                    Item { Layout.fillWidth: true }
                }

                Label {
                    text: "Планирование по умолчанию"
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    font.weight: Font.DemiBold
                    color: Theme.textMuted
                }

                SegmentedControl {
                    current: dialog.scheduleMode
                    options: dialog.templateKind === "recurring"
                        ? [
                            { label: "Весь день", value: "allday" },
                            { label: "Со временем", value: "timed" }
                          ]
                        : [
                            { label: "Без даты", value: "none" },
                            { label: "Весь день", value: "allday" },
                            { label: "Со временем", value: "timed" }
                          ]
                    onSelected: value => {
                        dialog.scheduleMode = value
                        dialog._updateRuleSummary()
                    }
                }

                RowLayout {
                    visible: dialog.scheduleMode === "timed"
                    spacing: Theme.spacingSm
                    Layout.fillWidth: true

                    Label {
                        text: "Время"
                        font.pixelSize: Theme.fontCaption
                        font.family: Theme.fontFamily
                        color: Theme.textSecondary
                    }
                    TimePickerField {
                        id: timeField
                        onTimeTextChanged: dialog._updateRuleSummary()
                    }
                    Label {
                        text: "Длительность, мин."
                        font.pixelSize: Theme.fontCaption
                        font.family: Theme.fontFamily
                        color: Theme.textSecondary
                    }
                    AppTextField {
                        id: durationField
                        placeholderText: "60"
                        validator: IntValidator { bottom: 1; top: 1440 }
                        Layout.preferredWidth: 80
                    }
                    Item { Layout.fillWidth: true }
                }

                // ---- правило повторения (для recurring-шаблона) ----
                ColumnLayout {
                    visible: dialog.templateKind === "recurring"
                    spacing: Theme.spacingSm
                    Layout.fillWidth: true

                    Label {
                        text: "Правило повторения по умолчанию"
                        font.pixelSize: Theme.fontCaption
                        font.family: Theme.fontFamily
                        font.weight: Font.DemiBold
                        color: Theme.textMuted
                    }
                    RecurrenceRuleEditor {
                        id: ruleEditor
                        presets: dialog.actionsVm ? dialog.actionsVm.recurrencePresets : []
                        customVisible: true
                        Layout.fillWidth: true
                        onRuleEdited: dialog._updateRuleSummary()
                    }
                }

                TagPicker {
                    id: tagPicker
                    Layout.fillWidth: true
                    availableTags: dialog.actionsVm ? dialog.actionsVm.availableTags : []
                    onCreateRequested: name => {
                        var result = dialog.actionsVm.createTag(name)
                        handleCreated(result)
                    }
                }

                Label {
                    text: "Шаблон хранится только в Planner Desktop и не "
                          + "синхронизируется с Google Calendar."
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    color: Theme.textMuted
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                Rectangle {
                    visible: templateErrorLabel.text.length > 0
                    Layout.fillWidth: true
                    implicitHeight: templateErrorLabel.implicitHeight + 2 * Theme.spacingSm
                    radius: Theme.radiusSmall
                    color: Theme.dangerSoft
                    border.color: Qt.alpha(Theme.danger, 0.35)
                    border.width: 1

                    Label {
                        id: templateErrorLabel
                        anchors.fill: parent
                        anchors.margins: Theme.spacingSm
                        text: dialog.settingsVm ? dialog.settingsVm.templateError : ""
                        font.pixelSize: Theme.fontCaption
                        font.family: Theme.fontFamily
                        color: Theme.danger
                        wrapMode: Text.WordWrap
                        verticalAlignment: Text.AlignVCenter
                    }
                }
            }
        }

        RowLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true

            Item { Layout.fillWidth: true }
            AppButton {
                text: "Отмена"
                variant: "ghost"
                onClicked: dialog.close()
            }
            AppButton {
                text: dialog.isEdit ? "Сохранить" : "Создать шаблон"
                variant: "primary"
                iconName: dialog.isEdit ? "check" : "plus"
                enabled: dialog.settingsVm ? !dialog.settingsVm.templateBusy : true
                onClicked: dialog.submit()
            }
        }
    }
}
