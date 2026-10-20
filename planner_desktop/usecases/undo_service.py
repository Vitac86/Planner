"""Bounded session history; inverse operations use repository-level atomic CAS."""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from dataclasses import dataclass
from functools import wraps
from typing import Callable, Optional

from planner_desktop.domain.task import Task
from planner_desktop.domain.task import utc_now
from planner_desktop.domain.task_undo import UNDO_FIELDS, supports_task_undo, task_user_state


@dataclass(frozen=True)
class UndoChange:
    before: Optional[Task]
    after: Task


@dataclass(frozen=True)
class UndoEntry:
    label: str
    changes: tuple[UndoChange, ...]
    unsupported_count: int = 0


@dataclass(frozen=True)
class UndoResult:
    restored_count: int = 0
    failed_count: int = 0
    errors: tuple[str, ...] = ()
    label: str = ""

    @property
    def ok(self) -> bool:
        return self.restored_count > 0 and self.failed_count == 0

    @property
    def message(self) -> str:
        if self.failed_count:
            return (f"Отменено: {self.restored_count}; не отменено: {self.failed_count}. "
                    + " ".join(self.errors))
        if self.restored_count:
            return f"Отменено: {self.label} ({self.restored_count})."
        return self.errors[0] if self.errors else "Нет действий для отмены."


