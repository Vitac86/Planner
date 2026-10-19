"""Репозитории нового десктопа.

FakeTaskRepository — in-memory, для тестов и демо-режима.
SQLiteTaskRepository (planner_desktop/storage) — экспериментальное
локальное хранилище, используется приложением по умолчанию.
TaskRepository — общий контракт, на который опираются ViewModel-и.
"""
from __future__ import annotations

from datetime import date
from typing import List, Optional, Protocol

from planner_desktop.domain.task import Task

from .fake_task_repository import FakeTaskRepository


class TaskRepository(Protocol):
    """Минимальный интерфейс репозитория задач для ViewModel-ей и тестов."""

    daily_titles: List[str]

    def add(self, task: Task) -> Task: ...

    def update(self, task: Task) -> Task: ...

    def update_local(self, task: Task) -> Task: ...

    def update_local_fields(
        self, uid: str, *, priority: Optional[int] = None,
        completed: Optional[bool] = None,
    ) -> Optional[Task]: ...

    def detach_calendar_task(self, task: Task) -> tuple[Task, Optional[str]]: ...

    def update_calendar_metadata(
        self, uid: str, event_id: str, etag: Optional[str],
        expected_event_id: Optional[str],
    ) -> bool: ...

    def apply_calendar_update(self, task: Task, expected: Task) -> bool: ...

    def get(self, task_id: int) -> Optional[Task]: ...

    def get_by_uid(self, uid: str) -> Optional[Task]: ...

    def get_by_google_event_id(self, event_id: str) -> Optional[Task]: ...

    def delete(self, task_id: int) -> bool: ...

    def complete(self, task_id: int, completed: bool = True) -> bool: ...

    def toggle_completed(self, uid: str) -> bool: ...

    def all(self) -> List[Task]: ...

    def list_all(self) -> List[Task]: ...

    def list_today(self, reference_date: Optional[date] = None) -> List[Task]: ...

    def list_undated(self) -> List[Task]: ...

    def list_by_series(self, series_uid: str) -> List[Task]: ...

    def hard_delete_by_uid(self, uid: str) -> bool: ...


__all__ = ["FakeTaskRepository", "TaskRepository"]
