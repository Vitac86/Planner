from datetime import date, datetime, time

import pytest

from planner_desktop.domain.recurrence import RecurrenceFrequency
from planner_desktop.domain.task import Task
from planner_desktop.usecases.series_conflict_service import (
    CONFIRMATION_REQUIRED_ERROR,
)
from tests.test_desktop_series_conflict_service import (
    TODAY,
    make_conflict,
    make_stack,
)


def _seed_history_rows(stack):
    tasks = stack.tasks
    completed = tasks.add(Task(
        title="Done", start=datetime(2026, 7, 13, 9), end=datetime(2026, 7, 13, 9, 30),
        series_uid="s1", occurrence_key="2026-07-13T09:00@Europe/Moscow",
        completed=True,
    ))
    exception = tasks.add(Task(
        title="Moved", start=datetime(2026, 7, 14, 11), end=datetime(2026, 7, 14, 12),
        series_uid="s1", occurrence_key="2026-07-14T09:00@Europe/Moscow",
        is_series_exception=True,
    ))
    tombstone = tasks.add(Task(
        title="Removed", start=datetime(2026, 7, 12, 9), end=datetime(2026, 7, 12, 9, 30),
        series_uid="s1", occurrence_key="2026-07-12T09:00@Europe/Moscow",
    ))
    tasks.delete(tombstone.id)
    future_a = tasks.add(Task(
        title="Future A", start=datetime(2026, 7, 16, 9), end=datetime(2026, 7, 16, 9, 30),
        series_uid="s1", occurrence_key="2026-07-16T09:00@Europe/Moscow",
    ))
    future_b = tasks.add(Task(
        title="Future B", start=datetime(2026, 7, 17, 9), end=datetime(2026, 7, 17, 9, 30),
        series_uid="s1", occurrence_key="2026-07-17T09:00@Europe/Moscow",
    ))
    future_exception = tasks.add(Task(
        title="Future exception", start=datetime(2026, 7, 18, 14),
        end=datetime(2026, 7, 18, 15),
        series_uid="s1", occurrence_key="2026-07-18T09:00@Europe/Moscow",
        is_series_exception=True,
    ))
    return completed, exception, tombstone, future_a, future_b, future_exception


def _remote_edit_schedule(stack):
    """Foreign edit: new title, weekly rule, new time — still supported."""
    stack.gateway.patch_event(stack.remote_id, {"summary": "Google version"})
    event = stack.gateway._events[stack.remote_id]
    event.start = datetime(2026, 7, 15, 10)
    event.end = datetime(2026, 7, 15, 10, 45)
    event.recurrence_lines = ("RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=WE,FR",)
    event.recurrence_start = event.start
    stack.gateway.patch_event(stack.remote_id, {"description": "note"})
    stack.pull.pull_remote_changes()
    link = stack.store.get_link("s1")
    assert link.link_status.value == "conflict"
    return link


def test_use_google_requires_confirmation(tmp_path):
    stack = make_stack(tmp_path)
    _remote_edit_schedule(stack)
    refused = stack.conflicts.resolve_use_google("s1")
    assert not refused.ok and refused.error == CONFIRMATION_REQUIRED_ERROR
    stack.store.close(); stack.ordinary.close()


def test_use_google_applies_snapshot_without_any_gateway_call(tmp_path):
    stack = make_stack(tmp_path)
    _seed_history_rows(stack)
    link = _remote_edit_schedule(stack)
    writes = stack.gateway.write_call_count
    lists = stack.gateway.list_call_count
    result = stack.conflicts.resolve_use_google("s1", confirmed=True)
    assert result.ok, result.error
    # Strictly local: zero Google reads or writes.
    assert stack.gateway.write_call_count == writes
    assert stack.gateway.list_call_count == lists

    series = stack.series_repo.get_by_uid("s1")
    assert series.title == "Google version"
    assert series.notes == "note"
    assert series.schedule.local_time == time(10, 0)
    assert series.schedule.duration_minutes == 45
    assert series.rule.frequency is RecurrenceFrequency.WEEKLY
    assert series.rule.weekdays == (2, 4)
    assert series.revision == 2

    stored = stack.store.get_link("s1")
    assert stored.link_status.value == "synced"
    assert stored.resolution_kind == "use_google"
    assert stored.conflict_remote_snapshot_json is None
    assert stored.remote_etag == link.conflict_remote_etag
    assert stored.last_synced_series_revision == 2
    audit = stack.store.list_resolutions("s1")[0]
    assert audit.status == "completed"
    assert audit.local_revision_before == 1
    assert audit.local_revision_after == 2
    assert stack.store.count_pending_ops() == 0
    stack.store.close(); stack.ordinary.close()


def test_use_google_preserves_history_and_replaces_future_rows(tmp_path):
    stack = make_stack(tmp_path)
    completed, exception, tombstone, future_a, future_b, future_exception = (
        _seed_history_rows(stack)
    )
    _remote_edit_schedule(stack)
    assert stack.conflicts.resolve_use_google("s1", confirmed=True).ok
    rows = {task.uid: task for task in stack.tasks.list_by_series("s1")}
    # Completed history, past exception, past tombstone and the future
    # exception all survive.
    assert rows[completed.uid].completed
    assert rows[exception.uid].is_series_exception
    assert rows[tombstone.uid].is_deleted
    assert rows[future_exception.uid].title == "Future exception"
    # Future uncompleted non-exception rows are replaced (removed; the
    # materializer recreates them from the accepted definition).
    assert future_a.uid not in rows
    assert future_b.uid not in rows
    stack.store.close(); stack.ordinary.close()


