"""User undo must remain safe while ordinary Calendar sync is in flight."""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.task import Task, utc_now
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.storage.tag_repository import SQLiteTagRepository
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.bulk_task_service import (
    ACTION_ADD_TAG, ACTION_COMPLETE, ACTION_DELETE, BulkTaskService,
)
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.usecases.tag_service import TagService


@pytest.fixture()
def stack(tmp_path):
    path = tmp_path / "undo.db"
    ui_repo, worker_repo = SQLiteTaskRepository(path), SQLiteTaskRepository(path)
    ui_queue, worker_queue = CalendarSyncStore(path), CalendarSyncStore(path)
    gateway = FakeCalendarGateway(base_time=utc_now() + timedelta(minutes=5))
    service = DesktopTaskService(ui_repo, ui_queue)
    engine = CalendarSyncEngine(worker_repo, worker_queue, gateway)
    task = service.create_task(Task(
        title="Original", notes="Original notes",
        start=datetime(2026, 10, 22, 10), end=datetime(2026, 10, 22, 11),
        duration_minutes=60,
    ))
    service.undo_service.clear()
    yield SimpleNamespace(
        ui_repo=ui_repo, worker_repo=worker_repo,
        ui_queue=ui_queue, worker_queue=worker_queue,
        service=service, undo=service.undo_service,
        gateway=gateway, engine=engine, task=task,
    )
    worker_queue.close()
    ui_queue.close()
    worker_repo.close()
    ui_repo.close()


def edit_command(title, **changes):
    values = dict(
        title=title, notes="Original notes", add_to_calendar=True,
        date_text="2026-10-22", time_text="10:00", duration_text="60",
        priority=0, completed=False,
    )
    values.update(changes)
    return TaskEditorCommand(**values)


def active_remote(stack):
    return [event for event in stack.gateway.events if not event.is_cancelled]


@pytest.mark.parametrize("phase", ["before_first_sync", "delete_queued", "delete_sent"])
def test_undo_delete_restores_task_and_valid_calendar_binding(stack, phase):
    if phase != "before_first_sync":
        stack.engine.sync_once()
    original_event_id = stack.ui_repo.get(stack.task.id).google_calendar_event_id
    assert stack.service.delete_task(stack.task.id)
    if phase == "delete_sent":
        stack.engine.sync_once()
        assert stack.gateway.get_event(original_event_id).is_cancelled

    result = stack.undo.undo()

    assert result.ok
    assert result.restored_count == 1
    assert result.failed_count == 0
    restored = stack.ui_repo.get(stack.task.id)
    assert not restored.is_deleted
    assert restored.title == "Original"
    stack.engine.sync_once()
    restored = stack.ui_repo.get(stack.task.id)
    assert len(active_remote(stack)) == 1
    assert stack.gateway.get_event(restored.google_calendar_event_id).summary == restored.title
    assert not stack.gateway.get_event(restored.google_calendar_event_id).is_cancelled
    if phase == "delete_sent":
        assert restored.google_calendar_event_id != original_event_id
    assert stack.worker_queue.count_pending_ops() == 0


def test_undo_during_remote_delete_does_not_reuse_event_being_deleted(stack, monkeypatch):
    stack.engine.sync_once()
    original_event_id = stack.ui_repo.get(stack.task.id).google_calendar_event_id
    stack.service.delete_task(stack.task.id)
    remote_delete = stack.gateway.delete_event
    outcomes = []

    def delete_after_user_undo(event_id):
        outcomes.append(stack.undo.undo())
        remote_delete(event_id)

    monkeypatch.setattr(stack.gateway, "delete_event", delete_after_user_undo)
    stack.engine.push_pending()
    monkeypatch.undo()
    assert len(outcomes) == 1 and outcomes[0].ok
    stack.engine.sync_once()
    restored = stack.ui_repo.get(stack.task.id)
    assert not restored.is_deleted
    assert restored.google_calendar_event_id != original_event_id
    assert stack.gateway.get_event(original_event_id).is_cancelled
    assert len(active_remote(stack)) == 1
    assert active_remote(stack)[0].id == restored.google_calendar_event_id


