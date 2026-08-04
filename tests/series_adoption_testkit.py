"""Shared deterministic environment for Phase 3.2B3C2 adoption tests."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta

from planner_desktop.storage.calendar_series_adoption_store import (
    CalendarSeriesAdoptionStore,
)
from planner_desktop.storage.calendar_series_occurrence_sync_store import (
    CalendarSeriesOccurrenceSyncStore,
)
from planner_desktop.storage.calendar_series_remote_split_store import (
    CalendarSeriesRemoteSplitStore,
)
from planner_desktop.storage.calendar_series_sync_store import (
    CalendarSeriesSyncStore,
)
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.external_series_repository import (
    SQLiteExternalSeriesRepository,
)
from planner_desktop.storage.series_repository import SQLiteSeriesRepository
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.sync.sync_types import CalendarEvent
from planner_desktop.usecases.series_adoption_service import SeriesAdoptionService


@dataclass
class AdoptionEnv:
    db_path: object
    tasks: SQLiteTaskRepository
    ordinary_store: CalendarSyncStore
    series: SQLiteSeriesRepository
    series_store: CalendarSeriesSyncStore
    occurrence_store: CalendarSeriesOccurrenceSyncStore
    split_store: CalendarSeriesRemoteSplitStore
    catalog: SQLiteExternalSeriesRepository
    adoption_store: CalendarSeriesAdoptionStore
    gateway: FakeCalendarGateway
    service: SeriesAdoptionService
    remote_id: str

    def close(self) -> None:
        self.adoption_store.close()
        self.catalog.close()
        self.split_store.close()
        self.occurrence_store.close()
        self.series_store.close()
        self.series.close()
        self.ordinary_store.close()
        self.tasks.close()


def build_env(
    tmp_path,
    *,
    all_day: bool = False,
    extras=None,
    private=None,
    recurrence=("RRULE:FREQ=DAILY;COUNT=3",),
    access_role="owner",
) -> AdoptionEnv:
    db_path = tmp_path / "desktop.db"
    tasks = SQLiteTaskRepository(db_path)
    ordinary_store = CalendarSyncStore(db_path)
    series = SQLiteSeriesRepository(db_path)
    series_store = CalendarSeriesSyncStore(db_path)
    occurrence_store = CalendarSeriesOccurrenceSyncStore(db_path)
    split_store = CalendarSeriesRemoteSplitStore(db_path)
    catalog = SQLiteExternalSeriesRepository(db_path)
    adoption_store = CalendarSeriesAdoptionStore(db_path)
    gateway = FakeCalendarGateway(calendar_access_role=access_role)
    start_day = date.today() + timedelta(days=7)
    if all_day:
        start = start_day
        end = start_day + timedelta(days=1)
        timezone_name = None
    else:
        start = datetime.combine(start_day, datetime.min.time()).replace(hour=10)
        end = start + timedelta(minutes=45)
        timezone_name = "Europe/Moscow"
    event = CalendarEvent(
        id="external-master",
        summary="External clean series",
        description="Visible Google notes",
        start=start,
        end=end,
        is_all_day=all_day,
        recurrence_lines=tuple(recurrence),
        recurrence_start=start,
        start_timezone=timezone_name,
        end_timezone=timezone_name,
        private_extended_properties=dict(private or {"foreign_key": "keep"}),
    )
    seeded = gateway.seed_external_recurring_master(event, extras=extras)
    CalendarSyncEngine(
        tasks,
        ordinary_store,
        gateway,
        catalog,
        series_link_store=series_store,
        occurrence_sync_store=occurrence_store,
        series_repository=series,
        split_store=split_store,
        adoption_store=adoption_store,
    ).pull_remote_changes()
    service = SeriesAdoptionService(
        catalog,
        adoption_store,
        series_store=series_store,
        split_store=split_store,
    )
    return AdoptionEnv(
        db_path=db_path,
        tasks=tasks,
        ordinary_store=ordinary_store,
        series=series,
        series_store=series_store,
        occurrence_store=occurrence_store,
        split_store=split_store,
        catalog=catalog,
        adoption_store=adoption_store,
        gateway=gateway,
        service=service,
        remote_id=str(seeded.id),
    )

