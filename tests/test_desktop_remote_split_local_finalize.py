"""Local atomic finalization: partition, history, tags, links, revision."""
from __future__ import annotations

from datetime import timedelta

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.google_series_split import RemoteSeriesSplitStatus
from planner_desktop.domain.series_calendar_link import SeriesLinkStatus
from planner_desktop.storage.tag_repository import SQLiteTagRepository
from planner_desktop.usecases.tag_service import TagService
from tests.remote_split_testkit import (
    START,
    TODAY,
    build_env,
    default_proposal,
    link_series,
    make_series,
    plan_split,
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


def test_partition_preserves_history_and_past_exception(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series(start=TODAY - timedelta(days=5), count=20))
    rows = env.live_rows("src-1")

    # Past completed occurrence and a past synced exception stay put.
    completed = rows[0]
    completed.set_completed(True)
    env.tasks.update(completed)
    past_exception = rows[2]
    seed_instances(env, "src-1", keys={str(past_exception.occurrence_key)})
    assert env.recurrence.edit_occurrence(
        past_exception.uid, _command(past_exception, title="TEST past exc")
    ).ok
    assert env.manual.run_once().ok

    target = next(
        row for row in env.live_rows("src-1")
        if row.start is not None
        and row.start.date() >= TODAY + timedelta(days=2)
    )
    result = env.splits.create_split_plan(
        "src-1", str(target.occurrence_key), default_proposal()
    )
    assert result.ok, result.error
    record = result.record
    summary = env.manual.run_once()
    assert summary.ok and summary.remote_splits_finalized == 1

    plan = env.split_store.get_plan(record.id)
    successor_uid = plan.reserved_successor_series_uid

    # Completed history is never deleted and stays under the source series.
    kept = env.tasks.get_by_uid(completed.uid)
    assert kept is not None and kept.completed
    assert kept.series_uid == "src-1"
    # The past exception row also stays with the source series.
    exc_row = env.tasks.get_by_uid(past_exception.uid)
    assert exc_row is not None and exc_row.is_series_exception
    assert exc_row.series_uid == "src-1"
    # Target-and-future replaceable rows are gone from the source; the
    # materializer regenerates the slots under the successor series.
    source_slots = {
        str(row.occurrence_key) for row in env.live_rows("src-1")
    }
    assert str(target.occurrence_key) not in source_slots
    env.recurrence.ensure_occurrences(TODAY, TODAY + timedelta(days=30))
    successor_rows = env.live_rows(successor_uid)
    assert successor_rows, "successor occurrences must materialize"
    assert all(row.series_uid == successor_uid for row in successor_rows)
    env.close()


def test_links_and_revisions_after_finalization(tmp_path):
    env = build_env(tmp_path)
    source_link = link_series(env, make_series())
    before = env.series_repo.get_by_uid("src-1")
    record, _ = plan_split(env, "src-1")
    assert env.manual.run_once().ok
    plan = env.split_store.get_plan(record.id)

    trimmed = env.series_repo.get_by_uid("src-1")
    assert trimmed.revision == before.revision + 1
    assert trimmed.rule.occurrence_count == 2

    updated_source_link = env.series_store.get_link("src-1")
    assert updated_source_link.id == source_link.id
    assert updated_source_link.link_status is SeriesLinkStatus.SYNCED
    assert updated_source_link.last_synced_payload_hash == (
        plan.source_trimmed_payload_hash
    )
    assert updated_source_link.last_synced_series_revision == trimmed.revision
    assert updated_source_link.remote_etag == plan.source_trimmed_remote_etag

    successor_link = env.series_store.get_link(
        plan.reserved_successor_series_uid
    )
    assert successor_link is not None
    assert successor_link.link_status is SeriesLinkStatus.SYNCED
    assert successor_link.remote_event_id == plan.successor_remote_event_id
    assert successor_link.last_synced_payload_hash == (
        plan.successor_payload_hash
    )
    assert successor_link.link_generation == 0
    # No pending queue rows appeared for either series.
    assert env.series_store.count_pending_ops() == 0
    env.close()


def test_series_tags_copied_to_successor(tmp_path):
    env = build_env(tmp_path)
    tag_repo = SQLiteTagRepository(env.db_path)
    tags = TagService(tag_repo, env.tasks)
    env.recurrence.tag_service = tags
    tag = tags.create("split-tag")

    series = make_series()
    created = env.recurrence.create_series(series, tag_ids=[tag.id])
    assert created.ok
    env.recurrence.ensure_occurrences(START, START + timedelta(days=30))
    assert env.links.connect_to_google("src-1").ok
    assert env.manual.run_once().ok

    record, _ = plan_split(env, "src-1")
    assert env.manual.run_once().ok
    plan = env.split_store.get_plan(record.id)
    assert env.series_repo.tag_ids_for_series(
        plan.reserved_successor_series_uid
    ) == [tag.id]
    # Source tags survive untouched.
    assert env.series_repo.tag_ids_for_series("src-1") == [tag.id]
    tag_repo.close()
    env.close()


def test_revision_drift_prevents_finalization(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    # Simulate an out-of-band revision bump (bypassing the split lock).
    env.series_repo._connection.execute(
        "UPDATE task_series SET revision = revision + 1 WHERE uid = 'src-1'"
    )
    env.series_repo._connection.commit()

    summary = env.manual.run_once()
    assert summary.ok
    assert summary.remote_splits_finalized == 0
    plan = env.split_store.get_plan(record.id)
    assert plan.state is RemoteSeriesSplitStatus.SUCCESSOR_CREATED
    assert "Ревизия" in (plan.last_error or "")
    # No successor TaskSeries or link leaked out of the failed transaction.
    assert env.series_repo.get_by_uid(
        plan.reserved_successor_series_uid
    ) is None
    assert env.series_store.get_link(
        plan.reserved_successor_series_uid
    ) is None
    env.close()


def test_no_ordinary_calendar_operations_are_generated(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    plan_split(env, "src-1")
    assert env.manual.run_once().ok
    assert env.ordinary_store.count_pending_ops() == 0
    # Materialized occurrences were never uploaded as ordinary events.
    assert all(
        event.is_recurring_master
        for event in env.gateway.events
        if not event.is_cancelled
    )
    for uid in ("src-1",):
        for row in env.tasks.list_by_series(uid):
            assert row.google_calendar_event_id is None
    env.close()
