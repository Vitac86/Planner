import QtQuick
import QtQuick.Controls

import "../theme"

// Reusable scrollable calendar grid. It only scales normalized ViewModel data.
Item {
    id: grid
    objectName: "calendarTimeGrid"

    property var days: []
    property var currentTimeData: ({ visible: false, dayIndex: -1 })
    property string selectedUid: ""
    property int visibleStartHour: 6
    property int visibleEndHour: 23
    property int initialScrollMinute: 480
    property bool compact: false
    property bool actionsEnabled: true
    property real selectedMinute: -1

    signal daySelected(int dayIndex)
    signal eventSelected(string uid)
    signal eventEditRequested(string uid)
    signal emptyTimeSelected(string dateText, int minute)

    readonly property real rulerWidth: compact ? 50 : 56
    readonly property real hourHeight: compact ? 60 : 66
    readonly property real minDayWidth: compact ? 250 : 112
    readonly property real columnsViewportWidth: Math.max(0, width - rulerWidth)
    readonly property real columnsContentWidth:
        Math.max(columnsViewportWidth, days.length * minDayWidth)
    readonly property real dayColumnWidth:
        days.length > 0 ? columnsContentWidth / days.length : columnsContentWidth
    readonly property bool gridFocused: activeFocus || timedFlick.activeFocus
    property bool initialScrollApplied: false

    activeFocusOnTab: true
    Accessible.role: Accessible.Pane
    Accessible.name: "Почасовая сетка календаря"
    Accessible.focusable: true

    function scrollToMinute(minute) {
        var y = (minute - visibleStartHour * 60) * hourHeight / 60
        timedFlick.contentY = Math.max(0, Math.min(
            timedFlick.contentHeight - timedFlick.height,
            y - Math.min(110, timedFlick.height * 0.22)))
    }

    function ensureInitialScroll(force) {
        if (initialScrollApplied && !force)
            return
        initialScrollApplied = true
        Qt.callLater(function() { grid.scrollToMinute(grid.initialScrollMinute) })
    }

    onVisibleChanged: if (visible) ensureInitialScroll(false)
    Component.onCompleted: ensureInitialScroll(false)

    CalendarAllDayLane {
        id: allDayLane
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: parent.top
        days: grid.days
        selectedUid: grid.selectedUid
        rulerWidth: grid.rulerWidth
        dayColumnWidth: grid.dayColumnWidth
        horizontalOffset: timedFlick.contentX
        actionsEnabled: grid.actionsEnabled
        onDaySelected: index => grid.daySelected(index)
        onEventSelected: uid => grid.eventSelected(uid)
        onEventEditRequested: uid => grid.eventEditRequested(uid)
    }

    Item {
        id: timedViewport
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.top: allDayLane.bottom
        anchors.bottom: parent.bottom
        clip: true

        Rectangle { anchors.fill: parent; color: Theme.surface }

        // Ruler is vertically synchronized but horizontally fixed.
        Item {
            id: rulerClip
            width: grid.rulerWidth
            anchors.left: parent.left
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            clip: true
            z: 10
            Rectangle { anchors.fill: parent; color: Theme.surface }
            CalendarTimeRuler {
                width: parent.width
                y: -timedFlick.contentY
                visibleStartHour: grid.visibleStartHour
                visibleEndHour: grid.visibleEndHour
                hourHeight: grid.hourHeight
            }
            Rectangle {
                anchors.right: parent.right
                width: 1
                height: parent.height
                color: Theme.borderStrong
            }
        }

        Flickable {
            id: timedFlick
            objectName: "calendarTimedFlick"
            anchors.left: parent.left
            anchors.leftMargin: grid.rulerWidth
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.bottom: parent.bottom
            contentWidth: grid.columnsContentWidth
            contentHeight: (grid.visibleEndHour - grid.visibleStartHour) * grid.hourHeight
            clip: true
            boundsBehavior: Flickable.StopAtBounds
            flickableDirection: Flickable.HorizontalAndVerticalFlick
            activeFocusOnTab: true
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            ScrollBar.horizontal: ScrollBar { policy: ScrollBar.AsNeeded }

            Row {
                width: grid.columnsContentWidth
                height: timedFlick.contentHeight

                Repeater {
                    model: grid.days
                    delegate: CalendarDayColumn {
                        required property var modelData
                        width: grid.dayColumnWidth
                        dayData: modelData
                        currentTimeData: grid.currentTimeData
                        selectedUid: grid.selectedUid
                        visibleStartHour: grid.visibleStartHour
                        visibleEndHour: grid.visibleEndHour
                        hourHeight: grid.hourHeight
                        actionsEnabled: grid.actionsEnabled
                        onDaySelected: index => {
                            grid.forceActiveFocus()
                            grid.daySelected(index)
                        }
                        onEventSelected: uid => {
                            grid.forceActiveFocus()
                            grid.eventSelected(uid)
                        }
                        onEventEditRequested: uid => grid.eventEditRequested(uid)
                        onEmptyTimeSelected: (dateText, minute) => {
                            grid.selectedMinute = minute
                            grid.emptyTimeSelected(dateText, minute)
                        }
                    }
                }
            }
        }
    }

    Rectangle {
        anchors.fill: parent
        anchors.margins: -2
        radius: Theme.radiusMedium
        color: "transparent"
        border.color: Theme.focusRing
        border.width: 2
        visible: grid.activeFocus
        z: 30
    }
}
