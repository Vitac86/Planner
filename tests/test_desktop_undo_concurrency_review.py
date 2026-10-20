"""Undo must never claim an unrelated background pull as the user's change."""
from datetime import datetime, timedelta
from types import SimpleNamespace

from PySide6.QtCore import QCoreApplication
import pytest

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.task import Task, utc_now
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel


@pytest.fixture
def stack(tmp_path):
    app = QCoreApplication.instance() or QCoreApplication([])
    path = tmp_path / "undo-capture.db"
    ui, worker = SQLiteTaskRepository(path), SQLiteTaskRepository(path)
    ui_queue, worker_queue = CalendarSyncStore(path), CalendarSyncStore(path)
    service = DesktopTaskService(ui, ui_queue)
    gateway = FakeCalendarGateway(base_time=utc_now() + timedelta(minutes=5))
    engine = CalendarSyncEngine(worker, worker_queue, gateway)
    task = service.create_task(Task(
        title="Original", start=datetime(2026, 10, 22, 10),
        end=datetime(2026, 10, 22, 11), duration_minutes=60,
    ))
    engine.sync_once()
    service.undo_service.clear()
    event_id = ui.get(task.id).google_calendar_event_id
    gateway.patch_event(event_id, {"summary": "Changed on phone"})
    yield SimpleNamespace(
        app=app, ui=ui, worker=worker, ui_queue=ui_queue,
        worker_queue=worker_queue, service=service, gateway=gateway,
        engine=engine, task=task, event_id=event_id,
    )
    worker_queue.close()
    ui_queue.close()
    worker.close()
    ui.close()


def assert_phone_change_survives(stack):
    stack.engine.sync_once()
    assert stack.ui.get(stack.task.id).title == "Changed on phone"
    active = [event for event in stack.gateway.events if not event.is_cancelled]
    assert len(active) <= 1
    assert all(event.summary == "Changed on phone" for event in active)


def test_rejected_editor_does_not_capture_concurrent_pull(stack, monkeypatch):
    def reject_after_pull(command):
        stack.engine.pull_remote_changes()
        return ["Invalid form"]

    monkeypatch.setattr(
        "planner_desktop.usecases.task_service.validate_editor", reject_after_pull,
    )
    result = stack.service.edit_task(stack.task.uid, TaskEditorCommand(title=""))

    assert not result.ok
    assert not stack.service.undo_service.can_undo
    assert not stack.service.undo_service.undo().ok
    assert_phone_change_survives(stack)


@pytest.mark.parametrize("through_viewmodel", [False, True])
def test_local_only_editor_undo_cannot_revert_concurrent_pull(
    stack, monkeypatch, through_viewmodel,
):
    update = stack.ui.update_local_fields

    def pull_before_local_save(uid, **changes):
        stack.engine.pull_remote_changes()
        return update(uid, **changes)

    monkeypatch.setattr(stack.ui, "update_local_fields", pull_before_local_save)
    if through_viewmodel:
        vm = TodayViewModel(service=stack.service)
        saved = vm.saveEditorWithTags(
            stack.task.uid, "Original", "", 3, True, False,
            "2026-10-22", "10:00", "60", False, [],
        )
    else:
        saved = stack.service.edit_task(stack.task.uid, TaskEditorCommand(
            title="Original", priority=3, add_to_calendar=True,
            date_text="2026-10-22", time_text="10:00", duration_text="60",
        )).ok

    assert saved
    assert stack.ui.get(stack.task.id).title == "Changed on phone"
    assert stack.ui.get(stack.task.id).priority == 3
    stack.service.undo_service.undo()
    assert_phone_change_survives(stack)


def test_delete_undo_cannot_revert_pull_between_read_and_delete(stack, monkeypatch):
    delete = stack.ui.delete

    def pull_before_delete(task_id):
        stack.engine.pull_remote_changes()
        return delete(task_id)

    monkeypatch.setattr(stack.ui, "delete", pull_before_delete)
    assert stack.service.delete_task(stack.task.id)
    assert stack.ui.get(stack.task.id).title == "Changed on phone"

    stack.service.undo_service.undo()

    assert_phone_change_survives(stack)