def test_use_google_keeps_tags_local(tmp_path):
    stack = make_stack(tmp_path)
    stack.series_repo.set_series_tags("s1", [11, 22])
    _remote_edit_schedule(stack)
    assert stack.conflicts.resolve_use_google("s1", confirmed=True).ok
    assert stack.series_repo.tag_ids_for_series("s1") == [11, 22]
    stack.store.close(); stack.ordinary.close()


def test_use_google_disabled_for_unsupported_recurrence(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    event = stack.gateway._events[stack.remote_id]
    event.recurrence_lines = ("RRULE:FREQ=WEEKLY;BYDAY=-1FR",)
    stack.gateway.patch_event(stack.remote_id, {"summary": "Unsupported"})
    stack.pull.pull_remote_changes()
    data = stack.conflicts.get_conflict("s1")
    assert data["canUseGoogle"] is False
    assert data["remote"]["supported"] is False
    assert data["remote"]["rawRecurrence"] == ["RRULE:FREQ=WEEKLY;BYDAY=-1FR"]
    assert data["remote"]["unsupportedReason"]
    # Keep Planner remains allowed (ownership verified) and disconnect too.
    assert data["canKeepPlanner"] is True
    assert data["canDisconnect"] is True
    refused = stack.conflicts.resolve_use_google("s1", confirmed=True)
    assert not refused.ok
    assert stack.series_repo.get_by_uid("s1").title == "Local authoritative"
    stack.store.close(); stack.ordinary.close()


def test_use_google_rejects_exdate_masters(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    event = stack.gateway._events[stack.remote_id]
    event.recurrence_lines = (
        "RRULE:FREQ=DAILY;INTERVAL=1",
        "EXDATE;TZID=Europe/Moscow:20260716T090000",
    )
    stack.gateway.patch_event(stack.remote_id, {"summary": "With EXDATE"})
    stack.pull.pull_remote_changes()
    refused = stack.conflicts.resolve_use_google("s1", confirmed=True)
    assert not refused.ok
    assert "EXDATE" in refused.error
    stack.store.close(); stack.ordinary.close()


def test_use_google_compensation_rolls_back_on_failure(tmp_path, monkeypatch):
    stack = make_stack(tmp_path)
    rows_before = _seed_history_rows(stack)
    _remote_edit_schedule(stack)
    original_title = stack.series_repo.get_by_uid("s1").title

    def crash(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(stack.store, "complete_use_google_locally", crash)
    result = stack.conflicts.resolve_use_google("s1", confirmed=True)
    assert not result.ok
    # Every mutated row is restored: series definition and future rows.
    series = stack.series_repo.get_by_uid("s1")
    assert series.title == original_title
    assert series.revision == 1
    uids = {task.uid for task in stack.tasks.list_by_series("s1")}
    assert {row.uid for row in rows_before} <= uids
    assert stack.store.get_link("s1").link_status.value == "conflict"
    audit = stack.store.list_resolutions("s1")[0]
    assert audit.status == "failed"
    stack.store.close(); stack.ordinary.close()


def test_use_google_next_pull_is_echo(tmp_path):
    stack = make_stack(tmp_path)
    _remote_edit_schedule(stack)
    assert stack.conflicts.resolve_use_google("s1", confirmed=True).ok
    stack.pull.pull_remote_changes()
    link = stack.store.get_link("s1")
    assert link.link_status.value == "synced"
    assert stack.store.count_pending_ops() == 0
    stack.store.close(); stack.ordinary.close()


def test_use_google_atomic_sqlite_rollback(tmp_path):
    """The SQLite unit of work rolls back everything when the audit row is
    missing (simulated storage failure at the last step)."""
    from planner_desktop.storage.series_repository import SQLiteSeriesRepository
    from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
    from planner_desktop.domain.recurrence import (
        RecurrenceRule, SeriesSchedule, TaskSeries, replace_series,
    )

    db = tmp_path / "atomic.db"
    series_repo = SQLiteSeriesRepository(db)
    tasks = SQLiteTaskRepository(db)
    series = series_repo.add(TaskSeries(
        uid="sq1", title="Original",
        schedule=SeriesSchedule(TODAY, False, time(9), 30, "Europe/Moscow"),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    ))
    row = tasks.add(Task(
        title="Future", start=datetime(2026, 7, 16, 9),
        end=datetime(2026, 7, 16, 9, 30),
        series_uid="sq1", occurrence_key="2026-07-16T09:00@Europe/Moscow",
    ))
    from planner_desktop.storage.calendar_series_sync_store import (
        CalendarSeriesSyncStore,
    )
    from planner_desktop.domain.series_calendar_link import SeriesCalendarLink
    store = CalendarSeriesSyncStore(db)
    link = store.create_pending_link(
        SeriesCalendarLink(series_uid="sq1", remote_event_id="plrq1"),
        desired_revision=1, desired_payload_hash="h", payload={},
    )
    accepted = replace_series(series, title="Accepted", revision=2)
    with pytest.raises(KeyError):
        series_repo.accept_remote_master_atomic(
            accepted=accepted,
            removed_task_uids=[row.uid],
            link_id=link.id,
            resolution_id=99999,  # audit row does not exist -> full rollback
            remote_etag='"9"',
            remote_updated_at_text=None,
            synced_payload_hash="rh",
        )
    assert series_repo.get_by_uid("sq1").title == "Original"
    assert tasks.get_by_uid(row.uid) is not None
    assert store.get_link("sq1").link_status.value == "pending_create"
    assert store.count_pending_ops() == 1
    store.close(); tasks.close(); series_repo.close()
