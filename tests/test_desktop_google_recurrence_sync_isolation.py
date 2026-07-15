"""Phase 3.2B1 discovery is pull-only and creates no Calendar operations."""
from datetime import date

from planner_desktop.domain.recurrence import RecurrenceFrequency, RecurrenceRule
from planner_desktop.repositories.external_series_repository import (
    InMemoryExternalSeriesRepository,
)
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.sync.google_calendar_gateway import (
    event_to_insert_body,
    recurrence_to_google_lines,
)
from planner_desktop.sync.sync_types import CalendarEvent


def test_master_pull_has_zero_queue_delta_and_zero_remote_writes(tmp_path):
    db_path = tmp_path / "desktop.db"
    tasks = SQLiteTaskRepository(db_path)
    queue = CalendarSyncStore(db_path)
    catalog = InMemoryExternalSeriesRepository(tasks)
    gateway = FakeCalendarGateway()
    gateway.insert_event(CalendarEvent(
        summary="Master", start=date(2026, 7, 15), end=date(2026, 7, 16),
        is_all_day=True, recurrence_lines=("RRULE:FREQ=DAILY",),
    ))
    gateway.reset_call_counts()
    before = queue.count_pending_ops()
    CalendarSyncEngine(tasks, queue, gateway, catalog).pull_remote_changes()
    assert queue.count_pending_ops() - before == 0
    assert gateway.write_call_count == 0
    assert gateway.list_call_count == 1
    tasks.close()
    queue.close()


def test_pure_serialization_helpers_make_no_gateway_calls():
    gateway = FakeCalendarGateway()
    lines = recurrence_to_google_lines(
        RecurrenceRule(RecurrenceFrequency.DAILY)
    )
    assert lines == ("RRULE:FREQ=DAILY;INTERVAL=1",)
    assert gateway.list_call_count == gateway.write_call_count == 0


def test_production_ordinary_insert_body_never_writes_recurrence_in_b1():
    event = CalendarEvent(
        summary="Transport only", start=date(2026, 7, 15),
        end=date(2026, 7, 16), is_all_day=True,
        recurrence_lines=("RRULE:FREQ=DAILY",),
    )
    assert "recurrence" not in event_to_insert_body(event)

