import QtQuick
import QtQuick.Controls

import "../theme"

Item {
    id: handle
    property string taskUid: ""
    property bool enabled: true
    property bool highlighted: false
    signal resizeStarted(string uid, string edge)
    signal resizeMoved(real x, real y, bool shift)
    signal resizeFinished()
    signal resizeCanceled()

    height: 10
    visible: enabled && highlighted

    Rectangle {
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.bottom: parent.bottom
        anchors.bottomMargin: 2
        width: Math.max(24, Math.min(parent.width - 10, 44))
        height: 3
        radius: 2
        color: Theme.accent
    }

    MouseArea {
        anchors.fill: parent
        enabled: handle.enabled
        cursorShape: Qt.SizeVerCursor
        preventStealing: true
        onPressed: mouse => {
            handle.resizeStarted(handle.taskUid, "end")
            handle.resizeMoved(mouse.x, mouse.y,
                               (mouse.modifiers & Qt.ShiftModifier) !== 0)
        }
        onPositionChanged: mouse => {
            if (pressed)
                handle.resizeMoved(mouse.x, mouse.y,
                                   (mouse.modifiers & Qt.ShiftModifier) !== 0)
        }
        onReleased: handle.resizeFinished()
        onCanceled: handle.resizeCanceled()
    }

    Accessible.role: Accessible.Slider
    Accessible.name: "Изменить длительность задачи"
    Accessible.description: "Перетаскивайте вертикально; Alt+Shift+Вверх или Вниз — с клавиатуры"
}
