"""Сценарии работы с задачами (use-case-слой нового десктопа).

ViewModel-и больше не решают, что делать с Calendar-очередью: сервис
выполняет операцию в репозитории и, если передана очередь
(CalendarSyncStore), ставит отложенную Calendar-операцию по правилам
из sync/calendar_sync_engine.py (record_local_*). Сам сервис НИКОГДА
не ходит в Google и сеть — push выполняет движок синхронизации
отдельно, когда появится реальный шлюз.

Продуктовые правила фазы 1:

- Calendar-операции ставятся только задачам с датой (timed или all-day);
- галочка «выполнено» — локальная: Calendar не имеет понятия
  «выполнено», событие остаётся в календаре как есть, операция
  в очередь не ставится;
- удаление задачи — тумбстоун; delete-операция ставится только если
  событие уже существовало (иначе снимается недопушенный create).
"""
from __future__ import annotations

from typing import Optional

from planner_desktop.domain.task import Task
from planner_desktop.repositories import TaskRepository
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.sync.calendar_sync_engine import (
    record_local_create,
    record_local_delete,
    record_local_update,
)


class DesktopTaskService:
    """CRUD задач + постановка Calendar-операций в локальную очередь."""

    def __init__(
        self,
        repository: TaskRepository,
        calendar_queue: Optional[CalendarSyncStore] = None,
    ) -> None:
        self.repository = repository
        self._queue = calendar_queue

    def create_task(self, task: Task) -> Task:
        created = self.repository.add(task)
        if self._queue is not None:
            record_local_create(self._queue, created)
        return created

    def update_task(self, task: Task) -> Task:
        updated = self.repository.update(task)
        if self._queue is not None:
            record_local_update(self._queue, updated)
        return updated

    def complete_task(self, task_id: int, completed: bool = True) -> bool:
        """Локальная галочка: Calendar-операция сознательно не ставится."""
        return self.repository.complete(task_id, completed)

    def toggle_completed(self, uid: str) -> bool:
        """Как complete_task: выполнено/не выполнено в календарь не уходит."""
        return self.repository.toggle_completed(uid)

    def delete_task(self, task_id: int) -> bool:
        """Тумбстоун в репозитории + delete/отмена операций в очереди."""
        deleted = self.repository.delete(task_id)
        if deleted and self._queue is not None:
            tombstone = self.repository.get(task_id)
            if tombstone is not None:
                record_local_delete(self._queue, tombstone)
        return deleted
