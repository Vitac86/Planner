"""Debounced form drafts, separate from saving tasks or queuing Calendar writes."""
from __future__ import annotations

import logging
from typing import Callable

from PySide6.QtCore import QObject, Property, QTimer, Signal, Slot

from planner_desktop.storage.draft_store import DraftStore

logger = logging.getLogger(__name__)

_CONTEXTS = {"quick-add", "editor:new", "editor:task", "editor:occurrence"}
_EDIT_CONTEXTS = {"editor:task", "editor:occurrence"}
_FIELDS = {
    "title", "notes", "priority", "mode", "dateText", "timeText",
    "durationText", "completed", "tagIds", "recurring", "rule",
    "rulePreset", "ruleCustomVisible", "expanded", "calendarChecked", "allDay",
}


class DraftsViewModel(QObject):
    stateChanged = Signal()

    def __init__(self, store: DraftStore, *,
                 task_exists: Callable[[str], bool] | None = None,
                 parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._store = store
        self._task_exists = task_exists
        self._storage_error = ""
        self._timer = QTimer(self)
        self._timer.setSingleShot(True)
        self._timer.setInterval(350)
        self._timer.timeout.connect(self.flush)

    @Property(str, notify=stateChanged)
    def storageError(self) -> str:
        return self._storage_error

    def _valid_context(self, context: str, uid: str) -> bool:
        return context in _CONTEXTS and bool(uid) == (context in _EDIT_CONTEXTS)

    def _task_is_available(self, context: str, uid: str) -> bool:
        if context not in _EDIT_CONTEXTS or self._task_exists is None:
            return True
        if self._task_exists(uid):
            return True
        # A form cannot recreate a task deleted elsewhere while it was closed.
        self._store.discard(context, uid)
        self._timer.start()
        return False

    @Slot(str, str, result="QVariantMap")
    def loadDraft(self, context: str, uid: str) -> dict:
        if not self._valid_context(context, uid):
            return {}
        try:
            if not self._task_is_available(context, uid):
                return {}
            return {key: value for key, value in self._store.load(context, uid).items()
                    if key in _FIELDS}
        except Exception:
            logger.exception("Could not load form draft")
            return {}

    @Slot(str, str, "QVariantMap")
    def saveDraft(self, context: str, uid: str, payload) -> None:
        if not self._valid_context(context, uid):
            return
        try:
            if not self._task_is_available(context, uid):
                return
            clean = {key: value for key, value in dict(payload).items()
                     if key in _FIELDS}
            self._store.save(context, uid, clean)
            self._timer.start()
        except Exception:
            logger.exception("Could not stage form draft")

    @Slot(str, str)
    def discardDraft(self, context: str, uid: str) -> None:
        if not self._valid_context(context, uid):
            return
        self._store.discard(context, uid)
        self.flush()

    @Slot()
    def flush(self) -> None:
        self._timer.stop()
        try:
            self._store.flush()
            error = ""
        except OSError:
            logger.exception("Could not persist form drafts")
            error = "Черновик сохранён в памяти, но не удалось записать его на диск."
        if error != self._storage_error:
            self._storage_error = error
            self.stateChanged.emit()
