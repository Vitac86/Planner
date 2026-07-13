"""Сценарий страницы «История» (use-case-слой десктопа).

Собирает журнал выполненного из двух локальных источников и группирует по
датам:

- разовые задачи с отметкой ``completed`` (дата — ``Task.completed_at``, для
  задач, выполненных до миграции, — приблизительно ``updated_at``);
- отметки выполнения ежедневных задач по конкретным датам
  (``desktop_daily_completions``).

Полностью локально: ни сети, ни Google, ни Calendar-очереди. Чистый Python,
поэтому логику группировки и фильтра диапазона можно проверить без Qt.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

from planner_desktop.domain.task import Task
from planner_desktop.repositories import TaskRepository
from planner_desktop.repositories.daily_task_repository import DailyTaskRepository

# Диапазоны фильтра «Истории». 0 — без ограничения («всё»).
RANGE_7_DAYS = 7
RANGE_30_DAYS = 30
RANGE_ALL = 0
VALID_RANGES = (RANGE_7_DAYS, RANGE_30_DAYS, RANGE_ALL)


def _local_date(value: datetime) -> date:
    """Календарная дата события в локальной зоне (aware -> локально)."""
    if value.tzinfo is not None:
        return value.astimezone().date()
    return value.date()


def _sort_ts(value: Optional[datetime]) -> float:
    """Устойчивый ключ сортировки: и aware, и naive без ошибок сравнения."""
    if value is None:
        return 0.0
    try:
        return value.timestamp()
    except (OverflowError, OSError, ValueError):
        return 0.0


def _task_time_label(task: Task) -> str:
    if task.is_all_day:
        return "Весь день"
    if task.start is not None:
        label = task.start.strftime("%H:%M")
        if task.end is not None:
            label += "–" + task.end.strftime("%H:%M")
        return label
    return ""


@dataclass
class HistoryEntry:
    """Одна строка журнала — разовая задача или отметка ежедневной задачи."""

    kind: str            # "task" | "daily"
    uid: str
    title: str
    notes: str
    time_label: str
    is_all_day: bool
    priority: int
    completed_at: Optional[datetime]
    completion_date: date
    is_daily: bool
    can_reopen: bool


@dataclass
class HistoryGroup:
    """Записи одного дня (для заголовка-даты в списке)."""

    day: date
    entries: List[HistoryEntry] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.entries)


class HistoryService:
    """Формирует сгруппированный по датам журнал выполненного (локально)."""

    def __init__(
        self,
        task_repository: TaskRepository,
        daily_repository: DailyTaskRepository,
    ) -> None:
        self._tasks = task_repository
        self._daily = daily_repository

    # ---- сборка записей ---------------------------------------------------------

    def _task_entries(self, since: Optional[date]) -> List[HistoryEntry]:
        entries: List[HistoryEntry] = []
        for task in self._tasks.list_all():
            if not task.completed or task.is_deleted:
                continue
            stamp = task.completed_at or task.updated_at
            completion_date = _local_date(stamp) if stamp is not None else date.min
            if since is not None and completion_date < since:
                continue
            entries.append(HistoryEntry(
                kind="task",
                uid=task.uid,
                title=task.title,
                notes=task.notes,
                time_label=_task_time_label(task),
                is_all_day=task.is_all_day,
                priority=task.priority,
                completed_at=stamp,
                completion_date=completion_date,
                is_daily=False,
                can_reopen=True,
            ))
        return entries

    def _daily_entries(self, since: Optional[date]) -> List[HistoryEntry]:
        entries: List[HistoryEntry] = []
        for completion in self._daily.all_completions(since=since):
            daily = self._daily.get_by_uid(completion.daily_uid)
            title = daily.title if daily is not None else "Ежедневная задача"
            notes = daily.notes if daily is not None else ""
            time_label = daily.preferred_time if daily is not None else ""
            entries.append(HistoryEntry(
                kind="daily",
                uid=completion.daily_uid,
                title=title,
                notes=notes,
                time_label=time_label,
                is_all_day=False,
                priority=0,
                completed_at=completion.completed_at,
                completion_date=completion.done_date,
                is_daily=True,
                can_reopen=False,
            ))
        return entries

    # ---- публичный API ----------------------------------------------------------

    def groups(
        self,
        *,
        range_days: int = RANGE_ALL,
        today: Optional[date] = None,
    ) -> List[HistoryGroup]:
        """Журнал, сгруппированный по датам (свежие сверху). range_days:
        7 / 30 — последние N дней включая сегодня; 0 — без ограничения."""
        today = today or date.today()
        since = None
        if range_days and range_days > 0:
            since = today - timedelta(days=range_days - 1)

        entries = self._task_entries(since) + self._daily_entries(since)

        buckets: dict = {}
        for entry in entries:
            buckets.setdefault(entry.completion_date, []).append(entry)

        result: List[HistoryGroup] = []
        for day in sorted(buckets, reverse=True):
            day_entries = buckets[day]
            day_entries.sort(key=lambda e: _sort_ts(e.completed_at), reverse=True)
            result.append(HistoryGroup(day=day, entries=day_entries))
        return result

    def total_completed(
        self,
        *,
        range_days: int = RANGE_ALL,
        today: Optional[date] = None,
    ) -> int:
        return sum(g.count for g in self.groups(range_days=range_days, today=today))


__all__ = [
    "HistoryService",
    "HistoryEntry",
    "HistoryGroup",
    "RANGE_7_DAYS",
    "RANGE_30_DAYS",
    "RANGE_ALL",
    "VALID_RANGES",
]
