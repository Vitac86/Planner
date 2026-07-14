import QtQuick
import QtQuick.Controls
import QtQuick.Controls.Basic as B
import QtQuick.Layouts
import QtQuick.Effects

import "../theme"

// Поле времени: клавиатурный ввод «ЧЧ:ММ» с валидатором (невалидную строку
// нельзя «зафиксировать» — на потере фокуса поле откатывается к последнему
// валидному значению) плюс выпадающий список получасов для выбора мышью.
// Финальная проверка всё равно за Python-валидацией формы.
Item {
    id: field

    property alias timeText: input.text
    property string placeholder: "ЧЧ:ММ"
    signal accepted()

    property string _lastValid: ""

    implicitWidth: 132
    implicitHeight: 38

    function openPicker() {
        var idx = _indexForText(input.text)
        input.forceActiveFocus()
        popup.open()
        timeList.currentIndex = idx
        Qt.callLater(function() {
            timeList.positionViewAtIndex(idx, ListView.Center)
        })
    }
    function _indexForText(text) {
        var m = text.match(/^([01]?\d|2[0-3]):([0-5]\d)$/)
        if (!m) return 18 // 09:00 — разумная середина рабочего дня
        return parseInt(m[1], 10) * 2 + (parseInt(m[2], 10) >= 30 ? 1 : 0)
    }
    function _moveSelection(delta) {
        if (!popup.visible) {
            openPicker()
            return
        }
        var next = Math.max(0, Math.min(uiVm.timeOptions.length - 1,
                                        timeList.currentIndex + delta))
        timeList.currentIndex = next
        timeList.positionViewAtIndex(next, ListView.Contain)
    }
    function _chooseIndex(index) {
        if (index < 0 || index >= uiVm.timeOptions.length)
            return
        input.text = uiVm.timeOptions[index]
        timeList.currentIndex = index
        popup.close()
        input.forceActiveFocus()
    }
    function _acceptCurrent() {
        _chooseIndex(timeList.currentIndex)
    }

    B.TextField {
        id: input

        anchors.fill: parent
        font.pixelSize: Theme.fontBody
        font.family: Theme.fontFamily
        color: Theme.textPrimary
        placeholderText: field.placeholder
        placeholderTextColor: Theme.textMuted
        selectionColor: Theme.accentSoft
        selectedTextColor: Theme.textPrimary
        selectByMouse: true
        hoverEnabled: true
        leftPadding: 12
        rightPadding: 34
        topPadding: 9
        bottomPadding: 9
        inputMethodHints: Qt.ImhTime

        Accessible.name: "Время"
        Accessible.description: "Введите ЧЧ:ММ или нажмите стрелку вниз для выбора"

        validator: RegularExpressionValidator {
            // «9:30» и «09:30» валидны; часы 0–23, минуты 0–59.
            regularExpression: /^([01]?\d|2[0-3]):[0-5]\d$/
        }

        onTextChanged: {
            if (acceptableInput) {
                field._lastValid = text
                if (popup.visible) {
                    timeList.currentIndex = field._indexForText(text)
                    timeList.positionViewAtIndex(timeList.currentIndex,
                                                 ListView.Contain)
                }
            }
        }
        onEditingFinished: {
            // Недопечатанное время не «застревает»: откат к валидному.
            if (text.length > 0 && !acceptableInput)
                text = field._lastValid
        }
        onAccepted: field.accepted()
        Keys.priority: Keys.BeforeItem
        Keys.onPressed: event => {
            if (event.key === Qt.Key_Down) {
                field._moveSelection(1)
                event.accepted = true
            } else if (event.key === Qt.Key_Up) {
                field._moveSelection(-1)
                event.accepted = true
            } else if (popup.visible
                       && (event.key === Qt.Key_Return
                           || event.key === Qt.Key_Enter)) {
                field._acceptCurrent()
                event.accepted = true
            } else if (popup.visible && event.key === Qt.Key_Escape) {
                popup.close()
                event.accepted = true
            }
        }

        background: Rectangle {
            radius: Theme.radiusSmall
            color: input.enabled ? Theme.surface : Theme.surfaceMuted
            border.color: (input.activeFocus || popup.visible) ? Theme.accent
                        : input.hovered ? Theme.borderStrong : Theme.border
            border.width: (input.activeFocus || popup.visible) ? 1.6 : 1
            Behavior on border.color { ColorAnimation { duration: 100 } }
        }
    }

    IconButton {
        anchors.right: parent.right
        anchors.rightMargin: 3
        anchors.verticalCenter: parent.verticalCenter
        implicitWidth: 28
        implicitHeight: 28
        iconName: "clock"
        tip: "Выбрать время"
        enabled: field.enabled
        onClicked: popup.visible ? popup.close() : field.openPicker()
    }

    Popup {
        id: popup
        y: field.height + 4
        width: field.width
        height: 232
        padding: 4
        modal: true
        focus: false // фокус остаётся в поле — можно продолжать печатать
        closePolicy: Popup.CloseOnEscape | Popup.CloseOnPressOutside
        Overlay.modal: Rectangle { color: "transparent" }

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

        contentItem: ListView {
            id: timeList
            clip: true
            model: uiVm.timeOptions
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar {}

            Accessible.name: "Варианты времени"

            delegate: Rectangle {
                required property string modelData
                required property int index

                readonly property bool active: index === timeList.currentIndex
                width: timeList.width
                height: 32
                radius: Theme.radiusSmall
                color: active ? Theme.accentSoft
                     : optionHover.hovered ? Theme.surfaceHover : "transparent"

                Label {
                    anchors.verticalCenter: parent.verticalCenter
                    anchors.left: parent.left
                    anchors.leftMargin: 12
                    text: modelData
                    font.pixelSize: Theme.fontBody
                    font.family: Theme.fontFamily
                    font.weight: active ? Font.DemiBold : Font.Normal
                    color: active ? Theme.accent : Theme.textPrimary
                }
                HoverHandler { id: optionHover; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: field._chooseIndex(index)
                }


                Accessible.role: Accessible.ListItem
                Accessible.name: modelData
                Accessible.selected: active
            }
        }
    }
}
