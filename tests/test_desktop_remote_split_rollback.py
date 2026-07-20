"""Durable explicit rollback (Part 11)."""
from __future__ import annotations

from planner_desktop.domain.google_series_split import (
    RemoteSeriesSplitStatus,
)
from tests.remote_split_testkit import (
    build_env,
    link_series,
    make_series,
    plan_split,
)


def _run_split_until(env, record, state: RemoteSeriesSplitStatus):
    """Advance the split remotely, then reset the durable state to simulate
    a plan stopped exactly at ``state``."""
    from planner_desktop.sync.calendar_series_remote_split_engine import (
        merge_split_master_resource,
    )

    source = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    trimmed = env.gateway.update_recurring_master_full(
        record.source_remote_event_id,
        merge_split_master_resource(source, record.trimmed_source_payload),
        expected_etag=record.source_remote_etag_base,
    )
    env.split_store.mark_source_trimmed(
        record.id, remote_etag=str(trimmed.get("etag"))
    )
    if state is RemoteSeriesSplitStatus.SOURCE_TRIMMED:
        return
    successor = env.gateway.insert_split_successor_master(
        record.successor_remote_event_id, record.successor_payload
    )
    env.split_store.mark_successor_created(
        record.id, remote_etag=str(successor.get("etag"))
    )


def test_pending_cancellation_needs_zero_google_calls(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    env.gateway.reset_call_counts()
    result = env.splits.request_split_rollback(record.id)
    assert result.ok
    assert result.record.state is RemoteSeriesSplitStatus.ROLLED_BACK
    assert env.gateway.write_call_count == 0
    assert env.gateway.list_call_count == 0
    env.close()


def test_rollback_after_source_trim_restores_original(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    _run_split_until(env, record, RemoteSeriesSplitStatus.SOURCE_TRIMMED)
    assert env.splits.request_split_rollback(record.id).ok

    summary = env.manual.run_once()
    assert summary.ok and summary.remote_split_rollbacks_completed == 1
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.ROLLED_BACK
    restored = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    assert "COUNT=5" in restored["recurrence"][0]
    # No successor master was ever created.
    assert env.gateway.get_recurring_master_resource(
        record.successor_remote_event_id
    ) is None
    # The local series is untouched and unlocked again.
    series = env.series_repo.get_by_uid("src-1")
    assert series.rule.occurrence_count == 5
    assert env.recurrence.update_series("src-1", title="TEST unlocked").ok
    env.close()


def test_rollback_after_successor_deletes_then_restores(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    _run_split_until(env, record, RemoteSeriesSplitStatus.SUCCESSOR_CREATED)
    assert env.splits.request_split_rollback(record.id).ok

    summary = env.manual.run_once()
    assert summary.ok and summary.remote_split_rollbacks_completed == 1
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.ROLLED_BACK
    assert env.gateway.get_recurring_master_resource(
        record.successor_remote_event_id
    ) is None
    restored = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    assert "COUNT=5" in restored["recurrence"][0]
    env.close()


def test_changed_source_prevents_restore_overwrite(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    _run_split_until(env, record, RemoteSeriesSplitStatus.SOURCE_TRIMMED)
    assert env.splits.request_split_rollback(record.id).ok

    # A foreign edit lands on the trimmed source before the rollback runs.
    current = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    foreign = dict(current)
    foreign["summary"] = "TEST foreign edit"
    env.gateway.update_recurring_master_full(
        record.source_remote_event_id, foreign,
        expected_etag=str(current.get("etag")),
    )
    writes = env.gateway.write_call_count

    summary = env.manual.run_once()
    assert summary.ok
    assert summary.remote_split_rollbacks_completed == 0
    assert summary.remote_split_conflicts >= 1
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.CONFLICT
    # Nothing was overwritten.
    assert env.gateway.write_call_count == writes
    remote = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    assert remote["summary"] == "TEST foreign edit"
    env.close()


def test_changed_successor_prevents_rollback_delete(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    _run_split_until(env, record, RemoteSeriesSplitStatus.SUCCESSOR_CREATED)
    assert env.splits.request_split_rollback(record.id).ok

    current = env.gateway.get_recurring_master_resource(
        record.successor_remote_event_id
    )
    foreign = dict(current)
    foreign["summary"] = "TEST foreign successor edit"
    env.gateway.update_recurring_master_full(
        record.successor_remote_event_id, foreign,
        expected_etag=str(current.get("etag")),
    )

    summary = env.manual.run_once()
    assert summary.ok
    assert summary.remote_split_rollbacks_completed == 0
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.CONFLICT
    # The changed successor was NOT deleted.
    survivor = env.gateway.get_recurring_master_resource(
        record.successor_remote_event_id
    )
    assert survivor is not None
    assert survivor["summary"] == "TEST foreign successor edit"
    env.close()
