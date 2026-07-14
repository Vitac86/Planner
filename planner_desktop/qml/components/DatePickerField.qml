import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as B
import QtQuick.Layouts
import QtQuick.Effects

import "../theme"

// Поле выбора даты. Пользователь видит русскую подпись («вт, 14 июля 2026»)
// и месячную сетку с русскими днями/месяцами — сырые строки «ГГГГ-ММ-ДД»
// наружу не показываются. Значение живёт в dateText (ISO, "" = не выбрано):
// финальная валидация всё равно остаётся за Python.
//
// Клавиатура: Tab доводит фокус, Enter/Space/↓ открывают сетку,
// Esc закрывает попап (стандартный closePolicy).
Item {
    id: field

    property string dateText: ""
    property string placeholder: "Выберите дату"

    readonly property bool hasDate: dateText.length > 0
    readonly property bool pickerOpen: popup.visible
    property var _cursorDate: new Date()

    implicitWidth: 230
    implicitHeight: 38

    // ---- разбор ISO без new Date(iso): JS парсит ISO как UTC и день «уезжает» ----
    function _parts() {
        if (hasDate) {
            var p = dateText.split("-")
            if (p.length === 3)
                return { y: parseInt(p[0], 10), m: parseInt(p[1], 10) - 1,
                         d: parseInt(p[2], 10) }
        }
        var now = new Date()
        return { y: now.getFullYear(), m: now.getMonth(), d: -1 }
    }
    function _pad(v) { return (v < 10 ? "0" : "") + v }
    function _sameDay(a, b) {
        return a && b
            && a.getFullYear() === b.getFullYear()
            && a.getMonth() === b.getMonth()
            && a.getDate() === b.getDate()
    }
    function _moveCursor(days) {
        var d = new Date(_cursorDate.getFullYear(), _cursorDate.getMonth(),
                         _cursorDate.getDate())
        d.setDate(d.getDate() + days)
        _cursorDate = d
        grid.year = d.getFullYear()
        grid.month = d.getMonth()
    }

    function openPicker() {
        var parts = _parts()
        grid.year = parts.y
        grid.month = parts.m
        _cursorDate = parts.d > 0
            ? new Date(parts.y, parts.m, parts.d)
            : new Date()
        popup.open()
        Qt.callLater(function() { grid.forceActiveFocus() })
    }
    function _select(jsDate) {
        _cursorDate = jsDate
        dateText = jsDate.getFullYear() + "-" + _pad(jsDate.getMonth() + 1)
                 + "-" + _pad(jsDate.getDate())
        popup.close()
    }

    activeFocusOnTab: true
    Accessible.role: Accessible.Button
    Accessible.name: field.hasDate
                     ? "Дата: " + uiVm.humanDate(field.dateText)
                     : field.placeholder
    Accessible.description: "Открыть календарь выбора даты"
    Accessible.focusable: field.enabled
    Keys.onReturnPressed: openPicker()
    Keys.onEnterPressed: openPicker()
    Keys.onSpacePressed: openPicker()
    Keys.onDownPressed: openPicker()

    Rectangle {
        anchors.fill: parent
        radius: Theme.radiusSmall
        color: field.enabled ? Theme.surface : Theme.surfaceMuted
        border.color: (field.activeFocus || popup.visible) ? Theme.accent
                    : hoverHandler.hovered ? Theme.borderStrong : Theme.border
        border.width: (field.activeFocus || popup.visible) ? 1.6 : 1
        Behavior on border.color { ColorAnimation { duration: 100 } }

        RowLayout {
            anchors.fill: parent
            anchors.leftMargin: 12
            anchors.rightMargin: 10
            spacing: 6

            AppIcon {
                name: "calendar"
                size: 16
                color: field.hasDate ? Theme.accent : Theme.textMuted
            }
            Label {
                text: field.hasDate ? uiVm.humanDate(field.dateText)
                                    : field.placeholder
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
                color: field.hasDate ? Theme.textPrimary : Theme.textMuted
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            AppIcon {
                name: popup.visible ? "chevron-up" : "chevron-down"
                size: 13
                color: Theme.textMuted
            }
        }
    }

    HoverHandler {
        id: hoverHandler
        cursorShape: Qt.PointingHandCursor
        enabled: field.enabled
    }
    TapHandler {
        enabled: field.enabled
        onTapped: {
            field.forceActiveFocus()
            if (popup.visible) popup.close()
            else field.openPicker()
        }
    }

    Popup {
        id: popup
        y: field.height + 4
        width: 300
        padding: Theme.spacingMd
        modal: true
        focus: true
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        Overlay.modal: Rectangle { color: "transparent" }
        onClosed: field.forceActiveFocus()

        background: Rectangle {
            radius: Theme.radiusMedium
            color: Theme.surface
            border.color: Theme.border
            border.width: 1
            layer.enabled: true
            layer.effect: MultiEffect {
                shadowEnabled: true
                shadowColor: Theme.shadowColor
                blurMax: Theme.shadowBlurMax
                shadowBlur: Theme.elevDialogBlur
                shadowVerticalOffset: 10
                shadowOpacity: 0.22
                autoPaddingEnabled: true
            }
        }

        contentItem: ColumnLayout {
            spacing: Theme.spacingSm

            // ---- заголовок месяца и навигация ----
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingXs

                IconButton {
                    iconName: "chevron-left"
                    tip: "Предыдущий месяц"
                    onClicked: {
                        if (grid.month === 0) { grid.month = 11; grid.year-- }
                        else grid.month--
                    }
                }
                Label {
                    text: Theme.monthName(grid.month) + " " + grid.year
                    horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: Theme.fontSubtitle
                    font.family: Theme.fontFamily
                    font.weight: Font.DemiBold
                    color: Theme.textPrimary
                    Layout.fillWidth: true
                }
                IconButton {
                    iconName: "chevron-right"
                    tip: "Следующий месяц"
                    onClicked: {
                        if (grid.month === 11) { grid.month = 0; grid.year++ }
                        else grid.month++
                    }
                }
            }

            // ---- дни недели (русские, неделя с понедельника) ----
            B.DayOfWeekRow {
                locale: Qt.locale("ru_RU")
                Layout.fillWidth: true
                delegate: Label {
                    required property var model
                    text: model.shortName
                    horizontalAlignment: Text.AlignHCenter
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    font.weight: Font.DemiBold
                    color: Theme.textMuted
                }
            }

            // ---- месячная сетка ----
            B.MonthGrid {
                id: grid
                locale: Qt.locale("ru_RU")
                Layout.fillWidth: true
                spacing: 0
                activeFocusOnTab: true

                Accessible.name: "Календарь. Стрелки меняют день, Enter выбирает дату"
                Accessible.focusable: true

                Keys.onPressed: event => {
                    if (event.key === Qt.Key_Left) {
                        field._moveCursor(-1)
                        event.accepted = true
                    } else if (event.key === Qt.Key_Right) {
                        field._moveCursor(1)
                        event.accepted = true
                    } else if (event.key === Qt.Key_Up) {
                        field._moveCursor(-7)
                        event.accepted = true
                    } else if (event.key === Qt.Key_Down) {
                        field._moveCursor(7)
                        event.accepted = true
                    } else if (event.key === Qt.Key_Return
                               || event.key === Qt.Key_Enter
                               || event.key === Qt.Key_Space) {
                        field._select(field._cursorDate)
                        event.accepted = true
                    } else if (event.key === Qt.Key_Escape) {
                        popup.close()
                        event.accepted = true
                    }
                }

                readonly property var sel: field._parts()

                delegate: Item {
                    id: cell
                    required property var model

                    readonly property bool inMonth: model.month === grid.month
                    readonly property bool isSelected: field.hasDate
                        && model.date.getFullYear() === grid.sel.y
                        && model.date.getMonth() === grid.sel.m
                        && model.date.getDate() === grid.sel.d
                    readonly property bool isCursor:
                        field._sameDay(model.date, field._cursorDate)

                    implicitWidth: 36
                    implicitHeight: 32

                    Rectangle {
                        anchors.centerIn: parent
                        width: 30
                        height: 30
                        radius: 8
                        color: cell.isSelected ? Theme.accent
                             : cell.isCursor && grid.activeFocus ? Theme.accentSoft
                             : cellHover.hovered ? Theme.surfaceHover : "transparent"
                        border.color: cell.isCursor && grid.activeFocus && !cell.isSelected
                                      ? Theme.focusRing
                                      : model.today && !cell.isSelected
                                        ? Theme.accent : "transparent"
                        border.width: cell.isCursor && grid.activeFocus ? 2 : 1.4

                        Label {
                            anchors.centerIn: parent
                            text: model.day
                            font.pixelSize: Theme.fontBody - 1
                            font.family: Theme.fontFamily
                            font.weight: (cell.isSelected || model.today)
                                         ? Font.DemiBold : Font.Normal
                            color: cell.isSelected ? Theme.textOnAccent
                                 : !cell.inMonth ? Theme.textMuted
                                 : model.today ? Theme.accent : Theme.textPrimary
                            opacity: cell.inMonth ? 1.0 : 0.45
                        }
                    }
                    HoverHandler { id: cellHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler {
                        onTapped: {
                            field._cursorDate = cell.model.date
                            field._select(cell.model.date)
                        }
                    }
                }
            }

            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

            // ---- быстрый прыжок ----
            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingSm

                AppButton {
                    text: "Сегодня"
                    variant: "ghost"
                    onClicked: field._select(new Date())
                }
                AppButton {
                    text: "Завтра"
                    variant: "ghost"
                    onClicked: {
                        var d = new Date()
                        d.setDate(d.getDate() + 1)
                        field._select(d)
                    }
                }
                Item { Layout.fillWidth: true }
            }
        }
    }
}