def test_delete_then_undo_during_initial_insert_keeps_one_live_task(stack, monkeypatch):
    remote_insert = stack.gateway.insert_event
    outcomes = []

    def reply_after_delete_and_undo(event):
        created = remote_insert(event)
        assert stack.service.delete_task(stack.task.id)
        outcomes.append(stack.undo.undo())
        return created

    monkeypatch.setattr(stack.gateway, "insert_event", reply_after_delete_and_undo)
    stack.engine.push_pending()
    monkeypatch.undo()
    assert outcomes[0].ok
    stack.engine.sync_once()
    restored = stack.ui_repo.get(stack.task.id)
    assert not restored.is_deleted
    assert len(stack.ui_repo.list_all()) == 1
    assert len(active_remote(stack)) == 1
    assert active_remote(stack)[0].id == restored.google_calendar_event_id


def test_move_and_editor_edit_are_recorded_without_viewmodel_wrappers(stack):
    stack.engine.sync_once()
    assert stack.service.move_timed_task(
        stack.task.uid, datetime(2026, 10, 24, 14, 30),
    ).ok
    assert stack.undo.can_undo
    assert stack.undo.undo().ok
    restored = stack.ui_repo.get(stack.task.id)
    assert restored.start == stack.task.start
    assert restored.end == stack.task.end

    assert stack.service.edit_task(stack.task.uid, edit_command(
        "Edited", notes="New notes", priority=3, completed=True,
        date_text="2026-10-25", time_text="15:00", duration_text="45",
    )).ok
    assert stack.undo.undo().ok
    restored = stack.ui_repo.get(stack.task.id)
    assert (restored.title, restored.notes, restored.priority, restored.completed) == (
        "Original", "Original notes", 0, False,
    )
    assert (restored.start, restored.end) == (stack.task.start, stack.task.end)
    stack.engine.sync_once()
    assert active_remote(stack)[0].summary == "Original"
    assert active_remote(stack)[0].start == stack.task.start


def test_own_push_metadata_does_not_make_undo_conflict(stack):
    assert stack.service.move_timed_task(stack.task.uid, datetime(2026, 10, 23, 12)).ok
    stack.engine.sync_once()
    assert stack.ui_repo.get(stack.task.id).google_calendar_event_id
    assert stack.undo.undo().ok
    stack.engine.sync_once()
    assert active_remote(stack)[0].start == stack.task.start
    assert len(active_remote(stack)) == 1


def test_repeated_undo_walks_back_sequential_editor_changes(stack):
    assert stack.service.edit_task(stack.task.uid, edit_command("First edit")).ok
    assert stack.service.edit_task(stack.task.uid, edit_command("Second edit")).ok
    assert stack.undo.undo().ok
    assert stack.ui_repo.get(stack.task.id).title == "First edit"
    assert stack.undo.undo().ok
    assert stack.ui_repo.get(stack.task.id).title == "Original"
    assert not stack.undo.can_undo


def test_bulk_undo_reports_partial_conflict_and_restores_other_tasks(stack):
    second = stack.service.create_task(Task(title="Second", start=stack.task.start, end=stack.task.end))
    third = stack.service.create_task(Task(title="Third", start=stack.task.start, end=stack.task.end))
    stack.engine.sync_once()
    stack.undo.clear()
    result = BulkTaskService(stack.service).execute(
        ACTION_DELETE, [stack.task.uid, second.uid, third.uid, "missing-task"],
    )
    assert result.affected_count == 3 and result.skipped_count == 1
    concurrent = stack.worker_repo.get(third.id)
    concurrent.title = "Another action changed this tombstone"
    stack.worker_repo.update_local(concurrent)

    undone = stack.undo.undo()

    assert undone.restored_count == 2
    assert undone.failed_count == 1
    assert undone.errors
    assert not undone.ok
    assert not stack.ui_repo.get(stack.task.id).is_deleted
    assert not stack.ui_repo.get(second.id).is_deleted
    assert stack.ui_repo.get(third.id).is_deleted
    assert stack.ui_repo.get(third.id).title == concurrent.title
    stack.engine.sync_once()
    assert {event.summary for event in active_remote(stack)} == {"Original", "Second"}


