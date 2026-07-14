from datetime import datetime, timedelta

from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.tag_repository import InMemoryTagRepository
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.storage.tag_repository import SQLiteTagRepository
from planner_desktop.usecases.bulk_task_service import (
    ACTION_ADD_TAG,
    ACTION_COMPLETE,
    ACTION_DELETE,
    ACTION_POSTPONE_TOMORROW,
    ACTION_PRIORITY,
    ACTION_REMOVE_TAG,
    ACTION_RESTORE,
    ACTION_UNSCHEDULE,
    STATUS_AFFECTED,
    STATUS_FAILED,
    STATUS_SKIPPED,
    BulkTaskService,
)
from planner_desktop.usecases.tag_service import TagService
from planner_desktop.usecases.task_service import DesktopTaskService


NOW = datetime(2026, 7, 14, 10, 0)


def sqlite_services(tmp_path):
    path = tmp_path / "desktop.db"
    repo = SQLiteTaskRepository(path)
    queue = CalendarSyncStore(path)
    tag_repo = SQLiteTagRepository(path)
    tags = TagService(tag_repo, repo)
    task_service = DesktopTaskService(repo, calendar_queue=queue, tag_service=tags)
    bulk = BulkTaskService(task_service, tags)
    return repo, queue, tag_repo, tags, task_service, bulk


def due(queue):
    return [(op.op, op.task_uid) for op in queue.list_due_ops()]


def close_all(repo, queue, tag_repo):
    tag_repo.close(); queue.close(); repo.close()


def test_bulk_complete_restore_priority_and_structured_counts(tmp_path):
    repo, queue, tag_repo, tags, service, bulk = sqlite_services(tmp_path)
    first = repo.add(Task(title="A", uid="a"))
    second = repo.add(Task(title="B", uid="b", completed=True))

    completed = bulk.execute(ACTION_COMPLETE, [second.uid, first.uid])
    assert [item.uid for item in completed.items] == ["b", "a"]
    assert (completed.affected_count, completed.skipped_count, completed.failed_count) == (1, 1, 0)
    assert repo.get_by_uid(first.uid).completed is True

    restored = bulk.execute(ACTION_RESTORE, [first.uid, second.uid])
    assert restored.affected_count == 2
    assert not repo.get_by_uid(first.uid).completed
    priority = bulk.execute(ACTION_PRIORITY, [first.uid, second.uid], 3)
    assert priority.affected_count == 2
    assert [repo.get_by_uid(uid).priority for uid in ("a", "b")] == [3, 3]
    assert due(queue) == []
    close_all(repo, queue, tag_repo)


def test_bulk_tag_add_remove_is_local_and_respects_existing_state(tmp_path):
    repo, queue, tag_repo, tags, service, bulk = sqlite_services(tmp_path)
    first = repo.add(Task(title="A")); second = repo.add(Task(title="B"))
    tag = tags.create("Работа")
    add = bulk.execute(ACTION_ADD_TAG, [first.uid, second.uid], tag.id)
    assert add.affected_count == 2
    again = bulk.execute(ACTION_ADD_TAG, [first.uid], tag.id)
    assert again.skipped_count == 1
    remove = bulk.execute(ACTION_REMOVE_TAG, [first.uid, second.uid], tag.id)
    assert remove.affected_count == 2
    assert due(queue) == []
    close_all(repo, queue, tag_repo)


def test_bulk_postpone_queue_semantics_and_recurring_skip(tmp_path):
    repo, queue, tag_repo, tags, service, bulk = sqlite_services(tmp_path)
    undated = repo.add(Task(title="Без даты"))
    start = datetime(2026, 7, 14, 9)
    linked = repo.add(Task(
        title="Linked", start=start, end=start + timedelta(hours=1),
        google_calendar_event_id="event-1",
    ))
    recurring = repo.add(Task(
        title="Recurring", start=start, end=start + timedelta(hours=1),
        google_calendar_event_id="event-r",
        google_calendar_recurring_event_id="series-r",
    ))
    result = bulk.execute(
        ACTION_POSTPONE_TOMORROW,
        [undated.uid, linked.uid, recurring.uid],
        now=NOW,
    )
    assert [item.status for item in result.items] == [
        STATUS_AFFECTED, STATUS_AFFECTED, STATUS_SKIPPED
    ]
    assert due(queue) == [
        ("create", undated.uid), ("update", linked.uid)
    ]
    assert repo.get_by_uid(recurring.uid).start == start
    close_all(repo, queue, tag_repo)


def test_bulk_unschedule_and_delete_follow_queue_contract(tmp_path):
    repo, queue, tag_repo, tags, service, bulk = sqlite_services(tmp_path)
    start = datetime(2026, 7, 14, 9)
    linked = repo.add(Task(
        title="Unschedule", start=start, end=start + timedelta(hours=1),
        google_calendar_event_id="event-u",
    ))
    doomed = repo.add(Task(
        title="Delete", start=start, end=start + timedelta(hours=1),
        google_calendar_event_id="event-d",
    ))
    unscheduled = bulk.execute(ACTION_UNSCHEDULE, [linked.uid])
    assert unscheduled.affected_count == 1
    detached = repo.get_by_uid(linked.uid)
    assert detached.start is None and detached.google_calendar_event_id is None
    deleted = bulk.execute(ACTION_DELETE, [doomed.uid])
    assert deleted.affected_count == 1
    assert repo.get_by_uid(doomed.uid).is_deleted
    assert due(queue) == [
        ("delete", linked.uid), ("delete", doomed.uid)
    ]
    close_all(repo, queue, tag_repo)


def test_busy_guard_and_structured_partial_failure():
    class FailingRepo(FakeTaskRepository):
        def complete(self, task_id, completed=True):
            task = self.get(task_id)
            if task and task.uid == "bad":
                raise RuntimeError("injected failure")
            return super().complete(task_id, completed)

    repo = FailingRepo(seed=False)
    repo.add(Task(title="Good", uid="good"))
    repo.add(Task(title="Bad", uid="bad"))
    bulk = BulkTaskService(DesktopTaskService(repo))
    result = bulk.execute(ACTION_COMPLETE, ["good", "bad"])
    assert [item.status for item in result.items] == [STATUS_AFFECTED, STATUS_FAILED]
    assert result.affected_count == 1 and result.failed_count == 1
    assert repo.get_by_uid("good").completed
    assert not repo.get_by_uid("bad").completed

    bulk._busy = True
    busy = bulk.execute(ACTION_COMPLETE, ["bad"])
    assert busy.busy_rejected and busy.items == ()


def test_schedule_queue_failure_rolls_back_each_failed_item(tmp_path):
    repo, queue, tag_repo, tags, service, bulk = sqlite_services(tmp_path)
    start = datetime(2026, 7, 14, 9)
    linked = repo.add(Task(
        title="Linked", start=start, end=start + timedelta(hours=1),
        google_calendar_event_id="event-1",
    ))
    original_enqueue = queue.enqueue_update
    queue.enqueue_update = lambda *args, **kwargs: (_ for _ in ()).throw(
        RuntimeError("queue down")
    )
    result = bulk.execute(ACTION_POSTPONE_TOMORROW, [linked.uid], now=NOW)
    assert result.failed_count == 1
    restored = repo.get_by_uid(linked.uid)
    assert restored.start == start and restored.google_calendar_event_id == "event-1"
    assert due(queue) == []
    queue.enqueue_update = original_enqueue
    close_all(repo, queue, tag_repo)
