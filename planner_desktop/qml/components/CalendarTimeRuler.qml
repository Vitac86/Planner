import QtQuick
import QtQuick.Controls

import "../theme"

// Fixed ruler; CalendarTimeGrid offsets it vertically with the scroll area.
Item {
    id: ruler

    property int visibleStartHour: 6
    property int visibleEndHour: 23
    property real hourHeight: 64

    implicitWidth: 54
    implicitHeight: (visibleEndHour - visibleStartHour) * hourHeight

    Repeater {
        model: ruler.visibleEndHour - ruler.visibleStartHour + 1
        delegate: Label {
            required property int index
            width: ruler.width - 8
            height: 20
            y: Math.max(0, Math.min(ruler.height - height,
                                    index * ruler.hourHeight - height / 2))
            text: String(ruler.visibleStartHour + index).padStart(2, "0") + ":00"
            horizontalAlignment: Text.AlignRight
            verticalAlignment: Text.AlignVCenter
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            font.weight: Font.Medium
            color: Theme.textMuted
        }
    }

    Accessible.ignored: true
}
