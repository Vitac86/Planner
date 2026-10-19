"""Local-only edits preserve a concurrently pulled Calendar payload and cursor."""
from copy import deepcopy
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.task import Task, utc_now
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.task_service import DesktopTaskService


@pytest.fixture
def sync(tmp_path):
    path = tmp_path / "local-fields.db"
    repo, ui_repo = SQLiteTaskRepository(path), SQLiteTaskRepository(path)
    store, ui_store = CalendarSyncStore(path), CalendarSyncStore(path)
    gateway = FakeCalendarGateway(base_time=utc_now() + timedelta(minutes=5))
    engine = CalendarSyncEngine(repo, store, gateway)
    service = DesktopTaskService(ui_repo, ui_store)
    task = service.create_task(Task(
        title="Original", start=datetime(2026, 7, 8, 10),
        end=datetime(2026, 7, 8, 11), duration_minutes=60,
    ))
    engine.sync_once()
    yield SimpleNamespace(
        repo=repo, ui_repo=ui_repo, store=store, ui_store=ui_store,
        gateway=gateway, engine=engine, service=service, task=task,
    )
    store.close()
    ui_store.close()
    repo.close()
    ui_repo.close()


def edit_local(sync, *, priority=0, completed=False):
    return sync.service.edit_task(sync.task.uid, TaskEditorCommand(
        title="Original", add_to_calendar=True, date_text="2026-07-08",
        time_text="10:00", duration_text="60",
        priority=priority, completed=completed,
    ))


@pytest.mark.parametrize("action", ["priority", "editor_priority", "editor_completed"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_local_field_save_preserves_pull_between_read_and_write(sync, monkeypatch, action, cancelled):
    event_id = sync.ui_repo.get(sync.task.id).google_calendar_event_id
    remote_start = datetime(2026, 7, 9, 14)
    if cancelled:
        sync.gateway.delete_event(event_id)
    else:
        sync.gateway.patch_event(event_id, {
            "summary": "Remote new", "start": remote_start,
            "end": remote_start + timedelta(hours=1),
        })
    original = sync.ui_repo.update_local_fields

    def interleave(uid, **changes):
        sync.engine.pull_remote_changes()
        return original(uid, **changes)

    monkeypatch.setattr(sync.ui_repo, "update_local_fields", interleave)
    if action == "priority":
        result = sync.service.set_priority(sync.task.uid, 3)
    elif action == "editor_priority":
        result = edit_local(sync, priority=3)
    else:
        result = edit_local(sync, completed=True)

    local = sync.ui_repo.get(sync.task.id)
    assert local.is_deleted == cancelled
    assert sync.store.count_pending_ops() == 0
    if cancelled:
        assert not result.ok
    else:
        assert result.ok
        assert result.task.title == local.title == "Remote new"
        assert result.task.start == local.start == remote_start
        assert local.priority == (0 if action == "editor_completed" else 3)
        assert local.completed == (action == "editor_completed")
    monkeypatch.undo()
    sync.engine.sync_once()
    assert sync.ui_repo.get(sync.task.id).is_deleted == cancelled
    assert sync.store.count_pending_ops() == 0


@pytest.mark.parametrize("action", ["priority", "complete", "toggle", "editor"])
@pytest.mark.parametrize("cancelled", [False, True])
def test_local_only_change_does_not_invalidate_inflight_pull(sync, monkeypatch, action, cancelled):
    event_id = sync.ui_repo.get(sync.task.id).google_calendar_event_id
    if cancelled:
        sync.gateway.delete_event(event_id)
    else:
        sync.gateway.patch_event(event_id, {"summary": "Remote new"})
    original = sync.repo.apply_calendar_update

    def interleave(task, expected):
        version = sync.ui_repo.get(sync.task.id).updated_at
        if action == "priority":
            assert sync.service.set_priority(sync.task.uid, 3).ok
        elif action == "complete":
            assert sync.service.complete_task(sync.task.id)
        elif action == "toggle":
            assert sync.service.toggle_completed(sync.task.uid)
        else:
            assert edit_local(sync, priority=3, completed=True).ok
        assert sync.ui_repo.get(sync.task.id).updated_at == version
        return original(task, expected)

    monkeypatch.setattr(sync.repo, "apply_calendar_update", interleave)
    sync.engine.pull_remote_changes()
    local = sync.ui_repo.get(sync.task.id)
    assert local.is_deleted == cancelled
    assert local.completed == (action != "priority")
    assert local.priority == (3 if action in {"priority", "editor"} else 0)
    if not cancelled:
        assert local.title == "Remote new"
    assert sync.store.count_pending_ops() == 0
    monkeypatch.undo()
    sync.engine.sync_once()
    assert sync.ui_repo.get(sync.task.id).is_deleted == cancelled


@pytest.mark.parametrize("edit_completion", [False, True])
def test_editor_updates_only_local_fields_the_user_changed(sync, monkeypatch, edit_completion):
    original = sync.ui_repo.update_local_fields

    def interleave(uid, **changes):
        original(uid, priority=3 if edit_completion else None,
                 completed=None if edit_completion else True)
        return original(uid, **changes)

    monkeypatch.setattr(sync.ui_repo, "update_local_fields", interleave)
    result = edit_local(sync, priority=0 if edit_completion else 3,
                        completed=edit_completion)
    assert result.ok
    assert result.task.priority == 3
    assert result.task.completed is True
    assert result.task.completed_at is not None
    assert sync.store.count_pending_ops() == 0


def test_fake_pull_matches_sqlite_preservation_of_local_fields():
    repo = FakeTaskRepository(seed=False)
    task = repo.add(Task(title="Original", tags=("Keep",)))
    expected = deepcopy(task)
    incoming = deepcopy(task)
    incoming.title = "Remote new"
    repo.update_local_fields(task.uid, priority=3, completed=True)
    assert task.updated_at == expected.updated_at
    assert repo.apply_calendar_update(incoming, expected)
    assert task.title == "Remote new"
    assert task.priority == 3 and task.completed
    assert task.completed_at is not None
    assert task.tags == ("Keep",)
