"""Репозиторий ежедневных задач нового десктопа.

InMemoryDailyTaskRepository — для тестов и демо-режима (никакой БД).
SQLiteDailyTaskRepository (planner_desktop/storage) — локальное хранилище,
используется приложением по умолчанию. DailyTaskRepository — общий
контракт: и сами задачи, и отметки выполнения по датам.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional, Protocol, Set

from planner_desktop.domain.daily_task import ALL_WEEKDAYS_MASK, DailyTask


class DailyTaskRepository(Protocol):
    """Минимальный интерфейс: CRUD ежедневных задач + отметки по датам."""

    def add(self, task: DailyTask) -> DailyTask: ...

    def update(self, task: DailyTask) -> DailyTask: ...

    def get_by_uid(self, uid: str) -> Optional[DailyTask]: ...

    def list_all(self) -> List[DailyTask]: ...

    def delete(self, uid: str) -> bool: ...

    def set_completed(self, uid: str, day: date, completed: bool) -> None: ...

    def is_completed(self, uid: str, day: date) -> bool: ...

    def completed_uids_for(self, day: date) -> Set[str]: ...


def _seed_daily_tasks() -> List[DailyTask]:
    """Демо-данные для in-memory режима — чтобы список был не пустым."""
    return [
        DailyTask(title="Зарядка", weekdays_mask=ALL_WEEKDAYS_MASK,
                  preferred_time="08:00"),
        DailyTask(title="Разбор почты", weekdays_mask=0b0011111,  # будни
                  preferred_time="10:00"),
        DailyTask(title="Итоги дня", weekdays_mask=ALL_WEEKDAYS_MASK,
                  preferred_time="20:00"),
    ]


class InMemoryDailyTaskRepository:
    """Хранит ежедневные задачи и отметки выполнения в памяти процесса."""

    def __init__(self, seed: bool = False) -> None:
        self._tasks: List[DailyTask] = _seed_daily_tasks() if seed else []
        self._next_id = 1
        for task in self._tasks:
            task.id = self._next_id
            self._next_id += 1
        # (uid, "ГГГГ-ММ-ДД") -> отметка выполнено.
        self._completions: Set[tuple] = set()

    def add(self, task: DailyTask) -> DailyTask:
        task.id = self._next_id
        self._next_id += 1
        self._tasks.append(task)
        return task

    def update(self, task: DailyTask) -> DailyTask:
        task.touch()
        return task  # задачи хранятся по ссылке

    def get_by_uid(self, uid: str) -> Optional[DailyTask]:
        for task in self._tasks:
            if task.uid == uid:
                return task
        return None

    def list_all(self) -> List[DailyTask]:
        return [t for t in self._tasks if not t.is_deleted]

    def delete(self, uid: str) -> bool:
        task = self.get_by_uid(uid)
        if task is None or task.is_deleted:
            return False
        task.mark_deleted()
        return True

    def set_completed(self, uid: str, day: date, completed: bool) -> None:
        key = (uid, day.isoformat())
        if completed:
            self._completions.add(key)
        else:
            self._completions.discard(key)

    def is_completed(self, uid: str, day: date) -> bool:
        return (uid, day.isoformat()) in self._completions

    def completed_uids_for(self, day: date) -> Set[str]:
        stamp = day.isoformat()
        return {uid for (uid, d) in self._completions if d == stamp}
