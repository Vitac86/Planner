"""Eligibility policy: clean series pass; every unsafe state blocks."""
from __future__ import annotations

from datetime import timedelta

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.google_series_split import (
    FutureExceptionSummary,
    RemoteSeriesSplitProposal,
    plan_remote_series_split,
)
from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
)
from tests.remote_split_testkit import (
    TODAY,
    build_env,
    default_proposal,
    link_series,
    make_series,
    seed_instances,
)


def _command(task, **overrides):
    values = dict(
        title=task.title,
        notes=task.notes,
        priority=task.priority,
        completed=task.completed,
        add_to_calendar=True,
        is_all_day=task.is_all_day,
        date_text=task.start.date().isoformat(),
        time_text="" if task.is_all_day else task.start.strftime("%H:%M"),
        duration_text="" if task.is_all_day else str(task.duration_minutes or 30),
    )
    values.update(overrides)
    return TaskEditorCommand(**values)


def test_clean_linked_series_is_eligible(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    rows = env.live_rows("src-1")
    plan, validation = env.splits.validate_split(
        "src-1", str(rows[2].occurrence_key), default_proposal()
    )
    assert validation.ok, validation.errors
    assert plan is not None and plan.occurrences_before_target == 2
    env.close()


def test_first_occurrence_routes_to_whole_series_edit(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    rows = env.live_rows("src-1")
    plan, validation = env.splits.validate_split(
        "src-1", str(rows[0].occurrence_key), default_proposal()
    )
    assert plan is None and not validation.ok
    assert "target_is_first" in validation.codes
    env.close()


def test_past_target_is_rejected(tmp_path):
    env = build_env(tmp_path)
    # Series started well before "today"; the second slot is in the past.
    link_series(env, make_series(start=TODAY - timedelta(days=10), count=30))
    rows = env.live_rows("src-1")
    plan, validation = env.splits.validate_split(
        "src-1", str(rows[1].occurrence_key), default_proposal()
    )
    assert plan is None and "target_in_past" in validation.codes
    env.close()


def test_non_slot_target_is_rejected(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    plan, validation = env.splits.validate_split(
        "src-1", "2026-08-04T23:59@Europe/Moscow", default_proposal()
    )
    assert plan is None and "target_not_slot" in validation.codes
    env.close()


def test_unsupported_rule_is_rejected_by_pure_planner():
    # An invalid weekly rule cannot round-trip to a Google RRULE.
    series = make_series(
        frequency=RecurrenceFrequency.WEEKLY, weekdays=(),
    )
    plan, validation = plan_remote_series_split(
        series,
        source_remote_event_id="plrsrc",
        target_occurrence_key="2026-08-10",
        proposal=RemoteSeriesSplitProposal(),
        future_exceptions=FutureExceptionSummary(),
        today=TODAY,
    )
    assert plan is None
    assert "unsupported_recurrence" in validation.codes


def test_unlinked_and_unsynced_series_are_rejected(tmp_path):
    env = build_env(tmp_path)
    created = env.recurrence.create_series(make_series())
    assert created.ok
    env.recurrence.ensure_occurrences(TODAY, TODAY + timedelta(days=20))
    rows = env.live_rows("src-1")
    _, validation = env.splits.validate_split(
        "src-1", str(rows[2].occurrence_key), default_proposal()
    )
    assert "not_linked" in validation.codes

    # Linked but still pending create (no manual sync yet) is not eligible.
    assert env.links.connect_to_google("src-1").ok
    _, validation = env.splits.validate_split(
        "src-1", str(rows[2].occurrence_key), default_proposal()
    )
    assert "link_not_synced" in validation.codes
    env.close()


def test_pending_master_operation_blocks(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    updated = env.recurrence.update_series("src-1", title="TEST renamed")
    assert updated.ok
    rows = env.live_rows("src-1")
    _, validation = env.splits.validate_split(
        "src-1", str(rows[2].occurrence_key), default_proposal()
    )
    assert not validation.ok
    assert {"link_not_synced", "pending_master_op"} & set(validation.codes)
    env.close()


def test_master_conflict_blocks(tmp_path):
    env = build_env(tmp_path)
    link = link_series(env, make_series())
    env.series_store.record_conflict(
        "src-1", reason="test conflict", remote_etag=link.remote_etag
    )
    rows = env.live_rows("src-1")
    _, validation = env.splits.validate_split(
        "src-1", str(rows[2].occurrence_key), default_proposal()
    )
    assert "link_not_synced" in validation.codes
    env.close()


def test_future_exception_tombstone_and_quarantine_block(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series(count=9))
    rows = env.live_rows("src-1")
    target_key = str(rows[3].occurrence_key)
    seed_instances(env, "src-1", keys={
        str(rows[5].occurrence_key), str(rows[6].occurrence_key),
    })

    # A future (>= target) local exception blocks with its exact date.
    future = rows[5]
    assert env.recurrence.edit_occurrence(
        future.uid, _command(future, title="TEST future exception")
    ).ok
    _, validation = env.splits.validate_split(
        "src-1", target_key, default_proposal()
    )
    assert "future_local_exception" in validation.codes
    assert any(str(future.occurrence_key) in error for error in validation.errors)

    # The pending occurrence op also blocks on its own.
    assert "future_pending_occurrence_op" in validation.codes

    # Drain the queue; the synced remote exception still blocks.
    assert env.manual.run_once().ok
    _, validation = env.splits.validate_split(
        "src-1", target_key, default_proposal()
    )
    assert "future_remote_exception" in validation.codes

    # A future tombstone blocks too.
    doomed = env.live_rows("src-1")[6]
    assert env.recurrence.delete_occurrence(doomed.uid)
    assert env.manual.run_once().ok
    _, validation = env.splits.validate_split(
        "src-1", target_key, default_proposal()
    )
    assert "future_local_tombstone" in validation.codes

    # A remote foreign edit lands in quarantine and blocks as well.
    instance_id = f"inst-src-1-6"
    payload = env.gateway.get_recurring_instance(instance_id)
    payload["summary"] = "TEST foreign remote edit"
    payload["etag"] = '"77"'
    env.gateway.seed_recurring_instance(payload)
    env.pull_engine().pull_remote_changes()
    _, validation = env.splits.validate_split(
        "src-1", target_key, default_proposal()
    )
    assert "future_quarantine" in validation.codes
    env.close()


def test_past_exception_does_not_block(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series(start=TODAY - timedelta(days=5), count=20))
    rows = env.live_rows("src-1")
    past = rows[1]
    seed_instances(env, "src-1", keys={str(past.occurrence_key)})
    # Directly mark the past materialized row as an exception (its slot is
    # before today's target; occurrence edits on past linked slots still
    # enqueue, so drain the queue).
    assert env.recurrence.edit_occurrence(
        past.uid, _command(past, title="TEST past exception")
    ).ok
    assert env.manual.run_once().ok
    target = next(
        row for row in rows
        if row.start is not None and row.start.date() >= TODAY + timedelta(days=2)
    )
    plan, validation = env.splits.validate_split(
        "src-1", str(target.occurrence_key), default_proposal()
    )
    assert validation.ok, validation.errors
    assert plan is not None
    env.close()


def test_duplicate_plan_requests_are_idempotent(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    rows = env.live_rows("src-1")
    key = str(rows[2].occurrence_key)
    first = env.splits.create_split_plan("src-1", key, default_proposal())
    second = env.splits.create_split_plan("src-1", key, default_proposal())
    assert first.ok and second.ok
    assert first.record.id == second.record.id
    assert first.record.reserved_successor_series_uid == (
        second.record.reserved_successor_series_uid
    )
    env.close()


def test_local_drift_between_link_and_series_blocks(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    # Simulate a hash drift: the link acknowledges a different payload hash.
    link = env.series_store.get_link("src-1")
    link.last_synced_payload_hash = "different"
    env.series_store.update_link(link)
    rows = env.live_rows("src-1")
    _, validation = env.splits.validate_split(
        "src-1", str(rows[2].occurrence_key), default_proposal()
    )
    assert "local_not_synced" in validation.codes
    env.close()