def test_remote_pull_after_action_makes_undo_reject_without_overwrite(stack):
    assert stack.service.edit_task(stack.task.uid, edit_command("Local edit")).ok
    stack.engine.sync_once()
    event_id = stack.ui_repo.get(stack.task.id).google_calendar_event_id
    stack.gateway.patch_event(event_id, {"summary": "Changed on phone"})
    stack.engine.pull_remote_changes()

    result = stack.undo.undo()

    assert not result.ok
    assert result.restored_count == 0 and result.failed_count == 1
    assert result.errors
    assert stack.ui_repo.get(stack.task.id).title == "Changed on phone"
    assert stack.worker_queue.count_pending_ops() == 0


def test_pull_with_old_snapshot_cannot_overwrite_undo(stack, monkeypatch):
    assert stack.service.edit_task(stack.task.uid, edit_command("Local edit")).ok
    stack.engine.sync_once()
    event_id = stack.ui_repo.get(stack.task.id).google_calendar_event_id
    stack.gateway.patch_event(event_id, {"summary": "Remote reply in flight"})
    apply_remote = stack.worker_repo.apply_calendar_update
    outcomes = []

    def undo_before_pull_commit(task, expected):
        outcomes.append(stack.undo.undo())
        return apply_remote(task, expected)

    monkeypatch.setattr(stack.worker_repo, "apply_calendar_update", undo_before_pull_commit)
    stack.engine.pull_remote_changes()
    assert outcomes[0].ok
    assert stack.ui_repo.get(stack.task.id).title == "Original"
    assert stack.worker_queue.has_pending_op(stack.task.uid)
    monkeypatch.undo()
    stack.engine.sync_once()
    assert active_remote(stack)[0].summary == "Original"


@pytest.mark.parametrize("action", ["completed", "priority"])
def test_local_only_undo_does_not_create_calendar_operation(stack, action):
    stack.engine.sync_once()
    if action == "completed":
        assert stack.service.complete_task(stack.task.id)
    else:
        assert stack.service.set_priority(stack.task.uid, 3).ok
    assert stack.worker_queue.count_pending_ops() == 0

    assert stack.undo.undo().ok

    task = stack.ui_repo.get(stack.task.id)
    assert not task.completed and task.completed_at is None
    assert task.priority == 0
    assert stack.worker_queue.count_pending_ops() == 0


def test_later_local_only_change_is_detected_despite_unchanged_updated_at(stack):
    assert stack.service.edit_task(stack.task.uid, edit_command("Local edit")).ok
    prior = stack.ui_repo.get(stack.task.id)
    stack.worker_repo.update_local_fields(stack.task.uid, priority=3)
    assert stack.ui_repo.get(stack.task.id).updated_at == prior.updated_at

    result = stack.undo.undo()

    assert not result.ok and result.failed_count == 1
    assert stack.ui_repo.get(stack.task.id).title == "Local edit"
    assert stack.ui_repo.get(stack.task.id).priority == 3


def test_explicit_capture_groups_multiple_actions(stack):
    second = stack.service.create_task(Task(title="Second"))
    stack.undo.clear()
    with stack.undo.capture("Изменить две задачи", [stack.task.uid, second.uid]):
        stack.service.set_priority(stack.task.uid, 2)
        stack.service.complete_task(second.id)
    result = stack.undo.undo()
    assert result.ok and result.restored_count == 2
    assert stack.ui_repo.get(stack.task.id).priority == 0
    assert not stack.ui_repo.get(second.id).completed
    assert not stack.undo.can_undo


@pytest.mark.parametrize("kind", ["local_series", "google_instance"])
def test_recurring_changes_are_not_claimed_as_generic_undo(stack, kind):
    task = stack.ui_repo.get(stack.task.id)
    if kind == "local_series":
        task.series_uid = "local-series"
        task.occurrence_key = "2026-10-22T10:00@UTC"
    else:
        task.google_calendar_recurring_event_id = "remote-master"
    stack.ui_repo.update(task)
    stack.undo.clear()
    assert stack.service.set_priority(task.uid, 3).ok
    assert not stack.undo.can_undo
    result = stack.undo.undo()
    assert not result.ok
    assert stack.ui_repo.get(task.id).priority == 3


