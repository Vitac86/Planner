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

    def list_today(self, reference_date: Optional[date] = None) -> List[Task]:
        day = reference_date or datetime.now().date()
        return [
            t
            for t in self.all()
            if t.start is not None and t.start.date() == day
        ]

    def list_undated(self) -> List[Task]:
        return [t for t in self.all() if t.start is None]

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
        task.completed = completed
        task.touch()
        return True

    def toggle_completed(self, uid: str) -> bool:
        task = self.get_by_uid(uid)
        if task is None or task.is_deleted:
            return False
        task.completed = not task.completed
        task.touch()
        return True
