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
    id: taskEditorDialog

    property var vm
    property bool isEdit: false
    property string taskUid: ""
    property bool recurringInstance: false
    property bool linkedTask: false

    // Экземпляр ЛОКАЛЬНОЙ серии (Phase 3.2A): сохранение всегда идёт через
    // явный выбор области изменений (SeriesScopeDialog).
    property bool seriesOccurrence: false
    property bool seriesException: false
    property string seriesUid: ""
    property string seriesSummaryText: ""
    property string seriesTimezone: ""
    property bool seriesLinkedToGoogle: false
    property string seriesLinkStatus: "Локальная серия"
    // Повторение для НОВОЙ задачи: включается тумблером, сохраняется
    // как локальная серия (vm.saveEditorAsSeries).
    property bool recurEnabled: false
    // Снимки исходного состояния для детекции изменений расписания/правила.
    property string _origScheduleJson: ""
    property string _origRuleJson: ""
    property var _scopeDialogObject: null
    property var _deleteSeriesConfirmObject: null
    property var _templatePickerObject: null
    property var _seriesGoogleLinkObject: null

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
        enabled: taskEditorDialog.visible
        onActivated: taskEditorDialog.submit()
    }

    // ---- открытие ----------------------------------------------------------

    function _prepareNestedPopups() {
        if (!taskEditorDialog._scopeDialogObject)
            taskEditorDialog._scopeDialogObject =
                scopeDialogFactory.createObject(taskEditorDialog.parent)
        if (!taskEditorDialog._deleteSeriesConfirmObject)
            taskEditorDialog._deleteSeriesConfirmObject =
                deleteSeriesConfirmFactory.createObject(taskEditorDialog.parent)
        if (!taskEditorDialog._templatePickerObject)
            taskEditorDialog._templatePickerObject =
                templatePickerFactory.createObject(taskEditorDialog.parent)
        if (!taskEditorDialog._seriesGoogleLinkObject)
            taskEditorDialog._seriesGoogleLinkObject =
                seriesGoogleLinkFactory.createObject(taskEditorDialog.parent)
    }

    function _resetForm(data) {
        taskEditorDialog._prepareNestedPopups()
        titleField.text = data.title || ""
        notesArea.text = data.notes || ""
        taskEditorDialog._setPriority(data.priority || 0)
        taskEditorDialog.schedMode = data.mode || "none"
        dateField.dateText = data.dateText || ""
        timeField.timeText = data.timeText || ""
        durationPicker.reset(data.durationText || "")
        completedCheck.checked = !!data.completed
        tagPicker.reset(data.tagIds || [])
        taskEditorDialog.linkedTask = !!data.isLinked
        taskEditorDialog.seriesOccurrence = !!data.isSeriesOccurrence
        taskEditorDialog.seriesException = !!data.isSeriesException
        taskEditorDialog.seriesUid = data.seriesUid || ""
        taskEditorDialog.seriesSummaryText = data.seriesSummary || ""
        taskEditorDialog.seriesTimezone = data.timezoneName || ""
        taskEditorDialog.seriesLinkedToGoogle = !!data.seriesLinkedToGoogle
        taskEditorDialog.seriesLinkStatus = data.seriesLinkStatus || "Локальная серия"
        taskEditorDialog.recurEnabled = !!data.recurring || taskEditorDialog.seriesOccurrence
        ruleEditor.reset(data.rule || {},
                         (data.recurring || data.isSeriesOccurrence) ? "custom" : "")
        taskEditorDialog._origScheduleJson = taskEditorDialog._scheduleJson()
        taskEditorDialog._origRuleJson = JSON.stringify(ruleEditor.ruleMap())
        taskEditorDialog._updateRecurrenceSummary()
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

    function openTemplatePicker() {
        taskEditorDialog._prepareNestedPopups()
        taskEditorDialog._templatePickerObject.templates =
            taskEditorDialog.vm ? taskEditorDialog.vm.taskTemplates : []
        taskEditorDialog._templatePickerObject.open()
    }

    // Создание из шаблона: форма предзаполнена, ничего не сохранено
    // до явного «Создать». data — vm.templatePrefill(uid).
    function openFromTemplate(data) {
        if (!data || !data.exists)
            return
        isEdit = false
        taskUid = ""
        recurringInstance = false
        _resetForm(data)
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

    // ---- повторение (локальные серии, Phase 3.2A) ------------------------

    function _scheduleJson() {
        return JSON.stringify([
            taskEditorDialog.schedMode, dateField.dateText, timeField.timeText,
            durationPicker.durationText
        ])
    }

    function _recurrencePayload() {
        return {
            title: titleField.text,
            notes: notesArea.text,
            priority: priorityRow.current,
            scheduled: taskEditorDialog.schedMode !== "none",
            isAllDay: taskEditorDialog.schedMode === "allday",
            dateText: taskEditorDialog.schedMode !== "none" ? dateField.dateText : "",
            timeText: taskEditorDialog.schedMode === "timed" ? timeField.timeText : "",
            durationText: taskEditorDialog.schedMode === "timed" ? durationPicker.durationText : "",
            completed: completedCheck.checked,
            tagIds: tagPicker.selectedIds,
            rule: ruleEditor.ruleMap()
        }
    }

    function _updateRecurrenceSummary() {
        if (!taskEditorDialog.recurEnabled || taskEditorDialog.recurringInstance || !taskEditorDialog.vm) {
            ruleEditor.summaryText = ""
            ruleEditor.errorText = ""
            return
        }
        if (taskEditorDialog.schedMode === "none") {
            ruleEditor.summaryText = ""
            ruleEditor.errorText = "Для повторения укажите дату начала."
            return
        }
        var res = taskEditorDialog.vm.recurrenceSummary(taskEditorDialog._recurrencePayload())
        ruleEditor.summaryText = res.ok ? res.summary : ""
        ruleEditor.errorText = res.ok ? "" : res.error
    }

    function _saveScoped(scope) {
        var ok = taskEditorDialog.vm.saveOccurrenceScoped(
            taskEditorDialog.taskUid, scope, taskEditorDialog._recurrencePayload())
        if (ok)
            taskEditorDialog.close()
    }

    // ---- режимы и пресеты -----------------------------------------------------

    function _setMode(value) {
        if (taskEditorDialog.recurringInstance)
            return
        if (value === taskEditorDialog.schedMode)
            return
        taskEditorDialog.schedMode = value
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
        taskEditorDialog._updateRecurrenceSummary()
    }

    function _applyPreset(presetId) {
        if (taskEditorDialog.recurringInstance)
            return
        var res = vm.applyEditorPreset(
            presetId, taskEditorDialog.schedMode, dateField.dateText, timeField.timeText)
        if (!res.ok)
            return
        taskEditorDialog.schedMode = res.mode
        dateField.dateText = res.dateText
        timeField.timeText = res.timeText
        taskEditorDialog._updateRecurrenceSummary()
    }

    function _setPriority(p) {
        priorityRow.current = Math.max(0, Math.min(3, p))
    }

    function submit() {
        if (vm.busy)
            return
        // Экземпляр локальной серии: область изменений выбирается ЯВНО,
        // случайного «применить ко всем будущим» нет.
        if (taskEditorDialog.isEdit && taskEditorDialog.seriesOccurrence) {
            var scheduleChanged = taskEditorDialog._scheduleJson() !== taskEditorDialog._origScheduleJson
            var ruleChanged =
                JSON.stringify(ruleEditor.ruleMap()) !== taskEditorDialog._origRuleJson
            taskEditorDialog._scopeDialogObject.openForSave(
                scheduleChanged, ruleChanged,
                taskEditorDialog.seriesLinkedToGoogle)
            return
        }
        // Новая задача с включённым повторением -> локальная серия.
        if (!taskEditorDialog.isEdit && taskEditorDialog.recurEnabled && !taskEditorDialog.recurringInstance) {
            if (vm.saveEditorAsSeries(taskEditorDialog._recurrencePayload()))
                taskEditorDialog.close()
            return
        }
        var ok = vm.saveEditorWithTags(
            taskUid,
            titleField.text,
            notesArea.text,
            priorityRow.current,
            taskEditorDialog.schedMode !== "none",
            taskEditorDialog.schedMode === "allday",
            taskEditorDialog.schedMode !== "none" ? dateField.dateText : "",
            taskEditorDialog.schedMode === "timed" ? timeField.timeText : "",
            taskEditorDialog.schedMode === "timed" ? durationPicker.durationText : "",
            completedCheck.checked,
            tagPicker.selectedIds
        )
        if (ok)
            taskEditorDialog.close()
    }

    Component {
        id: scopeDialogFactory
        SeriesScopeDialog {
            objectName: "seriesScopeDialog"
            onScopeChosen: scope => taskEditorDialog._saveScoped(scope)
        }
    }

    Component {
        id: seriesGoogleLinkFactory
        SeriesGoogleLinkDialog {
            vm: taskEditorDialog.vm
        }
    }

    Component {
        id: deleteSeriesConfirmFactory
        ConfirmDialog {
            objectName: "seriesDeleteConfirm"
            headerText: "Удалить локальную серию?"
            message: "Будущие невыполненные экземпляры будут удалены. "
                     + "Выполненные экземпляры и прошлая история сохранятся."
            confirmText: "Удалить серию"
            onConfirmed: uid => {
                if (taskEditorDialog.vm.deleteSeries(uid))
                    taskEditorDialog.close()
            }
        }
    }

    Component {
        id: templatePickerFactory
        TemplatePicker {
            objectName: "taskTemplatePicker"
            onTemplateChosen: uid => {
                var data = taskEditorDialog.vm.templatePrefill(uid)
                if (data && data.exists)
                    taskEditorDialog._resetForm(data)
            }
        }
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
                    name: taskEditorDialog.isEdit ? "edit" : "plus"
                    color: Theme.accent
                    size: 19
                }
            }
            Label {
                text: taskEditorDialog.isEdit ? "Редактировать задачу" : "Новая задача"
                font.pixelSize: Theme.fontTitle
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textPrimary
                Layout.alignment: Qt.AlignVCenter
            }
            SeriesBadge {
                isLocalSeries: taskEditorDialog.seriesOccurrence
                isGoogleSeries: taskEditorDialog.recurringInstance
                isException: taskEditorDialog.seriesException
                Layout.alignment: Qt.AlignVCenter
            }
            Item { Layout.fillWidth: true }
            AppButton {
                visible: !taskEditorDialog.isEdit
                text: "Из шаблона"
                variant: "ghost"
                iconName: "template"
                Accessible.name: "Создать из шаблона"
                onClicked: taskEditorDialog.openTemplatePicker()
            }
            IconButton {
                iconName: "close"
                tip: "Закрыть (Esc)"
                onClicked: taskEditorDialog.close()
            }
        }

        // ---- название: главное поле ----
        AppTextField {
            id: titleField
            placeholderText: "Что нужно сделать?"
            font.pixelSize: Theme.fontSubtitle + 1
            Layout.fillWidth: true
            onAccepted: taskEditorDialog.submit()
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
            current: taskEditorDialog.schedMode
            enabled: !taskEditorDialog.recurringInstance
            options: [
                { label: "Без даты", value: "none" },
                { label: "Весь день", value: "allday" },
                { label: "Со временем", value: "timed" }
            ]
            onSelected: value => taskEditorDialog._setMode(value)
        }

        GridLayout {
            visible: taskEditorDialog.schedMode !== "none"
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
                enabled: !taskEditorDialog.recurringInstance
                Layout.fillWidth: true
                Layout.maximumWidth: 300
                onDateTextChanged: taskEditorDialog._updateRecurrenceSummary()
            }

            Label {
                visible: taskEditorDialog.schedMode === "timed"
                text: "Время"
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textSecondary
            }
            TimePickerField {
                id: timeField
                visible: taskEditorDialog.schedMode === "timed"
                enabled: !taskEditorDialog.recurringInstance
                onAccepted: taskEditorDialog.submit()
                onTimeTextChanged: taskEditorDialog._updateRecurrenceSummary()
            }

            Label {
                visible: taskEditorDialog.schedMode === "timed"
                text: "Длительность"
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textSecondary
                Layout.alignment: Qt.AlignTop
                Layout.topMargin: 6
            }
            DurationPicker {
                id: durationPicker
                visible: taskEditorDialog.schedMode === "timed"
                enabled: !taskEditorDialog.recurringInstance
                presets: taskEditorDialog.vm ? taskEditorDialog.vm.durationPresets : []
                Layout.fillWidth: true
            }
        }

        SchedulePresetBar {
            presets: taskEditorDialog.vm ? taskEditorDialog.vm.editorPresets : []
            enabled: !taskEditorDialog.recurringInstance
            plusHourEnabled: taskEditorDialog.schedMode === "timed"
                             && timeField.timeText.length > 0
            onTriggered: presetId => taskEditorDialog._applyPreset(presetId)
            Layout.fillWidth: true
        }

        // ---- повторение (локальная серия, Phase 3.2A) ----
        RowLayout {
            visible: !taskEditorDialog.recurringInstance
                     && (!taskEditorDialog.isEdit || taskEditorDialog.seriesOccurrence)
            spacing: Theme.spacingSm
            Layout.fillWidth: true
            Layout.topMargin: Theme.spacingXs

            Switch {
                id: recurSwitch
                text: "Повторять"
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
                checked: taskEditorDialog.recurEnabled
                // Экземпляр существующей серии не «выключается» из серии
                // тумблером: серией управляют области изменений и действия
                // «остановить/удалить серию».
                enabled: !taskEditorDialog.isEdit || !taskEditorDialog.seriesOccurrence
                Accessible.name: "Повторять задачу"
                onToggled: {
                    taskEditorDialog.recurEnabled = checked
                    if (checked && taskEditorDialog.schedMode === "none")
                        taskEditorDialog._setMode("allday")
                    taskEditorDialog._updateRecurrenceSummary()
                }
            }
            Label {
                visible: taskEditorDialog.recurEnabled && taskEditorDialog.seriesTimezone.length > 0
                text: "Часовой пояс: " + taskEditorDialog.seriesTimezone
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textMuted
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Label {
                visible: taskEditorDialog.recurEnabled && taskEditorDialog.seriesTimezone.length === 0
                         && taskEditorDialog.vm !== undefined && taskEditorDialog.vm !== null
                text: "Часовой пояс: " + (taskEditorDialog.vm ? taskEditorDialog.vm.localTimezoneName : "")
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textMuted
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
        }

        Label {
            visible: taskEditorDialog.seriesOccurrence && taskEditorDialog.seriesSummaryText.length > 0
            text: "Серия: " + taskEditorDialog.seriesSummaryText
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textSecondary
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        RowLayout {
            visible: taskEditorDialog.isEdit && taskEditorDialog.seriesOccurrence
            Layout.fillWidth: true
            spacing: Theme.spacingSm
            Label {
                text: "Google: " + taskEditorDialog.seriesLinkStatus
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textSecondary
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
                Accessible.name: text
            }
            AppButton {
                text: "Google Calendar…"
                iconName: "repeat"
                variant: "secondary"
                enabled: taskEditorDialog.vm ? !taskEditorDialog.vm.busy : true
                Accessible.description:
                    "Предпросмотр данных и явные действия связи серии с Google Calendar"
                onClicked: {
                    taskEditorDialog._prepareNestedPopups()
                    taskEditorDialog._seriesGoogleLinkObject.openFor(
                        taskEditorDialog.seriesUid)
                }
            }
        }

        RecurrenceRuleEditor {
            id: ruleEditor
            visible: taskEditorDialog.recurEnabled && !taskEditorDialog.recurringInstance
                     && (!taskEditorDialog.isEdit || taskEditorDialog.seriesOccurrence)
            presets: taskEditorDialog.vm ? taskEditorDialog.vm.recurrencePresets : []
            Layout.fillWidth: true
            onRuleEdited: taskEditorDialog._updateRecurrenceSummary()
        }

        Label {
            visible: taskEditorDialog.recurEnabled && !taskEditorDialog.isEdit
            text: "Локальная серия: повторения существуют только в Planner "
                  + "Desktop и не отправляются в Google Calendar."
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }

        TagPicker {
            id: tagPicker
            Layout.fillWidth: true
            availableTags: taskEditorDialog.vm ? taskEditorDialog.vm.availableTags : []
            onCreateRequested: name => {
                var result = taskEditorDialog.vm.createTag(name)
                handleCreated(result)
            }
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
                visible: taskEditorDialog.isEdit
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
            }
        }

        // ---- предупреждение о повторяющемся событии ----
        RowLayout {
            visible: taskEditorDialog.recurringInstance
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

        RowLayout {
            visible: taskEditorDialog.isEdit && taskEditorDialog.linkedTask
                     && !taskEditorDialog.recurringInstance && !taskEditorDialog.seriesOccurrence
            spacing: Theme.spacingSm
            Layout.fillWidth: true

            AppIcon { name: "info"; size: 15; color: Theme.warningText }
            Label {
                text: "Эта обычная задача связана с Google Calendar. В Phase 3.2A "
                      + "её нельзя преобразовать в локальную повторяющуюся серию."
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
                text: taskEditorDialog.vm ? taskEditorDialog.vm.editorError : ""
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
                visible: taskEditorDialog.isEdit && !taskEditorDialog.seriesOccurrence
                text: "Удалить"
                variant: "ghost"
                iconName: "trash"
                enabled: taskEditorDialog.vm ? !taskEditorDialog.vm.busy : true
                Accessible.description: "Удалить обычную задачу"
                onClicked: {
                    var uid = taskEditorDialog.taskUid
                    taskEditorDialog.close()
                    taskEditorDialog.deleteRequested(uid)
                }
            }
            AppButton {
                visible: taskEditorDialog.isEdit && taskEditorDialog.seriesOccurrence
                text: "Серия…"
                variant: "ghost"
                iconName: "snooze"
                enabled: taskEditorDialog.vm ? !taskEditorDialog.vm.busy : true
                Accessible.description:
                    "Остановить серию с этого экземпляра или удалить всю серию"
                onClicked: seriesMenu.open()

                Menu {
                    id: seriesMenu
                    y: parent.height
                    MenuItem {
                        text: "Удалить только этот экземпляр…"
                        onTriggered: {
                            var uid = taskEditorDialog.taskUid
                            taskEditorDialog.close()
                            taskEditorDialog.deleteRequested(uid)
                        }
                    }
                    MenuItem {
                        text: "Дублировать как обычную задачу"
                        onTriggered: {
                            if (taskEditorDialog.vm.duplicateTask(
                                    taskEditorDialog.taskUid))
                                taskEditorDialog.close()
                        }
                    }
                    MenuSeparator {}
                    MenuItem {
                        text: "Остановить с этого экземпляра"
                        onTriggered: {
                            var uid = taskEditorDialog.taskUid
                            taskEditorDialog.close()
                            taskEditorDialog.vm.stopSeriesFromOccurrence(uid)
                        }
                    }
                    MenuItem {
                        text: "Удалить всю серию…"
                        onTriggered: {
                            taskEditorDialog._prepareNestedPopups()
                            taskEditorDialog._deleteSeriesConfirmObject.openFor(
                                taskEditorDialog.seriesUid)
                        }
                    }
                }
            }
            AppButton {
                visible: taskEditorDialog.isEdit
                         && !taskEditorDialog.seriesOccurrence
                text: "Дублировать"
                variant: "secondary"
                iconName: "plus"
                enabled: taskEditorDialog.vm ? !taskEditorDialog.vm.busy : true
                Accessible.name: "Дублировать редактируемую задачу"
                onClicked: {
                    if (taskEditorDialog.vm.duplicateTask(taskEditorDialog.taskUid))
                        taskEditorDialog.close()
                }
            }
            Item { Layout.fillWidth: true }
            AppButton {
                text: "Отмена"
                variant: "ghost"
                onClicked: taskEditorDialog.close()
            }
            AppButton {
                text: taskEditorDialog.isEdit ? "Сохранить" : "Создать"
                variant: "primary"
                iconName: taskEditorDialog.isEdit ? "check" : "plus"
                enabled: taskEditorDialog.vm ? !taskEditorDialog.vm.busy : true
                onClicked: taskEditorDialog.submit()
            }
        }
    }
}