def test_fake_repository_has_same_basic_delete_undo_contract():
    repo = FakeTaskRepository(seed=False)
    service = DesktopTaskService(repo)
    task = service.create_task(Task(title="In memory"))
    service.undo_service.clear()
    assert service.delete_task(task.id)
    assert service.undo_service.undo().ok
    assert repo.get(task.id).title == "In memory"
    assert not repo.get(task.id).is_deleted


@pytest.mark.parametrize("delete_already_sent", [False, True])
def test_undo_unschedule_recreates_binding_without_cancelling_old_delete(stack, delete_already_sent):
    stack.engine.sync_once()
    old_id = stack.ui_repo.get(stack.task.id).google_calendar_event_id
    assert stack.service.unschedule_task(stack.task.uid).ok
    if delete_already_sent:
        stack.engine.sync_once()
    assert stack.undo.undo().ok
    stack.engine.sync_once()
    restored = stack.ui_repo.get(stack.task.id)
    assert restored.start == stack.task.start
    assert restored.google_calendar_event_id != old_id
    assert stack.gateway.get_event(old_id).is_cancelled
    assert len(active_remote(stack)) == 1


def test_undo_creation_tombstones_and_removes_already_synced_event(stack):
    added = stack.service.create_task(Task(
        title="Accidental new task", start=stack.task.start, end=stack.task.end,
    ))
    stack.engine.sync_once()
    event_id = stack.ui_repo.get(added.id).google_calendar_event_id
    assert stack.undo.undo().ok
    assert stack.ui_repo.get(added.id).is_deleted
    stack.engine.sync_once()
    assert stack.gateway.get_event(event_id).is_cancelled
    assert [task.uid for task in stack.ui_repo.list_all()] == [stack.task.uid]


def test_late_patch_reply_preserves_inverse_move_for_next_sync(stack, monkeypatch):
    stack.engine.sync_once()
    assert stack.service.move_timed_task(stack.task.uid, datetime(2026, 10, 24, 14)).ok
    remote_patch = stack.gateway.patch_event
    outcomes = []

    def reply_after_undo(event_id, patch):
        result = remote_patch(event_id, patch)
        outcomes.append(stack.undo.undo())
        return result

    monkeypatch.setattr(stack.gateway, "patch_event", reply_after_undo)
    stack.engine.push_pending()
    monkeypatch.undo()
    assert outcomes[0].ok
    assert stack.ui_repo.get(stack.task.id).start == stack.task.start
    assert stack.worker_queue.has_pending_op(stack.task.uid)
    stack.engine.sync_once()
    assert active_remote(stack)[0].start == stack.task.start
    assert stack.worker_queue.count_pending_ops() == 0


def test_bulk_tag_undo_restores_associations_without_calendar_write(stack):
    tag_repo = SQLiteTagRepository(stack.ui_repo.db_path)
    tags = TagService(tag_repo, stack.ui_repo)
    stack.service.tag_service = tags
    try:
        existing = tags.create("Existing")
        added = tags.create("Added in bulk")
        second = stack.service.create_task(Task(title="Second"))
        tags.set_task_tags(stack.task.uid, [existing.id])
        stack.engine.sync_once()
        stack.undo.clear()
        result = BulkTaskService(stack.service, tags).execute(
            ACTION_ADD_TAG, [stack.task.uid, second.uid], added.id,
        )
        assert result.affected_count == 2
        assert stack.worker_queue.count_pending_ops() == 0

        undone = stack.undo.undo()

        assert undone.ok and undone.restored_count == 2
        assert [tag.id for tag in tags.tags_for_task(stack.task.uid)] == [existing.id]
        assert tags.tags_for_task(second.uid) == []
        assert stack.worker_queue.count_pending_ops() == 0
    finally:
        tag_repo.close()


