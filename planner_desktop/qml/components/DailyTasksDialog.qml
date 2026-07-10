import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as B
import QtQuick.Layouts
import QtQuick.Effects

import "../theme"

// Управление ежедневными (повторяющимися) задачами: список с включением/
// выключением, редактирование и удаление, плюс встроенный редактор с
// выбором дней недели. Полностью локально — dailyVm ничего не шлёт в Google.
Dialog {
    id: dialog

    property bool editing: false
    property string editUid: ""
    property int editMask: 0x7F

    readonly property var _weekdayLabels: ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

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

    function openList() {
        editing = false
        dialogRefresh()
        open()
    }
    function dialogRefresh() { dailyVm.refresh() }

    function startCreate() {
        editUid = ""
        titleField.text = ""
        notesField.text = ""
        timeField.text = ""
        enabledCheck.checked = true
        editMask = 0x7F
        dailyVm.clearEditorError()
        editing = true
        titleField.forceActiveFocus()
    }
    function startEdit(uid) {
        var data = dailyVm.editorDataFor(uid)
        if (!data || !data.exists) return
        editUid = uid
        titleField.text = data.title
        notesField.text = data.notes
        timeField.text = data.timeText
        enabledCheck.checked = data.enabled
        editMask = data.weekdaysMask
        dailyVm.clearEditorError()
        editing = true
        titleField.forceActiveFocus()
    }
    function submitEditor() {
        var ok = dailyVm.save(editUid, titleField.text, notesField.text,
                              enabledCheck.checked, editMask, timeField.text)
        if (ok) editing = false
    }
    function toggleDay(i) { editMask = editMask ^ (1 << i) }

    contentItem: ColumnLayout {
        spacing: Theme.spacingMd

        // ---- шапка ----
        RowLayout {
            spacing: Theme.spacingSm
            Layout.fillWidth: true
            Rectangle {
                implicitWidth: 34; implicitHeight: 34
                radius: Theme.radiusSmall
                color: Theme.accentSoft
                AppIcon { anchors.centerIn: parent; name: "refresh"; color: Theme.accent; size: 19 }
            }
            Label {
                text: dialog.editing
                      ? (dialog.editUid ? "Редактировать ежедневную" : "Новая ежедневная задача")
                      : "Ежедневные задачи"
                font.pixelSize: Theme.fontTitle
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textPrimary
                Layout.alignment: Qt.AlignVCenter
            }
            Item { Layout.fillWidth: true }
            IconButton { iconName: "close"; tip: "Закрыть"; onClicked: dialog.close() }
        }

        // =================== СПИСОК ===================
        ColumnLayout {
            visible: !dialog.editing
            Layout.fillWidth: true
            spacing: Theme.spacingSm

            Label {
                text: "Повторяются по выбранным дням недели. Отметки выполнения — на «Сегодня»."
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textMuted
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            ListView {
                id: list
                Layout.fillWidth: true
                Layout.preferredHeight: Math.min(contentHeight, 300)
                clip: true
                spacing: Theme.spacingSm
                model: dailyVm.items
                boundsBehavior: Flickable.StopAtBounds

                delegate: Rectangle {
                    required property var modelData
                    width: list.width
                    implicitHeight: 56
                    radius: Theme.radiusSmall
                    color: Theme.surfaceMuted
                    border.color: Theme.border
                    border.width: 1

                    RowLayout {
                        anchors.fill: parent
                        anchors.leftMargin: Theme.spacingMd
                        anchors.rightMargin: Theme.spacingSm
                        spacing: Theme.spacingSm

                        Switch {
                            checked: modelData.enabled
                            onToggled: dailyVm.setEnabled(modelData.uid, checked)
                        }
                        ColumnLayout {
                            spacing: 1
                            Layout.fillWidth: true
                            Label {
                                text: modelData.title
                                font.pixelSize: Theme.fontBody
                                font.family: Theme.fontFamily
                                font.weight: Font.Medium
                                color: modelData.enabled ? Theme.textPrimary : Theme.textMuted
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                            Label {
                                text: modelData.weekdaysText
                                      + (modelData.timeText.length > 0 ? " · " + modelData.timeText : "")
                                font.pixelSize: Theme.fontCaption
                                font.family: Theme.fontFamily
                                color: Theme.textMuted
                                elide: Text.ElideRight
                                Layout.fillWidth: true
                            }
                        }
                        IconButton {
                            iconName: "edit"; tip: "Изменить"
                            hoverGlyphColor: Theme.accent; hoverBg: Theme.accentSoft
                            onClicked: dialog.startEdit(modelData.uid)
                        }
                        IconButton {
                            iconName: "trash"; tip: "Удалить"
                            hoverGlyphColor: Theme.danger; hoverBg: Theme.dangerSoft
                            onClicked: dailyVm.remove(modelData.uid)
                        }
                    }
                }
            }

            EmptyState {
                visible: dailyVm.count === 0
                glyph: "🔁"
                text: "Ежедневных задач пока нет"
                hint: "Добавьте повторяющийся пункт — он будет появляться на «Сегодня»"
                Layout.fillWidth: true
                Layout.topMargin: Theme.spacingSm
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: Theme.spacingXs
                Item { Layout.fillWidth: true }
                AppButton {
                    text: "Добавить"
                    variant: "primary"
                    iconName: "plus"
                    onClicked: dialog.startCreate()
                }
            }
        }

        // =================== РЕДАКТОР ===================
        ColumnLayout {
            visible: dialog.editing
            Layout.fillWidth: true
            spacing: Theme.spacingMd

            AppTextField {
                id: titleField
                placeholderText: "Название (напр. «Зарядка»)"
                Layout.fillWidth: true
                onAccepted: dialog.submitEditor()
            }
            AppTextField {
                id: notesField
                placeholderText: "Заметка (необязательно)"
                Layout.fillWidth: true
            }

            // дни недели
            Label {
                text: "Дни недели"
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textMuted
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingXs
                Repeater {
                    model: 7
                    delegate: Rectangle {
                        required property int index
                        readonly property bool on: (dialog.editMask >> index) & 1
                        Layout.fillWidth: true
                        implicitHeight: 38
                        radius: Theme.radiusSmall
                        color: on ? Theme.accent : Theme.surface
                        border.color: on ? Theme.accent : Theme.border
                        border.width: 1
                        Behavior on color { ColorAnimation { duration: 90 } }
                        Label {
                            anchors.centerIn: parent
                            text: dialog._weekdayLabels[index]
                            font.pixelSize: Theme.fontCaption
                            font.family: Theme.fontFamily
                            font.weight: Font.DemiBold
                            color: parent.on ? Theme.textOnAccent : Theme.textSecondary
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: dialog.toggleDay(index)
                        }
                    }
                }
            }
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingSm
                AppButton { text: "Будни"; variant: "ghost"; onClicked: dialog.editMask = 0x1F }
                AppButton { text: "Выходные"; variant: "ghost"; onClicked: dialog.editMask = 0x60 }
                AppButton { text: "Каждый день"; variant: "ghost"; onClicked: dialog.editMask = 0x7F }
                Item { Layout.fillWidth: true }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingMd
                AppTextField {
                    id: timeField
                    placeholderText: "Время ЧЧ:ММ (необяз.)"
                    Layout.preferredWidth: 190
                }
                CheckBox {
                    id: enabledCheck
                    text: "Включена"
                    checked: true
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                }
                Item { Layout.fillWidth: true }
            }

            Label {
                text: dailyVm.editorError
                visible: text.length > 0
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.danger
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }

            RowLayout {
                Layout.fillWidth: true
                Layout.topMargin: Theme.spacingXs
                Item { Layout.fillWidth: true }
                AppButton { text: "Назад"; variant: "ghost"; onClicked: dialog.editing = false }
                AppButton {
                    text: dialog.editUid ? "Сохранить" : "Создать"
                    variant: "primary"
                    iconName: dialog.editUid ? "check" : "plus"
                    onClicked: dialog.submitEditor()
                }
            }
        }
    }
}
