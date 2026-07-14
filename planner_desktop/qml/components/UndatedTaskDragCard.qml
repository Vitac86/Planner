import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

Rectangle {
    id: card
    property var task: ({})
    property bool selected: false
    property bool actionsEnabled: true
    property bool dragActivated: false
    property bool suppressClick: false

    signal selectedRequested(string uid)
    signal dragStarted(string uid, string sourceKind)
    signal dragMoved(real x, real y, bool shift)
    signal dragFinished()
    signal dragCanceled()

    implicitHeight: task.notes && task.notes.length > 0 ? 82 : 62
    radius: Theme.radiusMedium
    color: selected ? Theme.accentSoft : cardHover.hovered ? Theme.surfaceHover : Theme.surface
    border.color: selected ? Theme.accent : Theme.border
    border.width: selected ? 2 : 1
    activeFocusOnTab: actionsEnabled

    RowLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingSm
        spacing: Theme.spacingSm

        Rectangle {
            Layout.preferredWidth: 5
            Layout.fillHeight: true
            radius: 3
            color: Theme.priorityColor(card.task.priority || 0)
        }
        ColumnLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: 3
            Label {
                text: card.task.title || "Без названия"
                font.pixelSize: Theme.fontBody
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textPrimary
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            Label {
                visible: card.task.notes && card.task.notes.length > 0
                text: card.task.notes || ""
                font.pixelSize: Theme.fontCaption
                font.family: Theme.fontFamily
                color: Theme.textSecondary
                elide: Text.ElideRight
                maximumLineCount: 2
                wrapMode: Text.WordWrap
                Layout.fillWidth: true
            }
            Label {
                text: card.task.priorityLabel || "Обычный"
                font.pixelSize: Theme.fontCaption - 1
                font.family: Theme.fontFamily
                color: Theme.priorityColor(card.task.priority || 0)
            }
        }
        AppIcon {
            name: "drag"
            size: 18
            color: Theme.textMuted
            Accessible.name: "Перетащить задачу в календарь"
        }
    }

    HoverHandler { id: cardHover; cursorShape: Qt.OpenHandCursor }
    MouseArea {
        anchors.fill: parent
        enabled: card.actionsEnabled
        preventStealing: true
        cursorShape: pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor
        property real pressX: 0
        property real pressY: 0
        onPressed: mouse => {
            pressX = mouse.x
            pressY = mouse.y
            card.dragActivated = false
            card.suppressClick = false
        }
        onPositionChanged: mouse => {
            if (!pressed)
                return
            var dx = mouse.x - pressX
            var dy = mouse.y - pressY
            if (!card.dragActivated
                    && Math.sqrt(dx * dx + dy * dy) >= Qt.styleHints.startDragDistance) {
                card.dragActivated = true
                card.suppressClick = true
                card.dragStarted(card.task.uid, "undated_panel")
            }
            if (card.dragActivated)
                card.dragMoved(mouse.x, mouse.y,
                               (mouse.modifiers & Qt.ShiftModifier) !== 0)
        }
        onReleased: {
            if (card.dragActivated) {
                card.dragFinished()
                card.dragActivated = false
                Qt.callLater(function() { card.suppressClick = false })
            }
        }
        onCanceled: {
            if (card.dragActivated)
                card.dragCanceled()
            card.dragActivated = false
            card.suppressClick = false
        }
        onClicked: if (!card.suppressClick) {
            card.forceActiveFocus()
            card.selectedRequested(card.task.uid)
        }
    }

    Keys.onReturnPressed: card.selectedRequested(card.task.uid)
    Keys.onEnterPressed: card.selectedRequested(card.task.uid)
    Keys.onSpacePressed: card.selectedRequested(card.task.uid)
    Accessible.role: Accessible.Button
    Accessible.name: (task.title || "Задача без даты") + ", без даты"
    Accessible.description: "Enter — открыть; перетащите в календарь или используйте редактор"
    Accessible.focusable: actionsEnabled
}
