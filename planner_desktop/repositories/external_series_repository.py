"""Repository contract and in-memory fake for external recurring masters."""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional, Protocol

from planner_desktop.domain.external_series import ExternalCalendarSeries
from planner_desktop.domain.task import utc_now


class ExternalSeriesRepository(Protocol):
    def upsert(self, series: ExternalCalendarSeries) -> ExternalCalendarSeries: ...

    def get(
        self, provider: str, calendar_id: str, remote_event_id: str
    ) -> Optional[ExternalCalendarSeries]: ...

    def list_all(self, include_deleted: bool = True) -> List[ExternalCalendarSeries]: ...

    def mark_deleted(
        self,
        provider: str,
        calendar_id: str,
        remote_event_id: str,
        *,
        etag: Optional[str] = None,
        remote_updated_at: Optional[datetime] = None,
        seen_at: Optional[datetime] = None,
    ) -> Optional[ExternalCalendarSeries]: ...

    def count_imported_instances(self, remote_event_id: str) -> int: ...

    def possible_legacy_master_import_ids(self) -> List[str]: ...

    def latest_refresh_at(self) -> Optional[datetime]: ...


class InMemoryExternalSeriesRepository:
    def __init__(self, task_repository=None) -> None:
        self._items: dict[tuple[str, str, str], ExternalCalendarSeries] = {}
        self._next_id = 1
        self._tasks = task_repository

    @staticmethod
    def _key(provider: str, calendar_id: str, remote_event_id: str) -> tuple[str, str, str]:
        return provider, calendar_id, remote_event_id

    def upsert(self, series: ExternalCalendarSeries) -> ExternalCalendarSeries:
        key = self._key(series.provider, series.calendar_id, series.remote_event_id)
        existing = self._items.get(key)
        stored = series.clone()
        if existing is None:
            stored.id = self._next_id
            self._next_id += 1
        else:
            stored.id = existing.id
            stored.first_seen_at = existing.first_seen_at
        self._items[key] = stored
        series.id = stored.id
        series.first_seen_at = stored.first_seen_at
        return series

    def get(self, provider: str, calendar_id: str, remote_event_id: str):
        item = self._items.get(self._key(provider, calendar_id, remote_event_id))
        return item.clone() if item is not None else None

    def list_all(self, include_deleted: bool = True) -> List[ExternalCalendarSeries]:
        items = sorted(self._items.values(), key=lambda item: item.id or 0)
        if not include_deleted:
            items = [item for item in items if not item.is_cancelled]
        return [item.clone() for item in items]

    def mark_deleted(
        self, provider: str, calendar_id: str, remote_event_id: str, *,
        etag: Optional[str] = None, remote_updated_at: Optional[datetime] = None,
        seen_at: Optional[datetime] = None,
    ) -> Optional[ExternalCalendarSeries]:
        key = self._key(provider, calendar_id, remote_event_id)
        item = self._items.get(key)
        if item is None:
            return None
        stamp = seen_at or utc_now()
        item.remote_status = "cancelled"
        item.deleted_at = item.deleted_at or stamp
        item.last_seen_at = stamp
        item.etag = etag or item.etag
        item.remote_updated_at = remote_updated_at or item.remote_updated_at
        return item.clone()

    def _task_items(self):
        if self._tasks is None:
            return []
        getter = getattr(self._tasks, "_tasks", None)
        if getter is not None:
            return list(getter)
        return list(self._tasks.list_all())

    def count_imported_instances(self, remote_event_id: str) -> int:
        return sum(
            1 for task in self._task_items()
            if task.google_calendar_recurring_event_id == remote_event_id
        )

    def possible_legacy_master_import_ids(self) -> List[str]:
        masters = {item.remote_event_id for item in self._items.values()}
        return sorted(
            task.uid for task in self._task_items()
            if task.google_calendar_event_id in masters
            and task.google_calendar_recurring_event_id is None
        )

    def latest_refresh_at(self) -> Optional[datetime]:
        return max((item.last_seen_at for item in self._items.values()), default=None)


__all__ = ["ExternalSeriesRepository", "InMemoryExternalSeriesRepository"]