class UndoService:
    """One history shared by all task surfaces; never restores old queue rows."""

    def __init__(self, task_service, limit: int = 20):
        self.service = task_service
        self.limit = max(1, int(limit))
        self._history: list[UndoEntry] = []
        self._listeners: list[Callable] = []
        self._active = None
        self._suspended = False
        self.status_message = "Отмена доступна для обычных задач; действия с сериями не отменяются."

    def add_listener(self, callback: Callable) -> None:
        self._listeners.append(callback)

    def _notify(self, event: str = "changed", label: str = "") -> None:
        for listener in tuple(self._listeners):
            listener(event, label)

    @property
    def can_undo(self) -> bool:
        return bool(self._history)

    @property
    def action_label(self) -> str:
        return self._history[-1].label if self._history else ""

    def clear(self) -> None:
        self._history.clear()
        self._notify()

    def observe(self, uid: str, task: Optional[Task], fields=None) -> None:
        if self._active is None or not uid:
            return
        before = self._active["before"].get(uid)
        observed = deepcopy(task)
        base = self._active["after"].get(uid) or before
        owned = self._active["owned_fields"].setdefault(uid, set())
        if fields is not None and base is not None and observed is not None:
            fields = set(fields)
            # Rebase untouched fields on the fresh result of a targeted write.
            # A phone edit merged before priority/completion/delete stays intact
            # when that local action is undone. Earlier writes in this group
            # remain protected by their own expected values.
            exact = deepcopy(observed)
            for name in owned - fields:
                setattr(exact, name, getattr(base, name))
            owned.update(fields)
            if before is not None:
                rebased = deepcopy(observed)
                for name in owned:
                    setattr(rebased, name, getattr(before, name))
                self._active["before"][uid] = rebased
            observed = exact
        elif before is not None and observed is not None:
            owned.update(name for name in UNDO_FIELDS
                         if getattr(before, name) != getattr(observed, name))
        self._active["after"][uid] = observed
        versions = self._active["versions"]
        versions[uid] = versions.get(uid, 0) + 1

    def observation_revision(self, uid: str) -> int:
        return self._active["versions"].get(uid, 0) if self._active else 0

    @contextmanager
    def capture(self, label: str, uids, *, before=None, fields=None, autodetect=False):
        if self._suspended:
            yield self
            return
        outer = self._active is None
        if outer:
            self._active = {
                "label": label, "before": {}, "after": {}, "versions": {}, "owned_fields": {},
            }
        active = self._active
        requested = tuple(dict.fromkeys(uid for uid in uids if uid))
        for uid in requested:
            if uid not in active["before"]:
                original = (before[uid] if before is not None and uid in before
                            else self.service.repository.get_by_uid(uid))
                active["before"][uid] = deepcopy(original)
        try:
            yield self
        finally:
            for uid in requested:
                # Explicit groups also cover local tag operations, which do
                # not use DesktopTaskService's ordinary mutation methods.
                if autodetect and (uid not in active["after"] or fields is not None):
                    self.observe(uid, self.service.repository.get_by_uid(uid), fields)
            if outer:
                self._active = None
                changes = []
                unsupported = 0
                for uid, original in active["before"].items():
                    changed = active["after"].get(uid)
                    if changed is None or task_user_state(original) == task_user_state(changed):
                        continue
                    if not supports_task_undo(original) or not supports_task_undo(changed):
                        unsupported += 1
                        continue
                    changes.append(UndoChange(original, changed))
                if changes:
                    entry = UndoEntry(active["label"], tuple(changes), unsupported)
                    self._history.append(entry)
                    self._history = self._history[-self.limit:]
                    self.status_message = (
                        "Часть действий с сериями не поддерживает отмену."
                        if unsupported else f"Можно отменить: {entry.label}"
                    )
                    self._notify("recorded", entry.label)
                elif unsupported:
                    self.status_message = "Действия с повторяющимися сериями не поддерживают отмену."
                    self._notify()

    def undo(self) -> UndoResult:
        if not self._history:
            return UndoResult(errors=("Нет действий для отмены.",))
        entry = self._history.pop()
        errors = []
        if entry.unsupported_count:
            errors.append(
                f"Изменения повторяющихся задач ({entry.unsupported_count}) не поддерживают отмену."
            )
        restored = 0
        self._suspended = True
        try:
            for change in reversed(entry.changes):
                try:
                    # The demo keeps tag associations in a separate in-memory
                    # repository. Validate ids before mutating either store.
                    tags = getattr(self.service, "tag_service", None)
                    memory_tag_ids = None
                    if (not hasattr(self.service.repository, "db_path") and tags is not None
                            and change.before is not None
                            and change.before.tags != change.after.tags):
                        available = {tag.name: tag.id for tag in tags.list_tags()}
                        if any(name not in available for name in change.before.tags):
                            raise ValueError("Один из прежних тегов удалён; отмена не применена.")
                        memory_tag_ids = [available[name] for name in change.before.tags]
                    self.service.repository.apply_undo(
                        change.after, change.before,
                        sync_enabled=self.service.calendar_queue is not None,
                    )
                    if memory_tag_ids is not None:
                        tags.repository.set_for_task(change.after.uid, memory_tag_ids, utc_now())
                    restored += 1
                except Exception as exc:
                    errors.append(f"«{change.after.title}»: {exc}")
        finally:
            self._suspended = False
        failed = len(errors) + max(0, entry.unsupported_count - 1)
        result = UndoResult(restored, failed, tuple(errors), entry.label)
        self.status_message = result.message
        self._notify()
        return result


def undoable(label: str, *, key: str = "uid", fields=None):
    """Capture public service boundaries, coalescing nested operations/groups."""
    def decorate(method):
        @wraps(method)
        def wrapped(self, item, *args, **kwargs):
            owner = getattr(self, "_service", self)
            history = owner.undo_service
            if key == "id":
                task = owner.repository.get(item)
                uid = task.uid if task is not None else ""
            elif key == "task":
                uid = item.uid
            else:
                uid = item
            initial = {uid: kwargs["previous"]} if kwargs.get("previous") is not None else None
            with history.capture(label, [uid], before=initial, autodetect=False):
                revision = history.observation_revision(uid)
                result = method(self, item, *args, **kwargs)
                task = result if isinstance(result, Task) else getattr(result, "task", None)
                # A child operation can report an exact/narrow result. Do not
                # replace it with a fresh read containing unrelated remote edits.
                if history.observation_revision(uid) == revision:
                    if task is not None:
                        history.observe(uid, task, fields)
                    elif result is True:
                        history.observe(uid, owner.repository.get_by_uid(uid), fields)
                return result
        return wrapped
    return decorate
