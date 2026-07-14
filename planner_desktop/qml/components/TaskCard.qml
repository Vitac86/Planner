import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Effects

import "../theme"

// Карточка задачи: галочка, полоса приоритета, заголовок, превью заметки,
// бейджи времени/синка и действия (редактировать/удалить). Тень лежит на
// фоновом прямоугольнике, контент — поверх и остаётся чётким.
// Используется и на «Сегодня», и в «Календаре».
Item {
    id: card

    property string uid: ""
    property string title: ""
    property string notes: ""
    property string timeLabel: ""
    property bool isAllDay: false
    property int priority: 0
    property bool completed: false
    property bool hasPendingSync: false
    property bool isLinked: false
    property bool isScheduled: false
    property bool isRecurring: false
    // Пока идёт операция (vm.busy), действия карточки выключены —
    // быстрый двойной клик не выполняет операцию дважды.
    property bool actionsEnabled: true

    property bool hovered: hoverHandler.hovered
    property bool selected: false
    readonly property bool keyboardFocusWithin: card.activeFocus
        || check.activeFocus || snoozeButton.activeFocus
        || editButton.activeFocus || deleteButton.activeFocus

    signal toggled(string uid)
    signal editRequested(string uid)
    signal deleteRequested(string uid)
    signal selectRequested(string uid)
    signal snoozeRequested(string uid)

    implicitHeight: Math.max(content.implicitHeight + 24, 60)
    activeFocusOnTab: actionsEnabled

    Accessible.role: Accessible.ListItem
    Accessible.name: title
    Accessible.description: timeLabel.length > 0 ? timeLabel : "Без даты"
    Accessible.focusable: actionsEnabled
    Accessible.selected: selected

    onActiveFocusChanged: {
        if (activeFocus)
            card.selectRequested(card.uid)
    }

    Keys.onPressed: event => {
        if (!card.actionsEnabled)
            return
        if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter) {
            card.editRequested(card.uid)
            event.accepted = true
        } else if (event.key === Qt.Key_Space) {
            card.toggled(card.uid)
            event.accepted = true
        } else if (event.key === Qt.Key_Delete) {
            card.deleteRequested(card.uid)
            event.accepted = true
        }
    }

    Rectangle {
        id: bg
        anchors.fill: parent
        radius: Theme.radiusMedium
        color: card.selected ? Theme.accentSoft
             : card.hovered ? Theme.surfaceHover : Theme.surface
        border.color: (card.selected || card.activeFocus) ? Theme.accent
                    : card.hovered ? Theme.borderStrong : Theme.border
        border.width: (card.selected || card.activeFocus) ? 1.6 : 1

        Behavior on color { ColorAnimation { duration: 110 } }
        Behavior on border.color { ColorAnimation { duration: 110 } }

        layer.enabled: true
        layer.effect: MultiEffect {
            shadowEnabled: true
            shadowColor: Theme.shadowColor
            blurMax: Theme.shadowBlurMax
            shadowBlur: card.hovered ? Theme.elevHoverBlur : Theme.elevCardBlur
            shadowVerticalOffset: card.hovered ? Theme.elevHoverY : Theme.elevCardY
            shadowOpacity: card.hovered ? Theme.elevHoverOpacity : Theme.elevCardOpacity
            autoPaddingEnabled: true
            Behavior on shadowBlur { NumberAnimation { duration: 130 } }
            Behavior on shadowOpacity { NumberAnimation { duration: 130 } }
        }
    }

    HoverHandler { id: hoverHandler }
    TapHandler {
        // Одиночный клик выделяет карточку (детали в инспекторе справа),
        // двойной — быстрый путь в редактор.
        onSingleTapped: {
            card.forceActiveFocus()
            card.selectRequested(card.uid)
        }
        onDoubleTapped: {
            if (card.actionsEnabled)
                card.editRequested(card.uid)
        }
    }

    RowLayout {
        id: content
        anchors.fill: parent
        anchors.leftMargin: 14
        anchors.rightMargin: 12
        anchors.topMargin: 12
        anchors.bottomMargin: 12
        spacing: Theme.spacingMd

        // ---- кастомная галочка выполнения ----
        Rectangle {
            id: check
            Layout.alignment: Qt.AlignVCenter
            implicitWidth: 22
            implicitHeight: 22
            radius: 7
            color: card.completed ? Theme.success : "transparent"
            border.color: card.completed ? Theme.success
                        : checkHover.hovered ? Theme.accent : Theme.borderStrong
            border.width: card.completed ? 0 : 1.6
            activeFocusOnTab: card.actionsEnabled

            Accessible.role: Accessible.CheckBox
            Accessible.name: card.completed
                             ? "Снять отметку выполнения: " + card.title
                             : "Отметить выполненной: " + card.title
            Accessible.checked: card.completed
            Accessible.checkable: true
            Accessible.focusable: card.actionsEnabled

            Keys.onPressed: event => {
                if (card.actionsEnabled
                        && (event.key === Qt.Key_Space
                            || event.key === Qt.Key_Return
                            || event.key === Qt.Key_Enter)) {
                    card.toggled(card.uid)
                    event.accepted = true
                }
            }

            Behavior on color { ColorAnimation { duration: 130 } }
            Behavior on border.color { ColorAnimation { duration: 130 } }

            AppIcon {
                anchors.centerIn: parent
                name: "check"
                color: Theme.textOnAccent
                size: 15
                strokeWidth: 2.4
                visible: card.completed
                scale: card.completed ? 1.0 : 0.4
                Behavior on scale { NumberAnimation { duration: 140; easing.type: Easing.OutBack } }
            }

            HoverHandler { id: checkHover; cursorShape: Qt.PointingHandCursor }
            TapHandler {
                enabled: card.actionsEnabled
                onTapped: {
                    check.forceActiveFocus()
                    card.toggled(card.uid)
                }
            }

            Rectangle {
                anchors.fill: parent
                anchors.margins: -3
                radius: parent.radius + 3
                color: "transparent"
                border.color: Theme.focusRing
                border.width: 2
                visible: check.activeFocus
            }
        }

        // цветная полоса приоритета
        Rectangle {
            visible: card.priority > 0
            width: 3
            radius: 2
            Layout.fillHeight: true
            Layout.topMargin: 3
            Layout.bottomMargin: 3
            color: Theme.priorityColor(card.priority)
        }

        ColumnLayout {
            spacing: 4
            Layout.fillWidth: true
            opacity: card.completed ? 0.55 : 1.0
            Behavior on opacity { NumberAnimation { duration: 150 } }

            Label {
                text: card.title
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
                font.weight: Font.Medium
                font.strikeout: card.completed
                color: card.completed ? Theme.textMuted : Theme.textPrimary
                elide: Text.ElideRight
                Layout.fillWidth: true
            }

            RowLayout {
                spacing: Theme.spacingSm
                visible: card.priority > 0 || card.notes.length > 0
                Layout.fillWidth: true

                PriorityPill { priority: card.priority }

                Label {
                    text: card.notes
                    visible: card.notes.length > 0
                    font.pixelSize: Theme.fontCaption
                    font.family: Theme.fontFamily
                    color: Theme.textMuted
                    elide: Text.ElideRight
                    maximumLineCount: 1
                    Layout.fillWidth: true
                }
            }
        }

        Badge {
            text: card.timeLabel
            fg: card.isAllDay ? Theme.success : Theme.accent
            bg: card.isAllDay ? Theme.successSoft : Theme.accentSoft
            Layout.alignment: Qt.AlignVCenter
        }

        Rectangle {
            visible: card.hasPendingSync
            Layout.alignment: Qt.AlignVCenter
            implicitHeight: 22
            implicitWidth: syncRow.implicitWidth + 16
            radius: height / 2
            color: Theme.warningSoft
            border.color: Theme.warningSoftBorder
            border.width: 1
            ToolTip.visible: syncHover.hovered
            ToolTip.text: "Операция ждёт отправки в Google Calendar (синк вручную)"

            Row {
                id: syncRow
                anchors.centerIn: parent
                spacing: 4
                AppIcon {
                    anchors.verticalCenter: parent.verticalCenter
                    name: "refresh"; size: 12; color: Theme.warningText
                }
                Label {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "Синк"
                    font.pixelSize: Theme.fontCaption - 1
                    font.family: Theme.fontFamily
                    font.weight: Font.DemiBold
                    color: Theme.warningText
                }
            }
            HoverHandler { id: syncHover }
        }

        Rectangle {
            visible: card.isLinked && !card.hasPendingSync
            Layout.alignment: Qt.AlignVCenter
            implicitWidth: 26
            implicitHeight: 22
            radius: height / 2
            color: Theme.surfacePressed
            ToolTip.visible: linkHover.hovered
            ToolTip.text: "Связано с событием Google Calendar"
            AppIcon {
                anchors.centerIn: parent
                name: "link"
                color: Theme.textSecondary
                size: 14
            }
            HoverHandler { id: linkHover }
        }

        RowLayout {
            spacing: 2
            Layout.alignment: Qt.AlignVCenter
            opacity: (card.hovered || card.keyboardFocusWithin) ? 1.0 : 0.0
            Behavior on opacity { NumberAnimation { duration: 130 } }

            IconButton {
                id: snoozeButton
                iconName: "snooze"
                tip: "Перенести…"
                hoverGlyphColor: Theme.accent
                hoverBg: Theme.accentSoft
                enabled: card.actionsEnabled
                onClicked: card.snoozeRequested(card.uid)
            }
            IconButton {
                id: editButton
                iconName: "edit"
                tip: "Редактировать"
                hoverGlyphColor: Theme.accent
                hoverBg: Theme.accentSoft
                enabled: card.actionsEnabled
                onClicked: card.editRequested(card.uid)
            }
            IconButton {
                id: deleteButton
                iconName: "trash"
                tip: "Удалить"
                hoverGlyphColor: Theme.danger
                hoverBg: Theme.dangerSoft
                enabled: card.actionsEnabled
                onClicked: card.deleteRequested(card.uid)
            }
        }
    }
}
