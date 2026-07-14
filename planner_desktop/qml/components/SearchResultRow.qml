import QtQuick
import QtQuick.Layouts

Item {
    id: row
    property var task: ({})
    property bool selected: false
    property bool actionsEnabled: true
    signal selectionRequested(string uid, bool ctrl, bool shift)
    signal openRequested(string uid)
    signal toggleRequested(string uid)
    signal deleteRequested(string uid)
    signal duplicateRequested(string uid)
    signal tagClicked(string name)

    implicitHeight: card.implicitHeight

    TaskCard {
        id: card
        anchors.fill: parent
        uid: row.task.uid || ""
        title: row.task.title || ""
        notes: row.task.notes || ""
        timeLabel: row.task.timeLabel || ""
        isAllDay: !!row.task.isAllDay
        priority: row.task.priority || 0
        completed: !!row.task.completed
        hasPendingSync: !!row.task.hasPendingSync
        isLinked: !!row.task.isLinked
        isScheduled: !!row.task.isScheduled
        isRecurring: !!row.task.isRecurring
        tags: row.task.tags || []
        tagOverflow: row.task.tagOverflow || 0
        selected: row.selected
        actionsEnabled: row.actionsEnabled
        onSelectionRequested: (uid, ctrl, shift) => row.selectionRequested(uid, ctrl, shift)
        onToggled: uid => row.toggleRequested(uid)
        onEditRequested: uid => row.openRequested(uid)
        onDeleteRequested: uid => row.deleteRequested(uid)
        onDuplicateRequested: uid => row.duplicateRequested(uid)
        onTagClicked: name => row.tagClicked(name)
    }
}
