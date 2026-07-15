"""Контракт репозитория локальных повторяющихся серий (Phase 3.2A).

SQLite-реализация — planner_desktop/storage/series_repository.py;
InMemorySeriesRepository — для тестов и демо-режима. Репозиторий не знает
ни про Qt, ни про Google, ни про Calendar-очередь.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Protocol, Sequence, Tuple

from planner_desktop.domain.recurrence import TaskSeries, replace_series


class SeriesRepository(Protocol):
    """Минимальный интерфейс хранилища серий."""

    def add(self, series: TaskSeries) -> TaskSeries: ...

    def update(self, series: TaskSeries) -> TaskSeries: ...

    def get_by_uid(self, uid: str) -> Optional[TaskSeries]: ...

    def list_all(self, include_inactive: bool = False) -> List[TaskSeries]: ...

    def delete(self, uid: str) -> bool: ...

    def set_series_tags(self, series_uid: str, tag_ids: Sequence[int]) -> None: ...

    def tag_ids_for_series(self, series_uid: str) -> List[int]: ...

    def count_active(self) -> int: ...


class InMemorySeriesRepository:
    """Хранит серии в памяти процесса (тесты/демо)."""

    def __init__(self) -> None:
        self._series: List[TaskSeries] = []
        self._tags: Dict[str, Tuple[int, ...]] = {}
        self._next_id = 1

    def add(self, series: TaskSeries) -> TaskSeries:
        if any(existing.uid == series.uid for existing in self._series):
            raise ValueError(f"Серия с uid {series.uid} уже существует")
        series.id = self._next_id
        self._next_id += 1
        self._series.append(series)
        return series

    def update(self, series: TaskSeries) -> TaskSeries:
        for index, existing in enumerate(self._series):
            if existing.uid == series.uid:
                series.touch()
                self._series[index] = series
                return series
        raise KeyError("Серия не найдена")

    def get_by_uid(self, uid: str) -> Optional[TaskSeries]:
        for series in self._series:
            if series.uid == uid:
                return replace_series(series)
        return None

    def list_all(self, include_inactive: bool = False) -> List[TaskSeries]:
        items = [s for s in self._series if not s.is_deleted]
        if not include_inactive:
            items = [s for s in items if s.active]
        return [replace_series(s) for s in items]

    def delete(self, uid: str) -> bool:
        for series in self._series:
            if series.uid == uid and not series.is_deleted:
                series.mark_deleted()
                return True
        return False

    def set_series_tags(self, series_uid: str, tag_ids: Sequence[int]) -> None:
        self._tags[series_uid] = tuple(dict.fromkeys(int(i) for i in tag_ids))

    def tag_ids_for_series(self, series_uid: str) -> List[int]:
        return list(self._tags.get(series_uid, ()))

    def count_active(self) -> int:
        return sum(1 for s in self._series if not s.is_deleted and s.active)


__all__ = ["InMemorySeriesRepository", "SeriesRepository"]
