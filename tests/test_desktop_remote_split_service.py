"""Split plan lifecycle: creation, idempotency, locks, cancel, rollback."""
from __future__ import annotations

import json

from planner_desktop.domain.google_series_split import (
    RemoteSeriesSplitStatus,
)
from planner_desktop.domain.series_calendar_link import (
    deterministic_remote_event_id,
)
from planner_desktop.usecases.remote_series_split_service import (
    SPLIT_ACTIVE_LINK_ERROR,
    SPLIT_ACTIVE_SERIES_EDIT_ERROR,
)
from tests.remote_split_testkit import (
    build_env,
    default_proposal,
    link_series,
    make_series,
    plan_split,
)


def test_create_plan_reserves_uid_and_deterministic_remote_id(tmp_path):
    env = build_env(tmp_path)
    link = link_series(env, make_series())
    record, target = plan_split(env, "src-1")

    assert record.state is RemoteSeriesSplitStatus.PENDING
    assert record.source_link_id == link.id
    assert record.source_remote_etag_base == link.remote_etag
    assert record.target_occurrence_key == str(target.occurrence_key)
    assert record.successor_remote_event_id == deterministic_remote_event_id(
        record.reserved_successor_series_uid
    )
    # Complete canonical snapshots are stored in the durable plan.
    trimmed = json.loads(record.source_trimmed_payload_json)
    successor = json.loads(record.successor_payload_json)
    assert "COUNT=2" in trimmed["recurrence"][0]
    assert "COUNT=3" in successor["recurrence"][0]
    snapshot = json.loads(record.successor_series_snapshot_json)
    assert snapshot["uid"] == record.reserved_successor_series_uid
    assert snapshot["title"] == "TEST split successor"
    env.close()


def test_plan_creation_mutates_no_local_series_and_makes_no_calls(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    env.gateway.reset_call_counts()
    before_series = env.series_repo.get_by_uid("src-1")
    record, _ = plan_split(env, "src-1")
    after_series = env.series_repo.get_by_uid("src-1")
    assert env.gateway.write_call_count == 0
    assert env.gateway.list_call_count == 0
    assert after_series.rule == before_series.rule
    assert after_series.revision == before_series.revision
    # The successor TaskSeries is NOT created at plan time.
    assert env.series_repo.get_by_uid(
        record.reserved_successor_series_uid
    ) is None
    env.close()


def test_active_plan_locks_series_and_link_operations(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, target = plan_split(env, "src-1")

    blocked = env.recurrence.update_series("src-1", title="nope")
    assert not blocked.ok
    assert SPLIT_ACTIVE_SERIES_EDIT_ERROR in blocked.errors

    assert not env.recurrence.stop_series("src-1").ok
    assert not env.recurrence.delete_series("src-1").ok

    disconnect = env.links.disconnect_keep_remote("src-1")
    assert not disconnect.ok and disconnect.error == SPLIT_ACTIVE_LINK_ERROR
    assert not env.links.request_remote_delete_keep_local("src-1").ok
    assert not env.links.request_delete_local_and_remote("src-1").ok

    # Another split cannot start; the same active plan is returned.
    duplicate = env.splits.create_split_plan(
        "src-1", str(target.occurrence_key), default_proposal()
    )
    assert duplicate.ok and duplicate.record.id == record.id
    env.close()


def test_occurrence_schedule_locks_only_at_or_after_target(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series(count=6))
    record, target = plan_split(env, "src-1", target_index=3)
    rows = env.live_rows("src-1")

    assert env.splits.is_occurrence_locked("src-1", rows[4].occurrence_key)
    assert env.splits.is_occurrence_locked("src-1", target.occurrence_key)
    assert not env.splits.is_occurrence_locked("src-1", rows[0].occurrence_key)

    # Schedule operations on future rows are blocked.
    assert not env.recurrence.delete_occurrence(rows[4].uid)
    # Local completion stays allowed while the split is pending.
    future = rows[4]
    future.set_completed(True)
    env.tasks.update(future)
    assert env.tasks.get_by_uid(future.uid).completed
    env.close()


def test_cancel_unstarted_plan_is_local_only(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    env.gateway.reset_call_counts()
    result = env.splits.cancel_unstarted_split(record.id)
    assert result.ok
    assert result.record.state is RemoteSeriesSplitStatus.ROLLED_BACK
    assert env.gateway.write_call_count == 0
    assert env.gateway.list_call_count == 0
    # The series is unlocked again.
    assert env.recurrence.update_series("src-1", title="TEST unlocked").ok
    env.close()


def test_rollback_request_transitions(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")

    # pending -> local cancellation via the rollback entry point too.
    result = env.splits.request_split_rollback(record.id)
    assert result.ok
    assert result.record.state is RemoteSeriesSplitStatus.ROLLED_BACK

    # partial states -> durable rollback_pending.
    record2, _ = plan_split(env, "src-1")
    env.split_store.mark_source_trimmed(record2.id, remote_etag='"9"')
    result = env.splits.request_split_rollback(record2.id)
    assert result.ok
    assert result.record.state is RemoteSeriesSplitStatus.ROLLBACK_PENDING
    env.close()


def test_retry_rearms_processable_plan(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    env.split_store.record_attempt_error(record.id, "transient")
    result = env.splits.retry_split(record.id)
    assert result.ok
    assert result.record.last_error is None

    env.split_store.mark_conflict(record.id, "conflict")
    result = env.splits.retry_split(record.id)
    assert not result.ok  # conflict requires explicit rollback/cancel
    env.close()


def test_conflict_resolution_allowed_only_when_split_is_conflicted(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    assert not env.splits.allows_conflict_resolution("src-1")
    env.split_store.mark_conflict(record.id, "x")
    assert env.splits.allows_conflict_resolution("src-1")
    env.close()
