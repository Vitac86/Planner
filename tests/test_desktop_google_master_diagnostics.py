"""Catalog diagnostics and additive manual-sync classification reporting."""
from datetime import date, datetime, timezone

from planner_desktop.domain.external_series import ExternalCalendarSeries
from planner_desktop.domain.task import Task
from planner_desktop.repositories.external_series_repository import (
    InMemoryExternalSeriesRepository,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.sync.sync_types import CalendarEvent
from planner_desktop.usecases.external_series_service import ExternalSeriesService
from planner_desktop.usecases.manual_sync_service import ManualSyncService


NOW = datetime(2026, 7, 15, 8, tzinfo=timezone.utc)


def item(remote_id, *, supported=True, cancelled=False):
    return ExternalCalendarSeries(
        provider="google", calendar_id="primary", remote_event_id=remote_id,
        title=f"Series {remote_id}", start_kind="all_day",
        start_value="2026-07-15", end_value="2026-07-16",
        recurrence_lines=(("RRULE:FREQ=DAILY" if supported else
                           "RRULE:FREQ=MONTHLY;BYSETPOS=1"),),
        support_status="supported" if supported else "unsupported",
        unsupported_reason=None if supported else "BYSETPOS пока не поддерживается.",
        first_seen_at=NOW, last_seen_at=NOW,
        remote_updated_at=NOW,
        remote_status="cancelled" if cancelled else "confirmed",
        deleted_at=NOW if cancelled else None,
    )


def test_query_service_reports_counts_rows_and_legacy_ids_read_only():
    tasks = FakeTaskRepository(seed=False)
    tasks.add(Task(
        title="Imported instance", google_calendar_event_id="i-1",
        google_calendar_recurring_event_id="active",
    ))
    legacy = tasks.add(Task(
        title="Possible legacy master", google_calendar_event_id="active"
    ))
    repository = InMemoryExternalSeriesRepository(tasks)
    for entry in (item("active"), item("unsupported", supported=False),
                  item("cancelled", cancelled=True)):
        repository.upsert(entry)
    service = ExternalSeriesService(repository)
    diagnostics = service.diagnostics()
    assert diagnostics["active_master_count"] == 2
    assert diagnostics["unsupported_master_count"] == 1
    assert diagnostics["cancelled_master_count"] == 1
    assert diagnostics["possible_legacy_master_import_count"] == 1
    assert diagnostics["possible_legacy_master_import_ids"] == (legacy.uid,)
    assert diagnostics["last_catalog_refresh_at"] == NOW
    rows = service.rows()
    active = next(row for row in rows if row["remoteEventId"] == "active")
    assert active["importedInstanceCount"] == 1
    assert "description" not in active
    assert tasks.get_by_uid(legacy.uid).is_deleted is False


def test_manual_sync_result_exposes_master_instance_and_support_counts(tmp_path):
    db_path = tmp_path / "desktop.db"
    tasks = SQLiteTaskRepository(db_path)
    store = CalendarSyncStore(db_path)
    external = InMemoryExternalSeriesRepository(tasks)
    gateway = FakeCalendarGateway(base_time=NOW)
    gateway.insert_event(CalendarEvent(
        summary="Ordinary", start=date(2026, 7, 15), end=date(2026, 7, 16),
        is_all_day=True,
    ))
    master = gateway.insert_event(CalendarEvent(
        summary="Master", start=date(2026, 7, 15), end=date(2026, 7, 16),
        is_all_day=True, recurrence_lines=("RRULE:FREQ=DAILY",),
    ))
    gateway.insert_event(CalendarEvent(
        summary="Unsupported", start=date(2026, 7, 15), end=date(2026, 7, 16),
        is_all_day=True,
        recurrence_lines=("RRULE:FREQ=MONTHLY;BYSETPOS=1",),
    ))
    gateway.insert_event(CalendarEvent(
        summary="Instance", start=date(2026, 7, 16), end=date(2026, 7, 17),
        is_all_day=True, recurring_event_id=master.id,
        original_start=datetime(2026, 7, 16),
    ))
    sync = ManualSyncService(
        tasks, store, gateway_provider=lambda: gateway,
        external_series_repository=external,
    )
    result = sync.run_once()
    assert result.ok
    assert result.ordinary_events_pulled == 1
    assert result.recurring_masters_discovered == 2
    assert result.recurring_instances_pulled == 1
    assert result.unsupported_masters == 1
    assert result.cancelled_masters == 0
    assert "мастеров серий 2" in result.summary
    store.close()
    tasks.close()

