import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

// Sticky day headers and a separate, horizontally synchronized all-day lane.
Item {
    id: lane

    property var days: []
    property string selectedUid: ""
    property real rulerWidth: 54
    property real dayColumnWidth: 120
    property real horizontalOffset: 0
    property int maxVisibleEvents: 3
    property bool actionsEnabled: true

    signal daySelected(int dayIndex)
    signal eventSelected(string uid)
    signal eventEditRequested(string uid)
    signal dragStarted(string uid, string sourceKind)
    signal dragPointer(string uid, real x, real y, bool shift)
    signal dragFinished()
    signal dragCanceled()

    readonly property real headerHeight: 48
    readonly property int laneRows: _laneRows()
    readonly property real eventsHeight: Math.max(38, laneRows * 30 + 8)
    implicitHeight: headerHeight + eventsHeight

    function _laneRows() {
        var rows = 1
        for (var i = 0; i < days.length; ++i) {
            var count = days[i].allDayEvents ? days[i].allDayEvents.length : 0
            var shown = Math.min(count, maxVisibleEvents)
            if (count > maxVisibleEvents) shown += 1
            rows = Math.max(rows, shown)
        }
        return rows
    }

    Rectangle {
        anchors.fill: parent
        color: Theme.surface
        border.color: Theme.border
        border.width: 1
    }

    // Fixed top-left / all-day labels.
    Item {
        width: lane.rulerWidth
        height: lane.height
        Label {
            anchors.left: parent.left
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.bottomMargin: Math.max(9, lane.eventsHeight - 26)
            text: "Весь день"
            horizontalAlignment: Text.AlignRight
            rightPadding: 8
            font.pixelSize: Theme.fontCaption - 1
            font.family: Theme.fontFamily
            color: Theme.textMuted
        }
        Rectangle {
            anchors.right: parent.right
            width: 1
            height: parent.height
            color: Theme.borderStrong
        }
    }

    Item {
        x: lane.rulerWidth
        width: lane.width - lane.rulerWidth
        height: lane.height
        clip: true

        Row {
            x: -lane.horizontalOffset
            height: parent.height

            Repeater {
                model: lane.days
                delegate: Item {
                    id: dayLane
                    required property var modelData
                    required property int index
                    width: lane.dayColumnWidth
                    height: lane.height

                    Rectangle {
                        width: parent.width
                        height: lane.headerHeight
                        color: dayLane.modelData.isSelected ? Theme.accentSoft
                             : dayHeaderHover.hovered ? Theme.surfaceHover : Theme.surface
                        border.color: dayLane.modelData.isToday ? Theme.accent
                                     : dayLane.modelData.isSelected
                                       ? Theme.accentSoftBorder : Theme.border
                        border.width: dayLane.modelData.isToday ? 2 : 1

                        Column {
                            anchors.centerIn: parent
                            spacing: 1
                            Label {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: dayLane.modelData.label
                                font.pixelSize: Theme.fontCaption
                                font.family: Theme.fontFamily
                                font.weight: Font.DemiBold
                                color: dayLane.modelData.isSelected
                                       ? Theme.accent : Theme.textSecondary
                            }
                            Label {
                                anchors.horizontalCenter: parent.horizontalCenter
                                text: dayLane.modelData.shortDate
                                font.pixelSize: Theme.fontBody
                                font.family: Theme.fontFamily
                                font.weight: Font.DemiBold
                                color: dayLane.modelData.isToday
                                       ? Theme.accent : Theme.textPrimary
                            }
                        }
                        HoverHandler { id: dayHeaderHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: lane.daySelected(dayLane.modelData.dayIndex) }
                        Accessible.role: Accessible.Button
                        Accessible.name: dayLane.modelData.label + ", " + dayLane.modelData.shortDate
                    }

                    Rectangle {
                        y: lane.headerHeight
                        width: parent.width
                        height: lane.eventsHeight
                        color: dayLane.modelData.isSelected ? Theme.accentSoft : Theme.surface
                        opacity: dayLane.modelData.isSelected ? 0.45 : 1.0
                    }

                    Repeater {
                        model: Math.min(lane.maxVisibleEvents,
                                        dayLane.modelData.allDayEvents.length)
                        delegate: Rectangle {
                            id: chip
                            required property int index
                            property var eventData: dayLane.modelData.allDayEvents[index]
                            objectName: "calendarAllDayEvent_" + (eventData.uid || "")
                            x: 4
                            y: lane.headerHeight + 4 + index * 30
                            width: dayLane.width - 8
                            height: 26
                            radius: Theme.radiusSmall
                            color: lane.selectedUid === eventData.uid
                                   ? Theme.accentSoft
                                   : eventData.completed ? Theme.successSoft
                                   : Theme.priorityBgColor(eventData.priority)
                            border.color: lane.selectedUid === eventData.uid
                                          ? Theme.accent
                                          : eventData.completed ? Theme.success
                                          : Theme.priorityColor(eventData.priority)
                            border.width: lane.selectedUid === eventData.uid ? 2 : 1
                            activeFocusOnTab: lane.actionsEnabled
                            property bool dragActivated: false
                            property bool suppressClick: false

                            RowLayout {
                                anchors.fill: parent
                                anchors.leftMargin: 7
                                anchors.rightMargin: 5
                                spacing: 4
                                Label {
                                    text: chip.eventData.title
                                    font.pixelSize: Theme.fontCaption
                                    font.family: Theme.fontFamily
                                    font.weight: Font.DemiBold
                                    font.strikeout: chip.eventData.completed
                                    color: chip.eventData.completed
                                           ? Theme.success : Theme.textPrimary
                                    elide: Text.ElideRight
                                    Layout.fillWidth: true
                                    Layout.minimumWidth: 0
                                }
                                Rectangle {
                                    visible: chip.eventData.hasPendingSync
                                             || chip.eventData.hasDeadLetter
                                    implicitWidth: 7
                                    implicitHeight: 7
                                    radius: 4
                                    color: chip.eventData.hasDeadLetter
                                           ? Theme.danger : Theme.warningText
                                }
                            }
                            MouseArea {
                                id: chipMouse
                                anchors.fill: parent
                                enabled: lane.actionsEnabled
                                preventStealing: true
                                property real pressX: 0
                                property real pressY: 0
                                onPressed: mouse => {
                                    pressX = mouse.x
                                    pressY = mouse.y
                                    chip.dragActivated = false
                                    chip.suppressClick = false
                                }
                                onPositionChanged: mouse => {
                                    if (!pressed)
                                        return
                                    var dx = mouse.x - pressX
                                    var dy = mouse.y - pressY
                                    if (!chip.dragActivated
                                            && Math.sqrt(dx * dx + dy * dy)
                                               >= Qt.styleHints.startDragDistance) {
                                        chip.dragActivated = true
                                        chip.suppressClick = true
                                        lane.dragStarted(chip.eventData.uid,
                                                         "all_day_lane")
                                    }
                                    if (chip.dragActivated) {
                                        var point = chip.mapToItem(lane, mouse.x, mouse.y)
                                        lane.dragPointer(chip.eventData.uid,
                                                         point.x, point.y,
                                                         (mouse.modifiers & Qt.ShiftModifier) !== 0)
                                    }
                                }
                                onReleased: {
                                    if (chip.dragActivated) {
                                        lane.dragFinished()
                                        chip.dragActivated = false
                                        Qt.callLater(function() { chip.suppressClick = false })
                                    }
                                }
                                onCanceled: {
                                    if (chip.dragActivated)
                                        lane.dragCanceled()
                                    chip.dragActivated = false
                                    chip.suppressClick = false
                                }
                                onClicked: if (!chip.suppressClick) {
                                    chip.forceActiveFocus()
                                    lane.eventSelected(chip.eventData.uid)
                                }
                                onDoubleClicked: if (!chip.suppressClick)
                                    lane.eventEditRequested(chip.eventData.uid)
                            }
                            Keys.onReturnPressed: lane.eventEditRequested(chip.eventData.uid)
                            Keys.onEnterPressed: lane.eventEditRequested(chip.eventData.uid)
                            Keys.onSpacePressed: lane.eventSelected(chip.eventData.uid)
                            Accessible.role: Accessible.Button
                            Accessible.name: chip.eventData.title
                            Accessible.description: chip.eventData.accessibleDescription
                            Accessible.focusable: lane.actionsEnabled
                        }
                    }

                    Label {
                        visible: dayLane.modelData.allDayEvents.length > lane.maxVisibleEvents
                        x: 7
                        y: lane.headerHeight + 6 + lane.maxVisibleEvents * 30
                        width: dayLane.width - 14
                        height: 24
                        text: "ещё " + (dayLane.modelData.allDayEvents.length - lane.maxVisibleEvents)
                        font.pixelSize: Theme.fontCaption
                        font.family: Theme.fontFamily
                        font.weight: Font.DemiBold
                        color: Theme.accent
                        verticalAlignment: Text.AlignVCenter
                    }

                    Rectangle {
                        anchors.right: parent.right
                        width: 1
                        height: parent.height
                        color: Theme.borderStrong
                    }
                }
            }
        }
    }
}
