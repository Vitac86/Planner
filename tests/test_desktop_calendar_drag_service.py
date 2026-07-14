"""Calendar move/conversion service and queue safety."""
import json
from datetime import date, datetime, timedelta

import pytest

from planner_desktop.domain.calendar_interactions import RECURRING_INTERACTION_ERROR
from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.sync.sync_types import OpKind
from planner_desktop.usecases.task_service import DesktopTaskService


NOW = datetime(2026, 7, 14, 9)


@pytest.fixture
def queue(tmp_path):
    value = CalendarSyncStore(tmp_path / "calendar.db")
    yield value
    value.close()


@pytest.fixture
def repo():
    return FakeTaskRepository(seed=False)


@pytest.fixture
def service(repo, queue):
    return DesktopTaskService(repo, calendar_queue=queue)


def add_timed(service, queue, *, uid="timed", linked=False):
    task = service.create_task(Task(
        uid=uid,
        title=uid,
        start=NOW,
        end=NOW + timedelta(hours=1),
        duration_minutes=60,
    ))
    queue.cancel_pending_ops(uid)
    if linked:
        task.google_calendar_event_id = "event-" + uid
        service.repository.update(task)
    return task


def pending(queue):
    return queue.list_due_ops()


def test_unlinked_move_updates_repository_without_duplicate_create(service, queue):
    task = add_timed(service, queue)
    result = service.move_timed_task(task.uid, NOW + timedelta(days=1))
    assert result.ok
    assert service.get_task(task.uid).start == NOW + timedelta(days=1)
    assert [op.op for op in pending(queue)] == [OpKind.CREATE]
    service.move_timed_task(task.uid, NOW + timedelta(days=2))
    assert [op.op for op in pending(queue)] == [OpKind.CREATE]


def test_linked_move_enqueues_update(service, queue):
    task = add_timed(service, queue, linked=True)
    result = service.move_timed_task(task.uid, NOW + timedelta(hours=2))
    assert result.ok
    assert [op.op for op in pending(queue)] == [OpKind.UPDATE]


def test_undated_to_scheduled_enqueues_create(service, queue):
    task = service.create_task(Task(uid="later", title="later"))
    assert pending(queue) == []
    result = service.schedule_undated_task(task.uid, NOW)
    assert result.ok
    assert [op.op for op in pending(queue)] == [OpKind.CREATE]


def test_linked_unschedule_queues_delete_with_event_id_and_unlinks(service, queue):
    task = add_timed(service, queue, linked=True)
    result = service.unschedule_task(task.uid)
    assert result.ok
    stored = service.get_task(task.uid)
    assert stored.start is None
    assert stored.google_calendar_event_id is None
    assert [op.op for op in pending(queue)] == [OpKind.DELETE]
    assert json.loads(pending(queue)[0].payload_json)["event_id"] == "event-timed"


def test_timed_all_day_round_trip_and_multiday_span(service, queue):
    task = add_timed(service, queue)
    converted = service.convert_to_all_day(task.uid, date(2026, 7, 20))
    assert converted.ok
    assert converted.task.end == datetime(2026, 7, 21)
    timed = service.convert_to_timed(task.uid, datetime(2026, 7, 20, 11))
    assert timed.ok
    assert timed.task.duration_minutes == 60

    timed.task.is_all_day = True
    timed.task.start = datetime(2026, 7, 20)
    timed.task.end = datetime(2026, 7, 23)
    service.repository.update(timed.task)
    moved = service.convert_to_all_day(task.uid, date(2026, 8, 1))
    assert moved.task.end == datetime(2026, 8, 4)


def test_noop_move_does_not_write_or_queue(service, repo, queue):
    task = add_timed(service, queue, linked=True)
    updated_at = task.updated_at
    result = service.move_timed_task(task.uid, NOW)
    assert result.ok
    assert pending(queue) == []
    assert repo.get_by_uid(task.uid).updated_at == updated_at


def test_recurring_move_refused_before_repository_or_queue_mutation(service, repo, queue):
    task = add_timed(service, queue, linked=True)
    task.google_calendar_recurring_event_id = "series"
    task.google_calendar_original_start = NOW
    repo.update(task)
    before = (task.start, task.end, task.updated_at)
    result = service.move_timed_task(task.uid, NOW + timedelta(days=1))
    assert not result.ok and result.errors == [RECURRING_INTERACTION_ERROR]
    stored = repo.get_by_uid(task.uid)
    assert (stored.start, stored.end, stored.updated_at) == before
    assert pending(queue) == []


class FailOnceRepository(FakeTaskRepository):
    def __init__(self):
        super().__init__(seed=False)
        self.fail_next = False

    def update(self, task):
        if self.fail_next:
            self.fail_next = False
            raise RuntimeError("disk full")
        return super().update(task)


class FailAfterEnqueueQueue(CalendarSyncStore):
    def __init__(self, db_path):
        super().__init__(db_path)
        self.fail_next_update = False

    def enqueue_update(self, task_uid, payload=None):
        super().enqueue_update(task_uid, payload)
        if self.fail_next_update:
            self.fail_next_update = False
            raise RuntimeError("queue unavailable")


def test_failed_repository_write_restores_original_and_queue(tmp_path):
    repo = FailOnceRepository()
    queue = CalendarSyncStore(tmp_path / "rollback.db")
    service = DesktopTaskService(repo, calendar_queue=queue)
    try:
        task = add_timed(service, queue, linked=True)
        original = (task.start, task.end, task.duration_minutes)
        repo.fail_next = True
        result = service.move_timed_task(task.uid, NOW + timedelta(days=1))
        assert not result.ok
        stored = repo.get_by_uid(task.uid)
        assert (stored.start, stored.end, stored.duration_minutes) == original
        assert pending(queue) == []
    finally:
        queue.close()


def test_failed_queue_write_rolls_back_repository_and_queue(tmp_path):
    repo = FakeTaskRepository(seed=False)
    queue = FailAfterEnqueueQueue(tmp_path / "queue-rollback.db")
    service = DesktopTaskService(repo, calendar_queue=queue)
    try:
        task = add_timed(service, queue, linked=True)
        original = (task.start, task.end, task.duration_minutes)
        queue.fail_next_update = True
        result = service.move_timed_task(task.uid, NOW + timedelta(days=1))
        assert not result.ok
        stored = repo.get_by_uid(task.uid)
        assert (stored.start, stored.end, stored.duration_minutes) == original
        assert pending(queue) == []
    finally:
        queue.close()
