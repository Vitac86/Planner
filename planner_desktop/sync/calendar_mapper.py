"""Чистый маппинг Task <-> CalendarEvent. Без сети, без Qt, без Google.

Правила формы (те же, что в calendar_contract.py):

1. Задача со временем -> событие с start/end-``datetime``
   (семантика dateTime/dateTime Calendar API).
2. All-day задача -> событие с start/end-``date`` (семантика date/date),
   конец — ЭКСКЛЮЗИВНАЯ дата: однодневная задача на 2026-06-05 даёт
   start=2026-06-05, end=2026-06-06.
3. Формы никогда не смешиваются.

Правило безопасности повторяющихся экземпляров:

Экземпляр повторяющегося события (task.google_calendar_recurring_event_id
заполнен) НЕЛЬЗЯ слепо патчить по start/end — Google трактует это как
перенос экземпляра и может ответить 400/409. Поэтому
``task_to_event_patch`` для такого экземпляра сознательно ОПУСКАЕТ
start/end/is_all_day и патчит только текстовые поля. Осознанный перенос
экземпляра — отдельная будущая фича, не «обновление по умолчанию».

Продуктовые решения этой фазы (зафиксированы, могут измениться позже):

- завершённая (completed) задача остаётся обычным событием в календаре:
  Calendar не имеет понятия «выполнено», а удалять событие с телефона
  из-за галочки в десктопе нельзя — запись пропала бы у пользователя;
- задача с тумбстоуном (deleted_at) в create/update не мапится вовсе —
  для неё существует только операция delete (см. движок);
- задача без даты в календарь не отправляется (фаза 1).
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Dict

from planner_desktop.domain.task import Task, utc_now
from planner_desktop.sync.sync_types import CalendarEvent

DEFAULT_EVENT_DURATION_MINUTES = 60


def is_syncable(task: Task) -> bool:
    """Претендует ли задача на событие календаря: есть дата и нет тумбстоуна."""
    return task.start is not None and not task.is_deleted


def _require_syncable(task: Task) -> None:
    if task.is_deleted:
        raise ValueError(
            "Задача с тумбстоуном не мапится в create/update — только delete."
        )
    if task.start is None:
        raise ValueError("Задача без даты в календарь не отправляется (фаза 1).")


def _all_day_dates(task: Task) -> tuple[date, date]:
    """Пара (start, end) для all-day: только даты, конец эксклюзивный."""
    start_date = task.start.date()
    end_date = task.end.date() if task.end is not None else start_date
    if end_date <= start_date:
        end_date = start_date + timedelta(days=1)
    return start_date, end_date


def _timed_range(task: Task) -> tuple[datetime, datetime]:
    """Пара (start, end) для задачи со временем; end выводится из длительности."""
    start = task.start
    if task.end is not None and task.end > start:
        return start, task.end
    minutes = task.duration_minutes or DEFAULT_EVENT_DURATION_MINUTES
    return start, start + timedelta(minutes=minutes)


def task_to_event(task: Task) -> CalendarEvent:
    """Task -> CalendarEvent для создания события (правила 1–2, без смешения форм)."""
    _require_syncable(task)
    if task.is_all_day:
        start_date, end_date = _all_day_dates(task)
        return CalendarEvent(
            summary=task.title,
            description=task.notes,
            start=start_date,
            end=end_date,
            is_all_day=True,
        )
    start, end = _timed_range(task)
    return CalendarEvent(
        summary=task.title,
        description=task.notes,
        start=start,
        end=end,
        is_all_day=False,
    )


def task_to_event_patch(task: Task) -> Dict[str, Any]:
    """Task -> частичный патч существующего события.

    Для обычного события патчатся и текст, и start/end. Для экземпляра
    повторяющегося события (recurring_event_id заполнен) start/end/is_all_day
    ОПУСКАЮТСЯ: слепой перенос экземпляра не поддерживается (правило 3
    контракта), обновляются только summary/description.
    """
    _require_syncable(task)
    patch: Dict[str, Any] = {
        "summary": task.title,
        "description": task.notes,
    }
    if task.google_calendar_recurring_event_id is not None:
        return patch  # экземпляр серии: start/end сознательно не трогаем
    if task.is_all_day:
        start_date, end_date = _all_day_dates(task)
        patch.update(start=start_date, end=end_date, is_all_day=True)
    else:
        start, end = _timed_range(task)
        patch.update(start=start, end=end, is_all_day=False)
    return patch


def _event_times_to_task_fields(event: CalendarEvent, task: Task) -> None:
    if event.is_all_day:
        # date/date -> в домене all-day хранится как полночь начала и
        # ЭКСКЛЮЗИВНАЯ полночь конца (см. domain/task.py).
        task.is_all_day = True
        task.start = datetime.combine(event.start, time.min)
        task.end = datetime.combine(event.end, time.min)
        task.duration_minutes = None
    else:
        task.is_all_day = False
        task.start = event.start
        task.end = event.end
        if event.start is not None and event.end is not None:
            delta = event.end - event.start
            task.duration_minutes = max(int(delta.total_seconds() // 60), 1)
        else:
            task.duration_minutes = None


def _event_link_to_task_fields(event: CalendarEvent, task: Task) -> None:
    task.google_calendar_event_id = event.id
    task.google_calendar_etag = event.etag
    task.google_calendar_recurring_event_id = event.recurring_event_id
    task.google_calendar_original_start = event.original_start


def event_to_new_task(event: CalendarEvent) -> Task:
    """CalendarEvent -> новая локальная задача (событие создано на телефоне).

    Отменённое событие в задачу не превращается — этим занимается движок
    (тумбстоун существующей задачи либо игнор незнакомого события).
    """
    if event.is_cancelled:
        raise ValueError("Отменённое событие не мапится в новую задачу.")
    task = Task(title=event.summary or "(без названия)", notes=event.description)
    _event_times_to_task_fields(event, task)
    _event_link_to_task_fields(event, task)
    task.updated_at = event.updated_at or utc_now()
    return task


def apply_event_to_task(event: CalendarEvent, task: Task) -> Task:
    """Накатить удалённую правку события на существующую задачу.

    Вызывается движком только когда remote победил по конфликтной
    политике; сам маппер ничего про конфликты не решает.
    """
    if event.is_cancelled:
        raise ValueError(
            "Отменённое событие не накатывается как правка — это тумбстоун."
        )
    task.title = event.summary or task.title
    task.notes = event.description
    _event_times_to_task_fields(event, task)
    _event_link_to_task_fields(event, task)
    return task
