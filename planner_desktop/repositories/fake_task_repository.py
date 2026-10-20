"""Фейковый in-memory репозиторий задач.

Остаётся для тестов и демо-режима: никакой БД, никаких файлов,
никаких Google API. По умолчанию приложение теперь использует
SQLiteTaskRepository (planner_desktop/storage) с тем же интерфейсом.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import List, Optional

from planner_desktop.domain.task import Task


def _today_at(hour: int, minute: int = 0) -> datetime:
    return datetime.combine(datetime.now().date(), time(hour, minute))


def _seed_tasks() -> List[Task]:
    """Демо-данные, чтобы скелет UI было на что смотреть."""
    morning = Task(
        title="Разобрать входящие",
        notes="Демо-задача (фейковые данные)",
        start=_today_at(9, 30),
        duration_minutes=30,
        priority=1,
    )
    morning.end = morning.start + timedelta(minutes=30)

    meeting = Task(
        title="Созвон по проекту",
        start=_today_at(14, 0),
        duration_minutes=60,
        priority=2,
    )
    meeting.end = meeting.start + timedelta(minutes=60)

    all_day = Task(
        title="День планирования",
        start=datetime.combine(datetime.now().date(), time.min),
        is_all_day=True,
    )
    all_day.end = all_day.start + timedelta(days=1)

    return [
        morning,
        meeting,
        all_day,
        Task(title="Купить билеты", notes="Без даты — не попадает в календарь"),
        Task(title="Прочитать статью про Qt Quick"),
    ]


class FakeTaskRepository:
    """Хранит задачи в списке в памяти процесса."""

    def __init__(self, seed: bool = True) -> None:
        self._tasks: List[Task] = _seed_tasks() if seed else []
        self._next_id = len(self._tasks) + 1
        for i, task in enumerate(self._tasks, start=1):
            task.id = i
        # «Ежедневные» пункты — фиксированный чек-лист, отдельно от задач.
        self.daily_titles: List[str] = [
            "Зарядка",
            "Разбор почты",
            "Итоги дня",
        ]

    def add(self, task: Task) -> Task:
        task.id = self._next_id
        self._next_id += 1
        self._tasks.append(task)
        return task

    def all(self) -> List[Task]:
        return [t for t in self._tasks if not t.is_deleted]

    def list_all(self) -> List[Task]:
        return self.all()

    def get(self, task_id: int) -> Optional[Task]:
        """Возвращает задачу по id, включая тумбстоуны (как SQLite-репозиторий)."""
        for task in self._tasks:
            if task.id == task_id:
                return task
        return None

    def get_by_uid(self, uid: str) -> Optional[Task]:
        for task in self._tasks:
            if task.uid == uid:
                return task
        return None

    def get_by_google_event_id(self, event_id: str) -> Optional[Task]:
        """Как у SQLite-репозитория: ищет и среди тумбстоунов."""
        for task in self._tasks:
            if task.google_calendar_event_id == event_id:
                return task
        return None

    def update(self, task: Task) -> Task:
        """Задачи хранятся по ссылке, поэтому достаточно обновить updated_at."""
        task.touch()
        return task

    def update_local(self, task: Task) -> Task:
        current = self.get_by_uid(task.uid)
        if (task.start is not None and current is not None
                and current.google_calendar_event_id is not None):
            task.google_calendar_event_id = current.google_calendar_event_id
            task.google_calendar_etag = current.google_calendar_etag
            task.google_calendar_recurring_event_id = current.google_calendar_recurring_event_id
            task.google_calendar_original_start = current.google_calendar_original_start
        return self.update(task)

    def update_local_fields(
        self, uid: str, *, priority: Optional[int] = None,
        completed: Optional[bool] = None,
    ) -> Optional[Task]:
        current = self.get_by_uid(uid)
        if current is None or current.is_deleted:
            return None
        if priority is not None:
            current.priority = priority
        if completed is not None:
            current.set_completed(completed)
        return current

    def detach_calendar_task(self, task: Task) -> tuple[Task, Optional[str]]:
        current = self.get_by_uid(task.uid)
        event_id = current.google_calendar_event_id if current is not None else None
        task.google_calendar_event_id = None
        task.google_calendar_etag = None
        task.google_calendar_recurring_event_id = None
        task.google_calendar_original_start = None
        return self.update(task), event_id

    def update_calendar_metadata(
        self, uid: str, event_id: str, etag: Optional[str],
        expected_event_id: Optional[str],
    ) -> bool:
        task = self.get_by_uid(uid)
        if (task is None or task.google_calendar_event_id != expected_event_id
                or (task.start is None and not task.is_deleted)):
            return False
        task.google_calendar_event_id = event_id
        task.google_calendar_etag = etag
        return True

    def apply_calendar_update(self, task: Task, expected: Task) -> bool:
        current = self.get_by_uid(expected.uid)
        if (current is None or current.updated_at != expected.updated_at
                or current.google_calendar_event_id != expected.google_calendar_event_id
                or current.google_calendar_etag != expected.google_calendar_etag):
            return False
        for name in (
            "title", "notes", "start", "end", "duration_minutes", "is_all_day",
            "google_calendar_event_id", "google_calendar_etag",
            "google_calendar_recurring_event_id", "google_calendar_original_start",
            "deleted_at",
        ):
            setattr(current, name, getattr(task, name))
        current.touch()
        return True

    def list_today(self, reference_date: Optional[date] = None) -> List[Task]:
        day = reference_date or datetime.now().date()
        return [
            t
            for t in self.all()
            if t.start is not None and t.start.date() == day
        ]

    def list_undated(self) -> List[Task]:
        return [t for t in self.all() if t.start is None]

    # ---- локальные серии (Phase 3.2A) ----------------------------------------

    def list_by_series(self, series_uid: str) -> List[Task]:
        """Все строки серии, включая тумбстоуны (как SQLite-репозиторий)."""
        return [t for t in self._tasks if t.series_uid == series_uid]

    def hard_delete_by_uid(self, uid: str) -> bool:
        """Физическое удаление (только замена материализованных экземпляров)."""
        for index, task in enumerate(self._tasks):
            if task.uid == uid:
                del self._tasks[index]
                return True
        return False

    def delete(self, task_id: int) -> bool:
        """Тумбстоун (deleted_at), запись из списка не выбрасывается."""
        task = self.get(task_id)
        if task is None or task.is_deleted:
            return False
        task.mark_deleted()
        return True

    def complete(self, task_id: int, completed: bool = True) -> bool:
        task = self.get(task_id)
        if task is None or task.is_deleted:
            return False
        task.set_completed(completed)
        return True

    def toggle_completed(self, uid: str) -> bool:
        task = self.get_by_uid(uid)
        if task is None or task.is_deleted:
            return False
        task.set_completed(not task.completed)
        return True

    # ---- диагностика (паритет с SQLiteTaskRepository) ------------------------

    def schema_version(self) -> int:
        """В памяти реальной схемы нет — отдаём текущую версию кода."""
        from planner_desktop.storage.schema import SCHEMA_VERSION
        return SCHEMA_VERSION

    def count_active(self) -> int:
        return len(self.all())