def test_editor_with_tags_is_one_undo_action(stack):
    from planner_desktop.viewmodels.task_actions import TaskActionsViewModel

    class EditorActions(TaskActionsViewModel):
        def _emit_data_changed(self):
            pass

    tag_repo = SQLiteTagRepository(stack.ui_repo.db_path)
    tags = TagService(tag_repo, stack.ui_repo)
    stack.service.tag_service = tags
    try:
        previous = tags.create("Previous tag")
        replacement = tags.create("Replacement tag")
        tags.set_task_tags(stack.task.uid, [previous.id])
        stack.undo.clear()
        vm = EditorActions(stack.service)
        assert vm.saveEditorWithTags(
            stack.task.uid, "Edited in dialog", "Edited notes", 2,
            True, False, "2026-10-22", "10:00", "60", True,
            [replacement.id],
        )

        result = stack.undo.undo()

        assert result.ok and result.restored_count == 1
        restored = stack.ui_repo.get(stack.task.id)
        assert restored.title == "Original"
        assert restored.notes == "Original notes"
        assert restored.priority == 0 and not restored.completed
        assert [tag.id for tag in tags.tags_for_task(stack.task.uid)] == [previous.id]
        assert not stack.undo.can_undo
    finally:
        tag_repo.close()


def test_new_editor_task_with_tags_can_be_undone(stack):
    from planner_desktop.viewmodels.task_actions import TaskActionsViewModel

    class EditorActions(TaskActionsViewModel):
        def _emit_data_changed(self):
            pass

    tag_repo = SQLiteTagRepository(stack.ui_repo.db_path)
    tags = TagService(tag_repo, stack.ui_repo)
    stack.service.tag_service = tags
    try:
        added_tag = tags.create("New task tag")
        stack.undo.clear()
        vm = EditorActions(stack.service)
        assert vm.saveEditorWithTags(
            "", "New from editor", "Notes", 1,
            False, False, "", "", "", False, [added_tag.id],
        )
        created = next(task for task in stack.ui_repo.list_all() if task.uid != stack.task.uid)
        assert created.tags == (added_tag.name,)

        result = stack.undo.undo()

        assert result.ok and result.restored_count == 1
        assert stack.ui_repo.get(created.id).is_deleted
        assert [task.uid for task in stack.ui_repo.list_all()] == [stack.task.uid]
        assert not stack.undo.can_undo
    finally:
        tag_repo.close()


def test_fake_bulk_tag_undo_restores_tag_repository_associations():
    from planner_desktop.repositories.tag_repository import InMemoryTagRepository

    repo = FakeTaskRepository(seed=False)
    tag_repo = InMemoryTagRepository()
    tags = TagService(tag_repo, repo)
    service = DesktopTaskService(repo, tag_service=tags)
    task = service.create_task(Task(title="Tagged in memory"))
    previous = tags.create("Previous")
    added = tags.create("Added")
    tags.set_task_tags(task.uid, [previous.id])
    service.undo_service.clear()
    assert BulkTaskService(service, tags).execute(ACTION_ADD_TAG, [task.uid], added.id).affected_count == 1

    result = service.undo_service.undo()

    assert result.ok
    assert repo.get(task.id).tags == (previous.name,)
    assert [tag.id for tag in tags.tags_for_task(task.uid)] == [previous.id]


def test_mixed_bulk_undo_explicitly_reports_unsupported_series(stack):
    recurring = stack.ui_repo.add(Task(
        title="Local series occurrence", series_uid="series-test",
        occurrence_key="2026-10-22",
    ))
    stack.undo.clear()
    result = BulkTaskService(stack.service).execute(
        ACTION_COMPLETE, [stack.task.uid, recurring.uid],
    )
    assert result.affected_count == 2

    undone = stack.undo.undo()

    assert not undone.ok
    assert undone.restored_count == 1
    assert undone.failed_count == 1
    assert undone.errors
    assert not stack.ui_repo.get(stack.task.id).completed
    assert stack.ui_repo.get(recurring.id).completed
