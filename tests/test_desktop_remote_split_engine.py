"""Remote split engine: trim, insert, finalize, retry, manual stats."""
from __future__ import annotations

from datetime import time, timedelta

from planner_desktop.domain.google_series_split import (
    RemoteSeriesSplitStatus,
)
from tests.remote_split_testkit import (
    START,
    build_env,
    default_proposal,
    link_series,
    make_series,
    plan_split,
)


def test_full_split_in_one_manual_sync_with_stats(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(
        env, "src-1",
        proposal=default_proposal(local_time=time(11)),
    )
    env.gateway.reset_call_counts()

    summary = env.manual.run_once()
    assert summary.ok
    assert summary.remote_splits_started == 1
    assert summary.remote_sources_trimmed == 1
    assert summary.remote_successors_created == 1
    assert summary.remote_splits_finalized == 1
    assert summary.remote_split_conflicts == 0
    assert summary.remote_split_terminal == 0
    # Exactly one source update and one successor insert.
    assert env.gateway.write_call_count == 2

    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.COMPLETED
    assert plan.completed_at is not None

    source = env.gateway.get_recurring_master_resource(
        plan.source_remote_event_id
    )
    successor = env.gateway.get_recurring_master_resource(
        plan.successor_remote_event_id
    )
    assert "COUNT=2" in source["recurrence"][0]
    assert "COUNT=3" in successor["recurrence"][0]
    assert successor["start"]["dateTime"].startswith("2026-08-05T11:00")
    env.close()


def test_unchanged_retry_sync_makes_zero_writes(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    plan_split(env, "src-1")
    assert env.manual.run_once().ok
    env.gateway.reset_call_counts()
    second = env.manual.run_once()
    assert second.ok
    assert env.gateway.write_call_count == 0
    assert second.remote_splits_started == 0
    assert second.remote_splits_finalized == 0
    env.close()


def test_local_finalize_failure_retries_only_local_transaction(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")

    original = env.split_store.finalize_linked_remote_split_atomic
    calls = {"n": 0}

    def failing_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated local persistence failure")
        return original(*args, **kwargs)

    env.split_store.finalize_linked_remote_split_atomic = failing_once
    first = env.manual.run_once()
    assert first.ok
    assert first.remote_sources_trimmed == 1
    assert first.remote_successors_created == 1
    assert first.remote_splits_finalized == 0
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.SUCCESSOR_CREATED
    remote_writes = env.gateway.write_call_count

    second = env.manual.run_once()
    assert second.ok
    assert second.remote_splits_finalized == 1
    assert second.remote_split_reconciliation_completions == 1
    # No further remote update or insert was issued.
    assert env.gateway.write_call_count == remote_writes
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.COMPLETED
    env.close()


def test_no_duplicate_successor_after_remote_success_replay(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")

    # Simulate a crash after both remote writes but before any local
    # transition: apply both writes out-of-band, keep the plan pending.
    source = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    from planner_desktop.sync.calendar_series_remote_split_engine import (
        merge_split_master_resource,
    )

    env.gateway.update_recurring_master_full(
        record.source_remote_event_id,
        merge_split_master_resource(source, record.trimmed_source_payload),
        expected_etag=record.source_remote_etag_base,
    )
    env.gateway.insert_split_successor_master(
        record.successor_remote_event_id, record.successor_payload
    )
    writes_before = env.gateway.write_call_count

    summary = env.manual.run_once()
    assert summary.ok
    assert summary.remote_splits_finalized == 1
    assert summary.remote_split_reconciliation_completions >= 2
    # Reconciliation issued zero additional mutations.
    assert env.gateway.write_call_count == writes_before
    masters = [
        event for event in env.gateway.events
        if event.is_recurring_master and not event.is_cancelled
    ]
    assert len(masters) == 2
    env.close()


def test_completed_split_lets_both_series_sync_independently(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    assert env.manual.run_once().ok
    plan = env.split_store.get_plan(record.id)
    successor_uid = plan.reserved_successor_series_uid
    env.recurrence.ensure_occurrences(START, START + timedelta(days=10))

    # Ordinary master updates run through the normal B2 queue afterwards.
    assert env.recurrence.update_series("src-1", title="TEST source renamed").ok
    assert env.recurrence.update_series(
        successor_uid, title="TEST successor renamed"
    ).ok
    summary = env.manual.run_once()
    assert summary.ok and summary.series_masters_updated == 2
    source = env.gateway.get_recurring_master_resource(
        plan.source_remote_event_id
    )
    successor = env.gateway.get_recurring_master_resource(
        plan.successor_remote_event_id
    )
    assert source["summary"] == "TEST source renamed"
    assert successor["summary"] == "TEST successor renamed"
    env.close()
