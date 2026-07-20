"""Remote-success/local-failure reconciliation for every split step."""
from __future__ import annotations

from planner_desktop.domain.google_series_split import (
    RemoteSeriesSplitRecoveryKind,
    RemoteSeriesSplitStatus,
)
from planner_desktop.sync.calendar_series_remote_split_engine import (
    CalendarSeriesRemoteSplitEngine,
    merge_split_master_resource,
)
from tests.remote_split_testkit import (
    build_env,
    link_series,
    make_series,
    plan_split,
)


def _engine(env) -> CalendarSeriesRemoteSplitEngine:
    return CalendarSeriesRemoteSplitEngine(
        env.split_store, env.series_repo, env.tasks, env.gateway
    )


def test_trim_already_applied_is_reconciled_without_write(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    source = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    env.gateway.update_recurring_master_full(
        record.source_remote_event_id,
        merge_split_master_resource(source, record.trimmed_source_payload),
        expected_etag=record.source_remote_etag_base,
    )
    writes = env.gateway.write_call_count

    result = _engine(env).process_pending()
    assert result.splits_finalized == 1
    assert result.reconciliation_completions >= 1
    item = result.items[0]
    assert item.recovery is RemoteSeriesSplitRecoveryKind.SOURCE_TRIM_RECONCILED
    # Only the successor insert was still required.
    assert env.gateway.write_call_count == writes + 1
    env.close()


def test_successor_already_created_is_reconciled_without_insert(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    engine = _engine(env)

    # Run trim, then simulate a crash by resetting the plan to
    # source_trimmed AFTER inserting the successor out-of-band.
    source = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    env.gateway.update_recurring_master_full(
        record.source_remote_event_id,
        merge_split_master_resource(source, record.trimmed_source_payload),
        expected_etag=record.source_remote_etag_base,
    )
    env.split_store.mark_source_trimmed(record.id, remote_etag="")
    env.gateway.insert_split_successor_master(
        record.successor_remote_event_id, record.successor_payload
    )
    writes = env.gateway.write_call_count

    result = engine.process_pending()
    assert result.splits_finalized == 1
    assert any(
        item.recovery is (
            RemoteSeriesSplitRecoveryKind.SUCCESSOR_INSERT_RECONCILED
        )
        for item in result.items
    )
    assert env.gateway.write_call_count == writes  # zero extra mutations
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.COMPLETED
    env.close()


def test_rollback_restore_is_reconciled_after_local_failure(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    engine = _engine(env)

    # Trim remotely; then request rollback and simulate that the restore
    # already reached Google while the local rolled_back mark was lost.
    source = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    env.gateway.update_recurring_master_full(
        record.source_remote_event_id,
        merge_split_master_resource(source, record.trimmed_source_payload),
        expected_etag=record.source_remote_etag_base,
    )
    env.split_store.mark_source_trimmed(record.id, remote_etag="")
    env.splits.request_split_rollback(record.id)

    trimmed = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    env.gateway.update_recurring_master_full(
        record.source_remote_event_id,
        merge_split_master_resource(trimmed, record.source_original_snapshot),
        expected_etag=str(trimmed.get("etag")),
    )
    writes = env.gateway.write_call_count

    result = engine.process_pending()
    assert result.rollbacks_completed == 1
    assert any(
        item.recovery is (
            RemoteSeriesSplitRecoveryKind.ROLLBACK_RESTORE_RECONCILED
        )
        for item in result.items
    )
    assert env.gateway.write_call_count == writes  # no duplicate update
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.ROLLED_BACK
    env.close()


def test_rollback_delete_is_reconciled_when_successor_already_gone(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    engine = _engine(env)

    source = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    env.gateway.update_recurring_master_full(
        record.source_remote_event_id,
        merge_split_master_resource(source, record.trimmed_source_payload),
        expected_etag=record.source_remote_etag_base,
    )
    env.split_store.mark_source_trimmed(record.id, remote_etag="")
    # Successor never existed remotely; rollback reconciles the delete step.
    env.splits.request_split_rollback(record.id)
    result = engine.process_pending()
    assert result.rollbacks_completed == 1
    assert any(
        item.recovery in (
            RemoteSeriesSplitRecoveryKind.ROLLBACK_DELETE_RECONCILED,
            RemoteSeriesSplitRecoveryKind.ROLLBACK_RESTORE_RECONCILED,
        )
        for item in result.items
    )
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.ROLLED_BACK
    # The original master content is restored.
    restored = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    assert "COUNT=5" in restored["recurrence"][0]
    env.close()
