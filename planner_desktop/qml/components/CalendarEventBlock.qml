import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Visual event only. Position and overlap column values come from Python.
Rectangle {
    id: block
    objectName: "calendarEvent_" + (eventData.uid || "")

    property var eventData: ({})
    property bool selected: false
    property bool actionsEnabled: true
    property bool dragActivated: false
    property bool suppressClick: false
    signal selectedRequested(string uid)
    signal editRequested(string uid)
    signal dragStarted(string uid, string sourceKind)
    signal dragMoved(real x, real y, bool shift)
    signal dragFinished()
    signal dragCanceled()
    signal resizeStarted(string uid, string edge)
    signal resizeMoved(real x, real y, bool shift)
    signal resizeFinished()
    signal resizeCanceled()

    readonly property bool roomy: height >= 46
    readonly property bool spacious: height >= 66 && width >= 92
    readonly property int priority: eventData.priority === undefined ? 0 : eventData.priority
    readonly property bool completed: eventData.completed === true

    radius: Math.min(Theme.radiusSmall, Math.max(4, height / 5))
    color: selected ? Theme.accentSoft
         : completed ? Theme.successSoft
         : Theme.priorityBgColor(priority)
    border.color: selected ? Theme.accent
                : completed ? Theme.successSoftBorder
                : Theme.priorityColor(priority)
    border.width: selected ? 2 : 1
    opacity: completed ? 0.82 : 1.0
    clip: true
    activeFocusOnTab: actionsEnabled

    Behavior on color { ColorAnimation { duration: 100 } }
    Behavior on border.color { ColorAnimation { duration: 100 } }

    Rectangle {
        anchors.left: parent.left
        anchors.top: parent.top
        anchors.bottom: parent.bottom
        width: 4
        color: block.completed ? Theme.success : Theme.priorityColor(block.priority)
    }

    ColumnLayout {
        anchors.fill: parent
        anchors.leftMargin: 9
        anchors.rightMargin: 5
        anchors.topMargin: block.roomy ? 5 : 2
        anchors.bottomMargin: 3
        spacing: 1

        RowLayout {
            Layout.fillWidth: true
            Layout.minimumWidth: 0
            spacing: 3

            Label {
                text: block.eventData.title || "Без названия"
                font.pixelSize: block.roomy ? Theme.fontCaption + 1 : Theme.fontCaption
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: block.completed ? Theme.success : Theme.textPrimary
                font.strikeout: block.completed
                elide: Text.ElideRight
                Layout.fillWidth: true
                Layout.minimumWidth: 0
            }
            Rectangle {
                visible: block.eventData.hasPendingSync === true
                         || block.eventData.hasDeadLetter === true
                implicitWidth: 8
                implicitHeight: 8
                radius: 4
                color: block.eventData.hasDeadLetter === true
                       ? Theme.danger : Theme.warningText
                ToolTip.visible: badgeHover.hovered
                ToolTip.text: block.eventData.hasDeadLetter === true
                              ? "Ошибка синхронизации"
                              : "Ожидает ручной синхронизации"
                HoverHandler { id: badgeHover }
            }
        }

        Label {
            visible: block.roomy
            text: block.eventData.gridTimeLabel || block.eventData.timeLabel || ""
            font.pixelSize: Theme.fontCaption - 1
            font.family: Theme.fontFamily
            color: Theme.textSecondary
            elide: Text.ElideRight
            Layout.fillWidth: true
            Layout.minimumWidth: 0
        }

        RowLayout {
            visible: block.spacious
            Layout.fillWidth: true
            spacing: 3
            AppIcon {
                name: "flag"
                size: 11
                color: Theme.priorityColor(block.priority)
            }
            Label {
                text: block.eventData.priorityLabel || Theme.priorityName(block.priority)
                font.pixelSize: Theme.fontCaption - 2
                font.family: Theme.fontFamily
                color: Theme.textMuted
                elide: Text.ElideRight
                Layout.fillWidth: true
            }
            AppIcon {
                visible: block.completed
                name: "check"
                size: 11
                color: Theme.success
            }
        }
    }

    HoverHandler {
        id: eventHover
        cursorShape: block.actionsEnabled ? Qt.PointingHandCursor : Qt.ArrowCursor
    }
    MouseArea {
        id: bodyMouse
        anchors.fill: parent
        anchors.bottomMargin: 8
        enabled: block.actionsEnabled
        acceptedButtons: Qt.LeftButton
        hoverEnabled: true
        preventStealing: true
        property real pressX: 0
        property real pressY: 0
        onPressed: mouse => {
            pressX = mouse.x
            pressY = mouse.y
            block.dragActivated = false
            block.suppressClick = false
        }
        onPositionChanged: mouse => {
            if (!pressed)
                return
            var dx = mouse.x - pressX
            var dy = mouse.y - pressY
            if (!block.dragActivated
                    && Math.sqrt(dx * dx + dy * dy) >= Qt.styleHints.startDragDistance) {
                block.dragActivated = true
                block.suppressClick = true
                block.dragStarted(block.eventData.uid, "timed_grid")
            }
            if (block.dragActivated)
                block.dragMoved(mouse.x, mouse.y,
                                (mouse.modifiers & Qt.ShiftModifier) !== 0)
        }
        onReleased: {
            if (block.dragActivated) {
                block.dragFinished()
                block.dragActivated = false
                Qt.callLater(function() { block.suppressClick = false })
            }
        }
        onCanceled: {
            if (block.dragActivated)
                block.dragCanceled()
            block.dragActivated = false
            block.suppressClick = false
        }
        onClicked: if (!block.suppressClick) {
            block.forceActiveFocus()
            block.selectedRequested(block.eventData.uid)
        }
        onDoubleClicked: if (!block.suppressClick)
            block.editRequested(block.eventData.uid)
    }

    CalendarResizeHandle {
        id: resizeHandle
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        taskUid: block.eventData.uid || ""
        enabled: block.actionsEnabled
        highlighted: block.selected || eventHover.hovered || block.activeFocus
        onResizeStarted: (uid, edge) => block.resizeStarted(uid, edge)
        onResizeMoved: (x, y, shift) => {
            var point = resizeHandle.mapToItem(block, x, y)
            block.resizeMoved(point.x, point.y, shift)
        }
        onResizeFinished: block.resizeFinished()
        onResizeCanceled: block.resizeCanceled()
    }
    Keys.onReturnPressed: block.editRequested(block.eventData.uid)
    Keys.onEnterPressed: block.editRequested(block.eventData.uid)
    Keys.onSpacePressed: block.selectedRequested(block.eventData.uid)

    Rectangle {
        anchors.fill: parent
        anchors.margins: -2
        radius: parent.radius + 2
        color: "transparent"
        border.color: Theme.focusRing
        border.width: 2
        visible: block.activeFocus
        z: 20
    }

    Accessible.role: Accessible.Button
    Accessible.name: eventData.title || "Событие календаря"
    Accessible.description: eventData.accessibleDescription || ""
    Accessible.focusable: actionsEnabled
}
