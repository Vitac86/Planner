"""Фейковый in-memory репозиторий задач.

Временная заглушка на время скелета: никакой БД, никаких файлов,
никаких Google API. Настоящий репозиторий появится отдельным шагом
и реализует тот же интерфейс (list_*/add/toggle_completed).
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
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

    def get_by_uid(self, uid: str) -> Optional[Task]:
        for task in self._tasks:
            if task.uid == uid:
                return task
        return None

    def list_today(self) -> List[Task]:
        today = datetime.now().date()
        return [
            t
            for t in self.all()
            if t.start is not None and t.start.date() == today
        ]

    def list_undated(self) -> List[Task]:
        return [t for t in self.all() if t.start is None]

    def toggle_completed(self, uid: str) -> bool:
        task = self.get_by_uid(uid)
        if task is None or task.is_deleted:
            return False
        task.completed = not task.completed
        task.touch()
        return True
