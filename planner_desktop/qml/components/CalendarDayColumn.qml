import QtQuick

import "../theme"

// One timed day column. Geometry ratios and overlap columns are ViewModel data.
Item {
    id: column

    property var dayData: ({ timedEvents: [] })
    property var currentTimeData: ({ visible: false, dayIndex: -1 })
    property string selectedUid: ""
    property int visibleStartHour: 6
    property int visibleEndHour: 23
    property real hourHeight: 64
    property bool actionsEnabled: true

    signal daySelected(int dayIndex)
    signal eventSelected(string uid)
    signal eventEditRequested(string uid)
    signal emptyTimeSelected(string dateText, int minute)

    readonly property real gridHeight: (visibleEndHour - visibleStartHour) * hourHeight
    height: gridHeight

    Rectangle {
        anchors.fill: parent
        color: column.dayData.isSelected ? Theme.accentSoft : Theme.surface
        opacity: column.dayData.isSelected ? 0.42 : 0.92
    }

    // Full-hour and half-hour rules.
    Repeater {
        model: (column.visibleEndHour - column.visibleStartHour) * 2 + 1
        delegate: Rectangle {
            required property int index
            x: 0
            y: index * column.hourHeight / 2
            width: column.width
            height: 1
            color: index % 2 === 0 ? Theme.borderStrong : Theme.border
            opacity: index % 2 === 0 ? 0.9 : 0.55
        }
    }
    Rectangle {
        anchors.right: parent.right
        width: 1
        height: parent.height
        color: Theme.borderStrong
    }

    // Empty-space selection only; it never creates a task in Phase 2.1.
    MouseArea {
        anchors.fill: parent
        z: 1
        onClicked: mouse => {
            column.forceActiveFocus()
            column.daySelected(column.dayData.dayIndex)
            var raw = column.visibleStartHour * 60
                      + mouse.y / column.height
                        * (column.visibleEndHour - column.visibleStartHour) * 60
            var snapped = Math.round(raw / 15) * 15
            column.emptyTimeSelected(column.dayData.dateText, snapped)
        }
    }

    Repeater {
        model: column.dayData.timedEvents || []
        delegate: CalendarEventBlock {
            id: eventBlock
            required property var modelData

            eventData: modelData
            selected: column.selectedUid === modelData.uid
            actionsEnabled: column.actionsEnabled
            readonly property real eventGap: 3
            readonly property int providedColumns: Math.max(1, modelData.overlapColumnCount)
            readonly property real slotWidth:
                (column.width - eventGap * (providedColumns + 1)) / providedColumns
            x: eventGap + modelData.overlapColumnIndex * (slotWidth + eventGap)
            y: modelData.topRatio * column.gridHeight + 1
            width: Math.max(22, slotWidth)
            height: Math.max(18, modelData.heightRatio * column.gridHeight - 2)
            z: selected || activeFocus ? 5 : 3
            onSelectedRequested: uid => column.eventSelected(uid)
            onEditRequested: uid => column.eventEditRequested(uid)
        }
    }

    CurrentTimeIndicator {
        x: 0
        width: column.width
        y: column.currentTimeData.topRatio * column.gridHeight
        indicatorVisible: column.currentTimeData.visible === true
                          && column.currentTimeData.dayIndex === column.dayData.dayIndex
        timeLabel: column.currentTimeData.timeLabel || ""
    }
}
