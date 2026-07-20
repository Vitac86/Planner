"""Shared environment for the Phase 3.2B3C1 remote split tests.

Everything runs against one SQLite database per test and the
FakeCalendarGateway; no network, no OAuth, no automatic sync.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Optional

from planner_desktop.domain.google_series_split import RemoteSeriesSplitProposal
from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
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
from planner_desktop.usecases.manual_sync_service import ManualSyncService
from planner_desktop.usecases.recurrence_service import RecurrenceService
from planner_desktop.usecases.remote_series_split_service import (
    RemoteSeriesSplitService,
)
from planner_desktop.usecases.series_calendar_link_service import (
    SeriesCalendarLinkService,
)

TODAY = date(2026, 8, 1)
START = date(2026, 8, 3)
BASE_TIME = datetime(2026, 8, 1, 8, tzinfo=timezone.utc)


@dataclass
class SplitEnv:
    db_path: Path
    tasks: SQLiteTaskRepository
    ordinary_store: CalendarSyncStore
    series_repo: SQLiteSeriesRepository
    series_store: CalendarSeriesSyncStore
    occurrence_store: CalendarSeriesOccurrenceSyncStore
    split_store: CalendarSeriesRemoteSplitStore
    catalog: SQLiteExternalSeriesRepository
    recurrence: RecurrenceService
    links: SeriesCalendarLinkService
    splits: RemoteSeriesSplitService
    gateway: FakeCalendarGateway
    manual: ManualSyncService

    def pull_engine(self) -> CalendarSyncEngine:
        return CalendarSyncEngine(
            self.tasks,
            self.ordinary_store,
            self.gateway,
            self.catalog,
            series_link_store=self.series_store,
            occurrence_sync_store=self.occurrence_store,
            series_repository=self.series_repo,
            split_store=self.split_store,
        )

    def live_rows(self, series_uid: str):
        return sorted(
            (
                row
                for row in self.tasks.list_by_series(series_uid)
                if not row.is_deleted
            ),
            key=lambda row: (row.start or datetime.min, row.uid),
        )

    def close(self) -> None:
        for closable in (
            self.catalog, self.split_store, self.occurrence_store,
            self.series_store, self.series_repo, self.ordinary_store,
            self.tasks,
        ):
            closable.close()


def build_env(tmp_path: Path) -> SplitEnv:
    db_path = Path(tmp_path) / "desktop.db"
    tasks = SQLiteTaskRepository(db_path)
    ordinary_store = CalendarSyncStore(db_path)
    series_repo = SQLiteSeriesRepository(db_path)
    series_store = CalendarSeriesSyncStore(db_path)
    occurrence_store = CalendarSeriesOccurrenceSyncStore(db_path)
    split_store = CalendarSeriesRemoteSplitStore(db_path)
    catalog = SQLiteExternalSeriesRepository(db_path)

    recurrence = RecurrenceService(series_repo, tasks)
    links = SeriesCalendarLinkService(
        series_repo, tasks, series_store, today_provider=lambda: TODAY
    )
    recurrence.series_link_service = links
    recurrence.occurrence_sync_store = occurrence_store
    splits = RemoteSeriesSplitService(
        series_repo,
        tasks,
        series_store,
        occurrence_store,
        split_store,
        external_series_repository=catalog,
        today_provider=lambda: TODAY,
    )
    recurrence.remote_split_service = splits
    links.remote_split_service = splits

    gateway = FakeCalendarGateway(base_time=BASE_TIME)
    manual = ManualSyncService(
        tasks,
        ordinary_store,
        gateway_provider=lambda: gateway,
        external_series_repository=catalog,
        series_store=series_store,
        series_repository=series_repo,
        occurrence_store=occurrence_store,
        split_store=split_store,
    )
    return SplitEnv(
        db_path=db_path,
        tasks=tasks,
        ordinary_store=ordinary_store,
        series_repo=series_repo,
        series_store=series_store,
        occurrence_store=occurrence_store,
        split_store=split_store,
        catalog=catalog,
        recurrence=recurrence,
        links=links,
        splits=splits,
        gateway=gateway,
        manual=manual,
    )


def make_series(
    uid: str = "src-1",
    *,
    title: str = "TEST split source",
    all_day: bool = False,
    start: date = START,
    local_time: Optional[time] = time(9),
    duration: Optional[int] = 30,
    timezone_name: str = "Europe/Moscow",
    frequency: RecurrenceFrequency = RecurrenceFrequency.DAILY,
    interval: int = 1,
    weekdays: tuple = (),
    month_day: Optional[int] = None,
    yearly_month: Optional[int] = None,
    yearly_day: Optional[int] = None,
    end_mode: RecurrenceEndMode = RecurrenceEndMode.COUNT,
    count: Optional[int] = 5,
    until: Optional[date] = None,
) -> TaskSeries:
    return TaskSeries(
        uid=uid,
        title=title,
        notes="remote split test series",
        priority=1,
        schedule=SeriesSchedule(
            start_date=start,
            all_day=all_day,
            local_time=None if all_day else local_time,
            duration_minutes=None if all_day else duration,
            timezone_name=timezone_name,
        ),
        rule=RecurrenceRule(
            frequency=frequency,
            interval=interval,
            weekdays=weekdays,
            month_day=month_day,
            yearly_month=yearly_month,
            yearly_day=yearly_day,
            end_mode=end_mode,
            occurrence_count=count if end_mode is RecurrenceEndMode.COUNT else None,
            until_date=until if end_mode is RecurrenceEndMode.UNTIL else None,
        ),
    )


def link_series(env: SplitEnv, series: TaskSeries, *, horizon_days: int = 40):
    """Create + materialize + connect + one manual sync; returns the link."""
    result = env.recurrence.create_series(series)
    assert result.ok, result.errors
    env.recurrence.ensure_occurrences(
        series.schedule.start_date,
        series.schedule.start_date + timedelta(days=horizon_days),
    )
    connect = env.links.connect_to_google(series.uid)
    assert connect.ok, connect.error
    sync = env.manual.run_once()
    assert sync.ok and sync.series_masters_created >= 1, sync.__dict__
    link = env.series_store.get_link(series.uid)
    assert link is not None
    return link


def seed_instances(
    env: SplitEnv, series_uid: str, *, prefix: str = "inst", keys=None
):
    """Install fake remote instances for every live occurrence (as Google
    would materialize them), so occurrence pushes have a target."""
    from planner_desktop.domain.google_occurrence import (
        local_occurrence_to_google_original_start,
    )
    from planner_desktop.sync.calendar_series_occurrence_mapper import (
        task_to_occurrence_owned_payload,
    )

    series = env.series_repo.get_by_uid(series_uid)
    link = env.series_store.get_link(series_uid)
    assert series is not None and link is not None
    ids = {}
    for index, task in enumerate(env.live_rows(series_uid)):
        if keys is not None and str(task.occurrence_key) not in keys:
            continue
        instance_id = f"{prefix}-{series_uid}-{index + 1}"
        ids[str(task.occurrence_key)] = instance_id
        env.gateway.seed_recurring_instance({
            "id": instance_id,
            "etag": '"1"',
            **task_to_occurrence_owned_payload(task, series),
            "recurringEventId": link.remote_event_id,
            "originalStartTime": local_occurrence_to_google_original_start(
                series, str(task.occurrence_key)
            ).to_google(),
            "extendedProperties": {"private": {}},
        })
    return ids


def default_proposal(**overrides) -> RemoteSeriesSplitProposal:
    values = {"title": "TEST split successor"}
    values.update(overrides)
    return RemoteSeriesSplitProposal(**values)


def plan_split(env: SplitEnv, series_uid: str, target_index: int = 2,
               proposal: Optional[RemoteSeriesSplitProposal] = None):
    rows = env.live_rows(series_uid)
    target = rows[target_index]
    result = env.splits.create_split_plan(
        series_uid, str(target.occurrence_key),
        proposal or default_proposal(),
    )
    assert result.ok, result.error
    return result.record, target
