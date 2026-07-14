"""Calendar timed resize service behavior."""
from datetime import datetime, timedelta

from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.sync.sync_types import OpKind
from planner_desktop.usecases.task_service import DesktopTaskService


START = datetime(2026, 7, 14, 9)


def make_service(tmp_path, *, linked=True):
    repo = FakeTaskRepository(seed=False)
    queue = CalendarSyncStore(tmp_path / "resize.db")
    service = DesktopTaskService(repo, calendar_queue=queue)
    task = service.create_task(Task(
        uid="resize",
        title="resize",
        start=START,
        end=START + timedelta(hours=1),
        duration_minutes=60,
        google_calendar_event_id="event-resize" if linked else None,
    ))
    queue.cancel_pending_ops(task.uid)
    return service, repo, queue, task


def test_resize_updates_duration_and_enqueues_update(tmp_path):
    service, repo, queue, task = make_service(tmp_path)
    try:
        result = service.resize_timed_task(
            task.uid, end=START + timedelta(minutes=90)
        )
        assert result.ok
        assert repo.get_by_uid(task.uid).duration_minutes == 90
        assert [op.op for op in queue.list_due_ops()] == [OpKind.UPDATE]
    finally:
        queue.close()


def test_resize_rejects_less_than_minimum_without_mutation(tmp_path):
    service, repo, queue, task = make_service(tmp_path)
    try:
        before = (task.start, task.end, task.duration_minutes)
        result = service.resize_timed_task(
            task.uid, end=START + timedelta(minutes=10)
        )
        assert not result.ok
        stored = repo.get_by_uid(task.uid)
        assert (stored.start, stored.end, stored.duration_minutes) == before
        assert queue.list_due_ops() == []
    finally:
        queue.close()


def test_recurring_resize_refused_with_zero_queue_mutation(tmp_path):
    service, repo, queue, task = make_service(tmp_path)
    try:
        task.google_calendar_recurring_event_id = "series"
        task.google_calendar_original_start = START
        repo.update(task)
        before = task.updated_at
        result = service.resize_timed_task(
            task.uid, end=START + timedelta(minutes=90)
        )
        assert not result.ok
        assert repo.get_by_uid(task.uid).updated_at == before
        assert queue.list_due_ops() == []
    finally:
        queue.close()
