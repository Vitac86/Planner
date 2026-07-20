"""Pull behaviour during an active split (Part 12)."""
from __future__ import annotations

import pytest

from planner_desktop.domain.google_series_split import (
    RemoteSeriesSplitStatus,
)
from planner_desktop.domain.series_calendar_link import SeriesLinkStatus
from planner_desktop.sync.calendar_series_remote_split_engine import (
    merge_split_master_resource,
)
from tests.remote_split_testkit import (
    build_env,
    link_series,
    make_series,
    plan_split,
)


def test_expected_split_echoes_are_not_conflicts(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    pull = env.pull_engine()
    pull.pull_remote_changes()  # establish cursor

    # Trim the source remotely (as the engine would); pulling the echo must
    # neither mark the LINK conflicted nor the plan.
    source = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    trimmed = env.gateway.update_recurring_master_full(
        record.source_remote_event_id,
        merge_split_master_resource(source, record.trimmed_source_payload),
        expected_etag=record.source_remote_etag_base,
    )
    env.split_store.mark_source_trimmed(record.id, remote_etag="old")
    successor = env.gateway.insert_split_successor_master(
        record.successor_remote_event_id, record.successor_payload
    )
    env.split_store.mark_successor_created(record.id, remote_etag="old")

    pulled = pull.pull_remote_changes()
    assert pulled >= 2
    assert pull.last_pull_stats.split_conflicts_detected == 0
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.SUCCESSOR_CREATED
    # Expected echoes refresh the acknowledged plan ETags.
    assert plan.source_trimmed_remote_etag == str(trimmed.get("etag"))
    assert plan.successor_remote_etag == str(successor.get("etag"))
    link = env.series_store.get_link("src-1")
    assert link.link_status is SeriesLinkStatus.SYNCED
    env.close()


def test_successor_never_becomes_task_or_unowned_external_master(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    pull = env.pull_engine()
    pull.pull_remote_changes()

    env.gateway.insert_split_successor_master(
        record.successor_remote_event_id, record.successor_payload
    )
    env.split_store.mark_source_trimmed(record.id, remote_etag="x")
    env.split_store.mark_successor_created(record.id, remote_etag="x")
    tasks_before = len(
        [t for t in env.tasks.list_all_including_deleted()]
        if hasattr(env.tasks, "list_all_including_deleted") else []
    )
    pull.pull_remote_changes()

    # No ordinary Task was created for the successor master.
    assert env.tasks.get_by_google_event_id(
        record.successor_remote_event_id
    ) is None
    # No unowned external catalog row appeared for it during the split.
    catalog_row = env.catalog.get(
        "google", "primary", record.successor_remote_event_id
    )
    assert catalog_row is None or catalog_row.planner_owned
    _ = tasks_before
    env.close()


def test_unexpected_source_change_marks_plan_conflict_on_pull(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    pull = env.pull_engine()
    pull.pull_remote_changes()

    current = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    foreign = dict(current)
    foreign["summary"] = "TEST foreign master edit"
    env.gateway.update_recurring_master_full(
        record.source_remote_event_id, foreign,
        expected_etag=str(current.get("etag")),
    )
    pull.pull_remote_changes()
    assert pull.last_pull_stats.split_conflicts_detected == 1
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.CONFLICT
    env.close()


def test_cancelled_source_recorded_without_recreation(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    pull = env.pull_engine()
    pull.pull_remote_changes()
    env.gateway.delete_recurring_master(record.source_remote_event_id)
    writes = env.gateway.write_call_count
    pull.pull_remote_changes()
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.CONFLICT
    assert env.gateway.write_call_count == writes  # no automatic recreation
    env.close()


def test_persistence_failure_prevents_cursor_advancement(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    pull = env.pull_engine()
    pull.pull_remote_changes()
    cursor_before = env.ordinary_store.get_sync_cursor()

    current = env.gateway.get_recurring_master_resource(
        record.source_remote_event_id
    )
    foreign = dict(current)
    foreign["summary"] = "TEST foreign master edit"
    env.gateway.update_recurring_master_full(
        record.source_remote_event_id, foreign,
        expected_etag=str(current.get("etag")),
    )

    original = env.split_store.mark_conflict

    def failing(*args, **kwargs):
        raise RuntimeError("simulated split persistence failure")

    env.split_store.mark_conflict = failing
    with pytest.raises(RuntimeError):
        pull.pull_remote_changes()
    assert env.ordinary_store.get_sync_cursor() == cursor_before
    env.split_store.mark_conflict = original
    # The retry applies the change and only then advances the cursor.
    pull.pull_remote_changes()
    assert env.ordinary_store.get_sync_cursor() != cursor_before
    assert env.split_store.get_plan(record.id).state is (
        RemoteSeriesSplitStatus.CONFLICT
    )
    env.close()


def test_completed_split_masters_use_normal_pull_rules(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    assert env.manual.run_once().ok
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.COMPLETED

    # A foreign edit on the successor now triggers ORDINARY B3A conflict
    # handling on its own link, not the split plan.
    current = env.gateway.get_recurring_master_resource(
        plan.successor_remote_event_id
    )
    foreign = dict(current)
    foreign["summary"] = "TEST post-split foreign edit"
    env.gateway.update_recurring_master_full(
        plan.successor_remote_event_id, foreign,
        expected_etag=str(current.get("etag")),
    )
    summary = env.manual.run_once()
    assert summary.ok
    successor_link = env.series_store.get_link(
        plan.reserved_successor_series_uid
    )
    assert successor_link.link_status is SeriesLinkStatus.CONFLICT
    assert env.split_store.get_plan(record.id).state is (
        RemoteSeriesSplitStatus.COMPLETED
    )
    env.close()
