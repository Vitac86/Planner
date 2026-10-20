"""Deterministic background-sync races using independent UI/worker connections."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from planner_desktop.domain.task import Task, utc_now
from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.sync.sync_types import RetryableGatewayError, TerminalGatewayError
from planner_desktop.usecases.task_service import DesktopTaskService


@pytest.fixture()
def sync(tmp_path):
    path = tmp_path / "concurrent.db"
    repo, ui_repo = SQLiteTaskRepository(path), SQLiteTaskRepository(path)
    store, ui_store = CalendarSyncStore(path), CalendarSyncStore(path)
    gateway = FakeCalendarGateway(base_time=utc_now() + timedelta(minutes=5))
    engine = CalendarSyncEngine(repo, store, gateway)
    service = DesktopTaskService(ui_repo, ui_store)
    task = service.create_task(Task(
        title="Original", start=datetime(2026, 7, 8, 10),
        end=datetime(2026, 7, 8, 11),
    ))
    yield SimpleNamespace(
        repo=repo, ui_repo=ui_repo, store=store, ui_store=ui_store,
        gateway=gateway, engine=engine, service=service, task=task,
    )
    store.close()
    ui_store.close()
    repo.close()
    ui_repo.close()


def after_gateway_reply(monkeypatch, sync, method, change):
    original = getattr(sync.gateway, method)

    def reply(*args, **kwargs):
        result = original(*args, **kwargs)
        change()
        return result

    monkeypatch.setattr(sync.gateway, method, reply)


def edit(sync, title="New local title"):
    task = sync.ui_repo.get(sync.task.id)
    task.title = title
    sync.service.update_task(task)


@pytest.mark.parametrize("phase", ["insert_event", "patch_event"])
def test_edit_during_push_preserves_local_fields_and_pending_generation(sync, monkeypatch, phase):
    if phase == "patch_event":
        sync.engine.sync_once()
        edit(sync, "Sent title")
    old_op_id = sync.store.list_due_ops()[0].id
    after_gateway_reply(monkeypatch, sync, phase, lambda: edit(sync))

    sync.engine.sync_once()

    local = sync.ui_repo.get(sync.task.id)
    assert local.title == "New local title"
    assert local.google_calendar_event_id is not None
    assert sync.store.has_pending_op(local.uid)
    assert all(op.id != old_op_id for op in sync.store.list_due_ops())
    monkeypatch.undo()
    sync.engine.sync_once()
    assert sync.gateway.get_event(local.google_calendar_event_id).summary == local.title
    assert sync.store.count_pending_ops() == 0
    assert len(sync.gateway.events) == 1


@pytest.mark.parametrize("phase", ["insert_event", "patch_event"])
@pytest.mark.parametrize("action", ["delete", "unschedule"])
def test_delete_or_unschedule_during_push_never_resurrects_task(sync, monkeypatch, phase, action):
    if phase == "patch_event":
        sync.engine.sync_once()
        edit(sync, "Sent title")
    change = (lambda: sync.service.delete_task(sync.task.id)) if action == "delete" else (
        lambda: sync.service.unschedule_task(sync.task.uid)
    )
    after_gateway_reply(monkeypatch, sync, phase, change)

    sync.engine.sync_once()

    local = sync.ui_repo.get(sync.task.id)
    assert local.is_deleted == (action == "delete")
    if action == "unschedule":
        assert local.start is None
        assert local.google_calendar_event_id is None
        assert len(sync.ui_repo.list_all()) == 1  # pull did not reimport the late insert
    assert sync.store.has_pending_op(local.uid)
    monkeypatch.undo()
    sync.engine.sync_once()
    assert sync.store.count_pending_ops() == 0
    assert all(event.is_cancelled for event in sync.gateway.events)
    assert len(sync.ui_repo.list_all()) == (0 if action == "delete" else 1)


def test_completion_during_create_survives_metadata_reply(sync, monkeypatch):
    after_gateway_reply(
        monkeypatch, sync, "insert_event", lambda: sync.service.complete_task(sync.task.id),
    )
    sync.engine.sync_once()
    assert sync.ui_repo.get(sync.task.id).completed
    assert sync.ui_repo.get(sync.task.id).completed_at is not None


@pytest.mark.parametrize("error", [RetryableGatewayError, TerminalGatewayError])
def test_failure_of_old_push_cannot_delay_or_dead_letter_new_edit(sync, monkeypatch, error):
    old_id = sync.store.list_due_ops()[0].id

    def fail(*args, **kwargs):
        edit(sync)
        raise error("Old request failed")

    monkeypatch.setattr(sync.gateway, "insert_event", fail)
    sync.engine.push_pending()
    pending = sync.store.list_due_ops()
    assert len(pending) == 1
    assert pending[0].id != old_id
    assert pending[0].attempts == 0
    assert pending[0].last_error is None
    assert sync.store.count_terminal_ops() == 0


@pytest.mark.parametrize("cancelled", [False, True])
@pytest.mark.parametrize("local_change", ["edit", "delete", "queue_only"])
def test_pull_cannot_write_between_local_read_and_atomic_compare(sync, monkeypatch, cancelled, local_change):
    sync.engine.sync_once()
    event_id = sync.ui_repo.get(sync.task.id).google_calendar_event_id
    if cancelled:
        sync.gateway.delete_event(event_id)
    else:
        sync.gateway.patch_event(event_id, {"summary": "Remote edit"})
    original = sync.repo.apply_calendar_update

    def interleave(task, expected):
        if local_change == "edit":
            edit(sync)
        elif local_change == "delete":
            sync.service.delete_task(sync.task.id)
        else:
            sync.ui_store.enqueue_update(sync.task.uid)
        return original(task, expected)

    monkeypatch.setattr(sync.repo, "apply_calendar_update", interleave)
    sync.engine.pull_remote_changes()
    local = sync.ui_repo.get(sync.task.id)
    assert local.is_deleted == (local_change == "delete")
    assert local.title == ("New local title" if local_change == "edit" else "Original")
    assert sync.store.has_pending_op(sync.task.uid)


def test_delete_payload_targets_old_event_after_task_gets_new_link(sync):
    sync.engine.sync_once()
    original_id = sync.ui_repo.get(sync.task.id).google_calendar_event_id
    new_event = sync.gateway.insert_event(sync.gateway.get_event(original_id))
    current = sync.ui_repo.get(sync.task.id)
    current.google_calendar_event_id = new_event.id
    sync.ui_repo.update(current)
    sync.ui_store.enqueue_delete(current.uid, payload={"event_id": original_id})

    sync.engine.push_pending()

    assert sync.gateway.get_event(original_id).is_cancelled
    assert not sync.gateway.get_event(new_event.id).is_cancelled


def test_queue_coalesces_updates_but_old_acknowledgement_cannot_remove_new_work(sync):
    old = sync.store.list_due_ops()[0]
    sync.ui_store.enqueue_update(sync.task.uid)
    new = sync.store.list_due_ops()[0]
    assert new.op == "create"
    assert new.id != old.id
    sync.store.remove_op(old.id)
    assert sync.store.list_due_ops()[0].id == new.id


def test_distinct_detached_event_deletions_are_retained(sync):
    sync.ui_store.enqueue_delete(sync.task.uid, {"event_id": "old-a"})
    sync.ui_store.enqueue_delete(sync.task.uid, {"event_id": "old-b"})
    assert sync.store.has_event_delete("old-a")
    assert sync.store.has_event_delete("old-b")
    assert sync.store.count_pending_ops() == 2


def test_failed_local_update_is_not_overwritten_by_pull(sync):
    sync.engine.sync_once()
    event_id = sync.ui_repo.get(sync.task.id).google_calendar_event_id
    sync.gateway.patch_event(event_id, {"summary": "Remote change"})
    edit(sync)
    sync.gateway.fail_next(TerminalGatewayError("Permission denied"))

    sync.engine.sync_once()

    assert sync.ui_repo.get(sync.task.id).title == "New local title"
    assert sync.store.count_terminal_ops() == 1
    edit(sync, "Corrected local title")
    sync.engine.sync_once()
    assert sync.gateway.get_event(event_id).summary == "Corrected local title"
    assert sync.store.count_terminal_ops() == 0


def test_local_save_keeps_binding_received_after_editor_opened(sync):
    stale_editor_task = sync.ui_repo.get(sync.task.id)
    sync.engine.sync_once()
    synced = sync.repo.get(sync.task.id)
    assert stale_editor_task.google_calendar_event_id is None
    stale_editor_task.title = "Edited while syncing"

    sync.ui_repo.update_local(stale_editor_task)

    local = sync.repo.get(sync.task.id)
    assert local.title == "Edited while syncing"
    assert local.google_calendar_event_id == synced.google_calendar_event_id
    assert local.google_calendar_etag == synced.google_calendar_etag


def test_cancelling_new_create_keeps_cleanup_of_previous_event(sync):
    sync.ui_store.enqueue_event_cleanup(sync.task.uid, "previous-event")
    sync.ui_store.cancel_pending_ops(sync.task.uid)
    remaining = sync.store.list_due_ops()
    assert len(remaining) == 1
    assert remaining[0].op == "delete"
    assert sync.store.has_event_delete("previous-event")


@pytest.mark.parametrize("through_editor", [False, True])
def test_unschedule_detaches_event_created_after_initial_ui_read(sync, monkeypatch, through_editor):
    original = sync.ui_repo.detach_calendar_task

    def attach_before_detach(task):
        sync.engine.sync_once()
        return original(task)

    monkeypatch.setattr(sync.ui_repo, "detach_calendar_task", attach_before_detach)
    result = (
        sync.service.edit_task(sync.task.uid, TaskEditorCommand(
            title="Edited while removing date", add_to_calendar=False,
        )) if through_editor else sync.service.unschedule_task(sync.task.uid)
    )
    assert result.ok
    local = sync.ui_repo.get(sync.task.id)
    assert local.start is None
    assert local.google_calendar_event_id is None
    assert sync.store.count_pending_ops() == 1

    sync.engine.sync_once()

    assert all(event.is_cancelled for event in sync.gateway.events)
    assert len(sync.ui_repo.list_all()) == 1


def test_late_acknowledgement_cannot_remove_rollback_operation(sync, monkeypatch):
    sync.engine.sync_once()
    edit(sync, "Saved before failed edit")
    saved = sync.ui_repo.get(sync.task.id)
    snapshot = sync.ui_store.snapshot_task_ops(sync.task.uid)
    failed = sync.ui_repo.get(sync.task.id)
    failed.title = "Edit whose enqueue failed"
    sync.ui_repo.update_local(failed)
    original = sync.engine._push_op

    def push_then_rollback(op):
        original(op)
        sync.ui_store.restore_task_ops(sync.task.uid, snapshot)
        sync.ui_repo.update_local(saved)

    monkeypatch.setattr(sync.engine, "_push_op", push_then_rollback)
    sync.engine.push_pending()

    pending = sync.store.list_due_ops()
    assert len(pending) == 1
    assert pending[0].id != snapshot[0]["id"]
    monkeypatch.undo()
    sync.engine.sync_once()
    local = sync.ui_repo.get(sync.task.id)
    assert sync.gateway.get_event(local.google_calendar_event_id).summary == saved.title
    assert sync.store.count_pending_ops() == 0


def test_queue_rollback_retains_concurrent_orphan_cleanup(sync):
    snapshot = sync.ui_store.snapshot_task_ops(sync.task.uid)
    sync.store.enqueue_event_cleanup(sync.task.uid, "orphan-from-late-reply")
    sync.ui_store.restore_task_ops(sync.task.uid, snapshot)
    assert sync.store.has_event_delete("orphan-from-late-reply")
    pending = sync.store.list_due_ops()
    assert len(pending) == 2
    assert all(op.id != snapshot[0]["id"] for op in pending)
