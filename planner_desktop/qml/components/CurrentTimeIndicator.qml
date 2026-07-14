import QtQuick
import QtQuick.Controls

import "../theme"

// Current-time line for the one day column containing today.
Item {
    id: indicator

    property bool indicatorVisible: false
    property string timeLabel: ""

    visible: indicatorVisible
    height: 2
    z: 8

    Rectangle {
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        height: 2
        color: Theme.danger
    }
    Rectangle {
        anchors.left: parent.left
        anchors.verticalCenter: parent.verticalCenter
        width: 8
        height: 8
        radius: 4
        color: Theme.danger
    }
    Label {
        visible: indicator.width >= 112
        anchors.right: parent.right
        anchors.bottom: parent.top
        anchors.bottomMargin: 2
        text: indicator.timeLabel
        font.pixelSize: Theme.fontCaption - 1
        font.family: Theme.fontFamily
        font.weight: Font.DemiBold
        color: Theme.danger
        background: Rectangle { color: Theme.surface; radius: 3 }
    }

    Accessible.ignored: true
}
