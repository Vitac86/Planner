import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

import "../theme"

Panel {
    id: panel
    property var tasks: []
    property string selectedUid: ""
    property var selectedUids: []
    property bool actionsEnabled: true
    property bool persistent: false

    signal taskSelected(string uid)
    signal taskSelectionRequested(string uid, bool ctrl, bool shift)
    signal dragStarted(string uid, string sourceKind)
    signal dragPointer(string uid, real x, real y, bool shift)
    signal dragFinished()
    signal dragCanceled()

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingMd
        spacing: Theme.spacingSm

        RowLayout {
            Layout.fillWidth: true
            Label {
                text: "Без даты"
                font.pixelSize: Theme.fontSubtitle
                font.family: Theme.fontFamily
                font.weight: Font.DemiBold
                color: Theme.textPrimary
                Layout.fillWidth: true
            }
            Badge { text: String(panel.tasks.length) }
        }
        Label {
            text: "Перетащите задачу в слот или на весь день"
            font.pixelSize: Theme.fontCaption
            font.family: Theme.fontFamily
            color: Theme.textMuted
            wrapMode: Text.WordWrap
            Layout.fillWidth: true
        }
        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

        ListView {
            id: undatedList
            objectName: "calendarUndatedTaskList"
            Layout.fillWidth: true
            Layout.fillHeight: true
            Layout.minimumHeight: 120
            clip: true
            spacing: Theme.spacingSm
            model: panel.tasks
            boundsBehavior: Flickable.StopAtBounds
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }
            delegate: UndatedTaskDragCard {
                id: taskCard
                required property var modelData
                width: undatedList.width
                task: modelData
                selected: panel.selectedUid === modelData.uid
                          || panel.selectedUids.indexOf(modelData.uid) >= 0
                actionsEnabled: panel.actionsEnabled
                onSelectedRequested: uid => panel.taskSelected(uid)
                onSelectionRequested: (uid, ctrl, shift) =>
                    panel.taskSelectionRequested(uid, ctrl, shift)
                onDragStarted: (uid, sourceKind) => panel.dragStarted(uid, sourceKind)
                onDragMoved: (x, y, shift) => {
                    var point = taskCard.mapToItem(panel, x, y)
                    panel.dragPointer(modelData.uid, point.x, point.y, shift)
                }
                onDragFinished: panel.dragFinished()
                onDragCanceled: panel.dragCanceled()
            }
        }

        EmptyState {
            visible: panel.tasks.length === 0
            Layout.fillWidth: true
            Layout.fillHeight: true
            iconName: "calendar"
            text: "Задач без даты нет"
            hint: "Новые недатированные задачи появятся здесь"
        }
    }

    Accessible.role: Accessible.Pane
    Accessible.name: "Панель задач без даты"
}
