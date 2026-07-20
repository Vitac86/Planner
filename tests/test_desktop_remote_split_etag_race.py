"""ETag/content race protection at every split step (Part 10)."""
from __future__ import annotations

from planner_desktop.domain.google_series_split import (
    RemoteSeriesSplitStatus,
)
from planner_desktop.sync.calendar_series_remote_split_engine import (
    merge_split_master_resource,
)
from tests.remote_split_testkit import (
    build_env,
    link_series,
    make_series,
    plan_split,
)


def _foreign_edit(env, remote_event_id, summary="TEST foreign edit"):
    current = env.gateway.get_recurring_master_resource(remote_event_id)
    foreign = dict(current)
    foreign["summary"] = summary
    return env.gateway.update_recurring_master_full(
        remote_event_id, foreign, expected_etag=str(current.get("etag"))
    )


def test_source_changed_before_trim_no_write(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    _foreign_edit(env, record.source_remote_event_id)
    writes = env.gateway.write_call_count

    summary = env.manual.run_once()
    assert summary.ok
    assert summary.remote_sources_trimmed == 0
    assert summary.remote_split_conflicts >= 1
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.CONFLICT
    # The engine performed zero split writes on the changed master.
    remote = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    assert remote["summary"] == "TEST foreign edit"
    assert "COUNT=5" in remote["recurrence"][0]
    assert env.gateway.write_call_count == writes
    env.close()


def test_source_changed_after_trim_blocks_successor(tmp_path):
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
    env.split_store.mark_source_trimmed(record.id, remote_etag="x")
    _foreign_edit(env, record.source_remote_event_id)

    summary = env.manual.run_once()
    assert summary.ok
    assert summary.remote_successors_created == 0
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.CONFLICT
    # No successor was inserted.
    assert env.gateway.get_recurring_master_resource(
        record.successor_remote_event_id
    ) is None
    env.close()


def test_successor_changed_before_finalize_blocks_local_state(tmp_path):
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
    env.split_store.mark_source_trimmed(record.id, remote_etag="x")
    env.gateway.insert_split_successor_master(
        record.successor_remote_event_id, record.successor_payload
    )
    env.split_store.mark_successor_created(record.id, remote_etag="x")
    _foreign_edit(env, record.successor_remote_event_id)

    summary = env.manual.run_once()
    assert summary.ok
    assert summary.remote_splits_finalized == 0
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.CONFLICT
    # Local state untouched: no successor series, source rule intact.
    assert env.series_repo.get_by_uid(
        plan.reserved_successor_series_uid
    ) is None
    assert env.series_repo.get_by_uid("src-1").rule.occurrence_count == 5
    env.close()


def test_stale_markers_do_not_fake_success(tmp_path):
    """A remote edit keeps the Planner private markers (foreign edits never
    update them); marker equality alone must not count as a completed trim."""
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")

    # Craft a master whose PRIVATE MARKERS look post-trim but whose actual
    # content is still the original rule.
    current = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    trimmed_private = (
        (record.trimmed_source_payload.get("extendedProperties") or {})
        .get("private") or {}
    )
    forged = dict(current)
    forged["extendedProperties"] = {"private": dict(trimmed_private)}
    forged["summary"] = "TEST stale markers"
    env.gateway.update_recurring_master_full(
        record.source_remote_event_id, forged,
        expected_etag=str(current.get("etag")),
    )

    summary = env.manual.run_once()
    assert summary.ok
    # Not reconciled as an already-applied trim: actual content differs.
    assert summary.remote_split_reconciliation_completions == 0
    assert summary.remote_sources_trimmed == 0
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.CONFLICT
    env.close()
