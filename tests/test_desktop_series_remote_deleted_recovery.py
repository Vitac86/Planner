from datetime import datetime

from planner_desktop.domain.series_conflict_resolution import (
    deterministic_remote_event_id_for_generation,
)
from planner_desktop.domain.task import Task
from planner_desktop.usecases.series_conflict_service import (
    CONFIRMATION_REQUIRED_ERROR,
)
from tests.test_desktop_series_conflict_service import make_stack


def _delete_remote(stack):
    stack.gateway.delete_recurring_master(stack.remote_id)
    stack.pull.pull_remote_changes()
    assert stack.store.get_link("s1").link_status.value == "remote_deleted"


def test_keep_local_detaches_dead_link_and_keeps_series(tmp_path):
    stack = make_stack(tmp_path)
    completed = stack.tasks.add(Task(
        title="Done", start=datetime(2026, 7, 13, 9),
        end=datetime(2026, 7, 13, 9, 30), series_uid="s1",
        occurrence_key="2026-07-13T09:00@Europe/Moscow", completed=True,
    ))
    _delete_remote(stack)
    result = stack.conflicts.recover_remote_deleted_keep_local("s1")
    assert result.ok
    link = stack.store.get_link("s1", include_detached=True)
    assert link.link_status.value == "detached"
    assert link.resolution_kind == "keep_local"
    series = stack.series_repo.get_by_uid("s1")
    assert series.active and not series.is_deleted
    assert stack.tasks.get_by_uid(completed.uid).completed
    stack.store.close(); stack.ordinary.close()


def test_recreate_requires_confirmation_and_remote_deleted_state(tmp_path):
    stack = make_stack(tmp_path)
    refused = stack.conflicts.recover_remote_deleted_recreate(
        "s1", confirmed=True
    )
    assert not refused.ok  # link is synced, not remote_deleted
    _delete_remote(stack)
    unconfirmed = stack.conflicts.recover_remote_deleted_recreate("s1")
    assert not unconfirmed.ok
    assert unconfirmed.error == CONFIRMATION_REQUIRED_ERROR
    assert stack.store.count_pending_ops() == 0
    stack.store.close(); stack.ordinary.close()


def test_recreate_opens_generation_one_with_new_stable_id(tmp_path):
    stack = make_stack(tmp_path)
    _delete_remote(stack)
    result = stack.conflicts.recover_remote_deleted_recreate(
        "s1", confirmed=True
    )
    assert result.ok and result.changed
    new_link = stack.store.get_link("s1")
    assert new_link.link_status.value == "pending_create"
    assert new_link.link_generation == 1
    expected_id = deterministic_remote_event_id_for_generation("s1", 1)
    assert new_link.remote_event_id == expected_id
    assert new_link.remote_event_id != stack.remote_id
    # Exactly one CREATE queued, carrying the audit id.
    ops = stack.store.list_ops()
    assert len(ops) == 1
    assert ops[0].op.value == "create"
    assert ops[0].remote_event_id == expected_id
    assert ops[0].resolution_id == result.resolution.id
    # The old remote_deleted generation is preserved as history.
    history = stack.store.list_links(include_detached=True)
    old = [item for item in history if item.link_generation == 0][0]
    assert old.link_status.value == "detached"
    assert old.resolution_kind == "recreate"
    assert old.remote_event_id == stack.remote_id
    stack.store.close(); stack.ordinary.close()


def test_repeated_recreate_calls_do_not_mint_more_generations(tmp_path):
    stack = make_stack(tmp_path)
    _delete_remote(stack)
    first = stack.conflicts.recover_remote_deleted_recreate("s1", confirmed=True)
    second = stack.conflicts.recover_remote_deleted_recreate("s1", confirmed=True)
    third = stack.conflicts.recover_remote_deleted_recreate("s1", confirmed=True)
    assert first.ok and first.changed
    assert second.ok and not second.changed
    assert third.ok and not third.changed
    assert second.link.id == first.link.id
    assert stack.store.max_link_generation("s1") == 1
    assert stack.store.count_pending_ops() == 1
    generations = [
        item.link_generation
        for item in stack.store.list_links(include_detached=True)
    ]
    assert sorted(generations) == [0, 1]
    stack.store.close(); stack.ordinary.close()


def test_successful_fake_create_syncs_new_generation(tmp_path):
    stack = make_stack(tmp_path)
    _delete_remote(stack)
    result = stack.conflicts.recover_remote_deleted_recreate("s1", confirmed=True)
    push = stack.engine.push_pending()
    assert push.created == 1
    assert push.remote_deleted_recreated == 1
    new_link = stack.store.get_link("s1")
    assert new_link.link_status.value == "synced"
    assert new_link.link_generation == 1
    remote = stack.gateway.get_recurring_master(new_link.remote_event_id)
    assert remote is not None
    assert remote.summary == "Local authoritative"
    audit = stack.store.get_resolution(result.resolution.id)
    assert audit.status == "completed"
    assert stack.store.count_pending_ops() == 0
    # Old master id remains dead; only one live master exists.
    assert stack.gateway.get_recurring_master(stack.remote_id) is None
    live_masters = [
        event for event in stack.gateway.events
        if event.is_recurring_master and not event.is_cancelled
    ]
    assert len(live_masters) == 1
    stack.store.close(); stack.ordinary.close()


def test_delete_local_preserves_completed_history_without_google_ops(tmp_path):
    stack = make_stack(tmp_path)
    completed = stack.tasks.add(Task(
        title="Done", start=datetime(2026, 7, 13, 9),
        end=datetime(2026, 7, 13, 9, 30), series_uid="s1",
        occurrence_key="2026-07-13T09:00@Europe/Moscow", completed=True,
    ))
    future = stack.tasks.add(Task(
        title="Future", start=datetime(2026, 7, 16, 9),
        end=datetime(2026, 7, 16, 9, 30), series_uid="s1",
        occurrence_key="2026-07-16T09:00@Europe/Moscow",
    ))
    _delete_remote(stack)
    writes = stack.gateway.write_call_count
    refused = stack.conflicts.delete_remote_deleted_local_series("s1")
    assert not refused.ok and refused.error == CONFIRMATION_REQUIRED_ERROR
    result = stack.conflicts.delete_remote_deleted_local_series(
        "s1", confirmed=True
    )
    assert result.ok
    # No Google operation: the master is already absent.
    assert stack.gateway.write_call_count == writes
    assert stack.store.count_pending_ops() == 0
    assert stack.series_repo.get_by_uid("s1").is_deleted
    assert stack.tasks.get_by_uid(completed.uid).completed
    assert stack.tasks.get_by_uid(future.uid) is None
    link = stack.store.get_link("s1", include_detached=True)
    assert link.link_status.value == "detached"
    assert link.resolution_kind == "delete_local"
    stack.store.close(); stack.ordinary.close()
