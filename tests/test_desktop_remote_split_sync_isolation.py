"""Isolation: the split never contaminates ordinary/master/occurrence sync,
performs no automatic network calls and does not enable adoption."""
from __future__ import annotations

from datetime import datetime, timedelta

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.task import Task
from planner_desktop.sync.sync_types import CalendarEvent
from planner_desktop.usecases.task_service import DesktopTaskService
from tests.remote_split_testkit import (
    START,
    build_env,
    link_series,
    make_series,
    plan_split,
    seed_instances,
)


def test_split_produces_zero_ordinary_events_and_queue_rows(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    plan_split(env, "src-1")
    assert env.manual.run_once().ok

    assert env.ordinary_store.count_pending_ops() == 0
    ordinary_events = [
        event for event in env.gateway.events
        if event.is_ordinary_event and not event.is_cancelled
    ]
    assert ordinary_events == []
    # Materialized occurrences carry no Google event ids.
    for row in env.tasks.list_by_series("src-1"):
        assert row.google_calendar_event_id is None
    env.close()


def test_ordinary_and_master_sync_remain_operational(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    plan_split(env, "src-1")
    assert env.manual.run_once().ok

    # Ordinary Task sync still works through its own queue.
    desktop = DesktopTaskService(env.tasks, env.ordinary_store)
    ordinary = desktop.create_task(Task(
        title="TEST ordinary task",
        start=datetime(2026, 8, 3, 15),
        end=datetime(2026, 8, 3, 15, 30),
    ))
    summary = env.manual.run_once()
    assert summary.ok and summary.pushed >= 1
    assert env.tasks.get_by_uid(ordinary.uid).google_calendar_event_id

    # Master sync still works for an unrelated linked series.
    other = link_series(env, make_series(uid="src-2", title="TEST other"))
    assert env.recurrence.update_series("src-2", title="TEST other renamed").ok
    summary = env.manual.run_once()
    assert summary.ok and summary.series_masters_updated == 1
    assert env.gateway.get_recurring_master_resource(
        other.remote_event_id
    )["summary"] == "TEST other renamed"
    env.close()


def test_occurrence_sync_remains_operational_after_split(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    assert env.manual.run_once().ok
    plan = env.split_store.get_plan(record.id)
    successor_uid = plan.reserved_successor_series_uid
    env.recurrence.ensure_occurrences(START, START + timedelta(days=10))

    rows = env.live_rows(successor_uid)
    target = rows[0]
    seed_instances(env, successor_uid, keys={str(target.occurrence_key)})
    command = TaskEditorCommand(
        title="TEST successor exception",
        notes=target.notes,
        priority=target.priority,
        completed=False,
        add_to_calendar=True,
        is_all_day=False,
        date_text=target.start.date().isoformat(),
        time_text=(target.start + timedelta(hours=1)).strftime("%H:%M"),
        duration_text="30",
    )
    assert env.recurrence.edit_occurrence(target.uid, command).ok
    summary = env.manual.run_once()
    assert summary.ok and summary.occurrence_updates_pushed == 1
    env.close()


def test_no_network_on_plan_or_ui_and_no_automatic_sync(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    env.gateway.reset_call_counts()
    # Planning, validating, listing, cancelling: zero Google calls.
    record, target = plan_split(env, "src-1")
    env.splits.validate_split(
        "src-1", str(target.occurrence_key)
    )
    env.splits.list_split_history()
    env.splits.diagnostics()
    env.splits.request_split_rollback(record.id)
    assert env.gateway.write_call_count == 0
    assert env.gateway.list_call_count == 0
    env.close()


def test_adoption_of_external_masters_remains_absent(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    plan_split(env, "src-1")

    # A foreign recurring master pulled during an active split is only
    # catalogued; it never becomes a local TaskSeries or a Task.
    foreign = env.gateway.insert_event.__self__  # gateway instance
    event = CalendarEvent(
        summary="TEST foreign external master",
        start=datetime(2026, 8, 4, 10),
        end=datetime(2026, 8, 4, 11),
        recurrence_lines=("RRULE:FREQ=DAILY;INTERVAL=1;COUNT=3",),
        start_timezone="Europe/Moscow",
        end_timezone="Europe/Moscow",
    )
    stored = foreign.insert_recurring_master("foreignmaster1", _owned(event))
    assert stored is not None

    series_before = {s.uid for s in env.series_repo.list_all(True)}
    assert env.manual.run_once().ok
    series_after = {s.uid for s in env.series_repo.list_all(True)}
    # The split creates its successor series; nothing else appears and the
    # foreign master is never adopted.
    adopted = series_after - series_before
    assert all(
        env.series_store.get_link(uid) is None
        or env.series_store.get_link(uid).remote_event_id != "foreignmaster1"
        for uid in adopted
    )
    catalog_row = env.catalog.get("google", "primary", "foreignmaster1")
    assert catalog_row is not None
    assert not catalog_row.planner_owned
    assert catalog_row.linked_series_uid is None
    assert env.tasks.get_by_google_event_id("foreignmaster1") is None
    env.close()


def _owned(event: CalendarEvent) -> CalendarEvent:
    # Foreign master: carries NO Planner ownership markers at all.
    from dataclasses import replace

    return replace(event, private_extended_properties={})
