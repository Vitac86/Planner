"""User-visible task state used for optimistic, session-local undo."""
from __future__ import annotations

from planner_desktop.domain.task import Task

UNDO_FIELDS = (
    "title", "notes", "tags", "start", "end", "duration_minutes", "is_all_day",
    "priority", "completed", "completed_at", "deleted_at",
)
CALENDAR_UNDO_FIELDS = (
    "title", "notes", "start", "end", "duration_minutes", "is_all_day", "deleted_at",
)
GOOGLE_LINK_FIELDS = (
    "google_calendar_event_id", "google_calendar_etag",
    "google_calendar_recurring_event_id", "google_calendar_original_start",
)


def task_user_state(task: Task | None):
    if task is None:
        return None
    return tuple(
        tuple(sorted(task.tags)) if name == "tags" else getattr(task, name)
        for name in UNDO_FIELDS
    )


def supports_task_undo(task: Task | None) -> bool:
    return task is None or not (
        task.series_uid or task.google_calendar_recurring_event_id
        or task.google_calendar_original_start
    )


def prepare_task_undo(current: Task | None, expected: Task, previous: Task | None):
    """Validate user state and build an inverse using the latest remote link."""
    from copy import deepcopy
    from planner_desktop.domain.task import utc_now

    if current is None or task_user_state(current) != task_user_state(expected):
        raise ValueError("Задача изменилась после этого действия; отмена не применена.")
    if not all(supports_task_undo(task) for task in (current, expected, previous)):
        raise ValueError("Отмена действий с повторяющимися сериями не поддерживается.")
    if previous is not None and previous.uid != current.uid:
        raise ValueError("Не совпадает задача для отмены.")
    target = deepcopy(previous if previous is not None else current)
    target.id, target.uid = current.id, current.uid
    if previous is None:
        target.mark_deleted()
    for name in GOOGLE_LINK_FIELDS:
        setattr(target, name, getattr(current, name))
    restore_deleted = current.is_deleted and not target.is_deleted
    detach = (restore_deleted or target.is_deleted or target.start is None
              or (current.start is None and target.start is not None))
    cleanup_event_id = current.google_calendar_event_id if detach else None
    if detach:
        for name in GOOGLE_LINK_FIELDS:
            setattr(target, name, None)
    calendar_changed = any(
        getattr(current, name) != getattr(target, name) for name in CALENDAR_UNDO_FIELDS
    )
    target.updated_at = utc_now() if calendar_changed else current.updated_at
    return target, calendar_changed, cleanup_event_id
