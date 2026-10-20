"""QML adapter for the shared ordinary-task undo history."""
from PySide6.QtCore import QObject, Property, Signal, Slot


class UndoViewModel(QObject):
    changed = Signal()
    actionRecorded = Signal(str)
    tasksMutated = Signal()
    toastMessage = Signal(str)
    toastError = Signal(str)

    def __init__(self, service, parent=None):
        super().__init__(parent)
        self._service = service
        service.add_listener(self._history_changed)

    def _history_changed(self, event, label):
        self.changed.emit()
        if event == "recorded":
            self.actionRecorded.emit(label)

    @Property(bool, notify=changed)
    def canUndo(self):
        return self._service.can_undo

    @Property(str, notify=changed)
    def actionLabel(self):
        return self._service.action_label

    @Property(str, notify=changed)
    def statusMessage(self):
        return self._service.status_message

    @Slot(result=bool)
    def undo(self):
        result = self._service.undo()
        if result.restored_count:
            self.tasksMutated.emit()
        if result.failed_count or not result.restored_count:
            self.toastError.emit(result.message)
        else:
            self.toastMessage.emit(result.message)
        return result.ok
