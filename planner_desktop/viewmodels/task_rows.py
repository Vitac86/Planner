"""Общие преобразования Task -> словари для QML и формы редактора.

Чистые функции без Qt: ими пользуются TodayViewModel и CalendarViewModel,
чтобы карточка задачи и диалог редактирования выглядели и вели себя
одинаково на всех страницах.
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Set

from planner_desktop.domain.commands import (
    DATE_FORMAT,
    TIME_FORMAT,
    TaskEditorCommand,
    priority_label,
)
from planner_desktop.domain.task import Task
from planner_desktop.usecases.task_service import (
    DesktopTaskService,
    TaskOperationResult,
)


def time_label(task: Task) -> str:
    if task.is_all_day:
        return "Весь день"
    if task.start is not None:
        label = task.start.strftime("%H:%M")
        if task.end is not None:
            label += "–" + task.end.strftime("%H:%M")
        return label
    return ""


def task_to_row(task: Task, pending_uids: Set[str]) -> Dict[str, Any]:
    """Строка списка задач для QML (TaskCard)."""
    return {
        "uid": task.uid,
        "title": task.title,
        "notes": task.notes,
        "timeLabel": time_label(task),
        "isAllDay": task.is_all_day,
        "priority": task.priority,
        "priorityLabel": priority_label(task.priority),
        "completed": task.completed,
        "hasPendingSync": task.uid in pending_uids,
        "isLinked": task.google_calendar_event_id is not None,
        "isScheduled": task.start is not None,
        "isRecurring": task.google_calendar_recurring_event_id is not None,
        "isSeriesOccurrence": task.series_uid is not None,
        "isSeriesException": bool(task.is_series_exception),
        "seriesUid": task.series_uid or "",
        "tags": list(task.tags[:3]),
        "tagOverflow": max(0, len(task.tags) - 3),
    }


def editor_payload(task: Optional[Task]) -> Dict[str, Any]:
    """Данные для предзаполнения TaskEditorDialog; пустая форма для None."""
    if task is None:
        return {
            "exists": False,
            "uid": "",
            "title": "",
            "notes": "",
            "priority": 0,
            "scheduled": False,
            "isAllDay": False,
            "mode": "none",
            "dateText": "",
            "timeText": "",
            "durationText": "",
            "completed": False,
            "isLinked": False,
            "isRecurringInstance": False,
            "isSeriesOccurrence": False,
            "isSeriesException": False,
            "seriesUid": "",
            "occurrenceKey": "",
        }
    scheduled = task.start is not None
    return {
        "exists": True,
        "uid": task.uid,
        "title": task.title,
        "notes": task.notes,
        "priority": task.priority,
        "scheduled": scheduled,
        "isAllDay": task.is_all_day,
        # Режим сегментов формы: none / allday / timed (см. domain/scheduling.py).
        "mode": ("allday" if task.is_all_day else "timed") if scheduled else "none",
        "dateText": task.start.strftime(DATE_FORMAT) if scheduled else "",
        "timeText": (
            task.start.strftime(TIME_FORMAT)
            if scheduled and not task.is_all_day
            else ""
        ),
        "durationText": (
            str(task.duration_minutes) if task.duration_minutes else ""
        ),
        "completed": task.completed,
        "isLinked": task.google_calendar_event_id is not None,
        "isRecurringInstance": task.google_calendar_recurring_event_id is not None,
        "isSeriesOccurrence": task.series_uid is not None,
        "isSeriesException": bool(task.is_series_exception),
        "seriesUid": task.series_uid or "",
        "occurrenceKey": task.occurrence_key or "",
    }


def save_editor(
    service: DesktopTaskService,
    uid: str,
    title: str,
    notes: str,
    priority: int,
    scheduled: bool,
    is_all_day: bool,
    date_text: str,
    time_text: str,
    duration_text: str,
    completed: bool,
) -> TaskOperationResult:
    """Единая точка сохранения формы: пустой uid — создание, иначе правка."""
    command = TaskEditorCommand(
        title=title,
        notes=notes,
        add_to_calendar=scheduled,
        is_all_day=is_all_day,
        date_text=date_text,
        time_text=time_text,
        duration_text=duration_text,
        priority=priority,
        completed=completed,
    )
    if uid:
        return service.edit_task(uid, command)
    return service.create_from_editor(command)
