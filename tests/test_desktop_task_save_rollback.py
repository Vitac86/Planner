"""Saving a task and its sync operation must succeed or fail together."""
from copy import deepcopy
from datetime import datetime

import pytest

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.task import Task
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel


@pytest.fixture
def stores(tmp_path):
    path = tmp_path / "planner.db"
    repository = SQLiteTaskRepository(path)
    queue = CalendarSyncStore(path)
    yield DesktopTaskService(repository, calendar_queue=queue), repository, queue
    queue.close()
    repository.close()


def scheduled_task(**changes):
    fields = dict(
        title="Original",
        start=datetime(2026, 7, 15, 9),
        end=datetime(2026, 7, 15, 10),
        duration_minutes=60,
    )
    fields.update(changes)
    return Task(**fields)


@pytest.mark.parametrize("after_write", [False, True])
def test_failed_quick_add_leaves_no_task_or_queue_and_retry_creates_once(stores, monkeypatch, after_write):
    service, repository, queue = stores
    original_enqueue = queue.enqueue_create

    def fail(uid, payload=None):
        if after_write:
            original_enqueue(uid, payload)
        raise RuntimeError("queue failure")

    monkeypatch.setattr(queue, "enqueue_create", fail)
    clock = [0.0]
    vm = TodayViewModel(service=service, clock=lambda: clock[0])
    args = ("Meeting", "Notes", True, False, "2026-07-15", "09:00", "60")
    assert vm.addTask(*args) is False
    assert "queue failure" in vm.errorMessage
    assert repository.list_all() == []
    assert queue.list_due_ops() == []

    monkeypatch.setattr(queue, "enqueue_create", original_enqueue)
    clock[0] += 1
    assert vm.addTask(*args) is True
    assert len(repository.list_all()) == 1
    assert len(queue.list_due_ops()) == 1


def test_update_failure_restores_content_and_existing_queue(stores, monkeypatch):
    service, repository, queue = stores
    task = service.create_task(scheduled_task(google_calendar_event_id="event-1"))
    old_ops = queue.snapshot_task_ops(task.uid)
    old_task = deepcopy(repository.get_by_uid(task.uid))
    enqueue = queue.enqueue_update

    def fail_after_write(uid, payload=None):
        enqueue(uid, payload)
        raise RuntimeError("queue failure")

    monkeypatch.setattr(queue, "enqueue_update", fail_after_write)
    vm = TodayViewModel(service=service)
    assert not vm.saveEditor(
        task.uid, "Changed", "Changed notes", 3, True, False,
        "2026-07-16", "11:00", "30", True,
    )
    restored = repository.get_by_uid(task.uid)
    assert restored.title == old_task.title
    assert restored.notes == old_task.notes
    assert restored.start == old_task.start and restored.end == old_task.end
    assert restored.completed == old_task.completed
    assert restored.google_calendar_event_id == "event-1"
    restored_ops = queue.snapshot_task_ops(task.uid)
    assert [{key: value for key, value in row.items() if key != "id"}
            for row in restored_ops] == [
        {key: value for key, value in row.items() if key != "id"}
        for row in old_ops
    ]
    assert {row["id"] for row in restored_ops}.isdisjoint(
        row["id"] for row in old_ops
    )


def test_update_failure_restores_snapshot_in_reference_based_repository(stores, monkeypatch):
    _, _, queue = stores
    from planner_desktop.repositories.fake_task_repository import FakeTaskRepository

    repository = FakeTaskRepository(seed=False)
    service = DesktopTaskService(repository, calendar_queue=queue)
    task = service.create_task(scheduled_task())
    old_start = task.start

    def fail(*args, **kwargs):
        raise RuntimeError("queue failure")

    monkeypatch.setattr(queue, "enqueue_create", fail)
    command = TaskEditorCommand(
        title="Changed", add_to_calendar=True,
        date_text="2026-07-17", time_text="14:00", duration_text="30",
    )
    with pytest.raises(RuntimeError, match="queue failure"):
        service.edit_task(task.uid, command)
    assert task.title == "Original"
    assert task.start == old_start


def test_stale_editor_preserves_binding_created_by_background_sync(stores):
    service, repository, queue = stores
    task = service.create_task(scheduled_task())
    stale_editor = deepcopy(task)
    queue.remove_op(queue.list_due_ops()[0].id)
    pushed = repository.get_by_uid(task.uid)
    pushed.google_calendar_event_id = "event-created-in-background"
    pushed.google_calendar_etag = "new-etag"
    repository.update(pushed)

    stale_editor.title = "Edited while syncing"
    service.update_task(stale_editor)

    saved = repository.get_by_uid(task.uid)
    assert saved.title == "Edited while syncing"
    assert saved.google_calendar_event_id == "event-created-in-background"
    assert saved.google_calendar_etag == "new-etag"
    assert [op.op for op in queue.list_due_ops()] == ["update"]
