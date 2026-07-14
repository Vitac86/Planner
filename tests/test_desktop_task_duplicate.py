from datetime import datetime, timedelta, timezone

from planner_desktop.domain.task import Task
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.storage.tag_repository import SQLiteTagRepository
from planner_desktop.usecases.tag_service import TagService
from planner_desktop.usecases.task_service import (
    DUPLICATE_DELETED_ERROR,
    DesktopTaskService,
)


def build(tmp_path):
    db_path = tmp_path / "desktop.db"
    repo = SQLiteTaskRepository(db_path)
    queue = CalendarSyncStore(db_path)
    tags_repo = SQLiteTagRepository(db_path)
    tags = TagService(tags_repo, repo)
    service = DesktopTaskService(repo, calendar_queue=queue, tag_service=tags)
    return repo, queue, tags_repo, tags, service


def due(queue):
    return [(op.op, op.task_uid) for op in queue.list_due_ops()]


def test_undated_duplicate_remains_local_copies_tags_and_resets_completed(tmp_path):
    repo, queue, tag_repo, tags, service = build(tmp_path)
    source = service.create_task(Task(
        title="Идея", notes="детали", priority=3, completed=True,
    ))
    tag = tags.create("Проект")
    tags.set_task_tags(source.uid, [tag.id])

    result = service.duplicate_task(source.uid)
    assert result.ok
    duplicate = repo.get_by_uid(result.task.uid)
    assert duplicate.uid != source.uid
    assert (duplicate.title, duplicate.notes, duplicate.priority) == (
        "Идея", "детали", 3
    )
    assert duplicate.tags == ("Проект",)
    assert duplicate.completed is False and duplicate.completed_at is None
    assert duplicate.start is None
    assert due(queue) == []
    tag_repo.close(); queue.close(); repo.close()


def test_scheduled_duplicate_enqueues_exactly_one_create_and_strips_linkage(tmp_path):
    repo, queue, tag_repo, tags, service = build(tmp_path)
    start = datetime(2026, 7, 14, 9, 0)
    source = repo.add(Task(
        title="Встреча",
        start=start,
        end=start + timedelta(minutes=45),
        duration_minutes=45,
        google_calendar_event_id="event-1",
        google_calendar_etag="etag-1",
        google_calendar_recurring_event_id="series-1",
        google_calendar_original_start=start.replace(tzinfo=timezone.utc),
    ))
    result = service.duplicate_task(source.uid)
    assert result.ok
    duplicate = repo.get_by_uid(result.task.uid)
    assert due(queue) == [("create", duplicate.uid)]
    assert (duplicate.start, duplicate.end, duplicate.duration_minutes) == (
        source.start, source.end, 45
    )
    assert duplicate.google_calendar_event_id is None
    assert duplicate.google_calendar_etag is None
    assert duplicate.google_calendar_recurring_event_id is None
    assert duplicate.google_calendar_original_start is None
    tag_repo.close(); queue.close(); repo.close()


def test_tombstone_cannot_be_duplicated(tmp_path):
    repo, queue, tag_repo, tags, service = build(tmp_path)
    source = repo.add(Task(title="Удалена"))
    repo.delete(source.id)
    result = service.duplicate_task(source.uid)
    assert not result.ok
    assert result.errors == [DUPLICATE_DELETED_ERROR]
    assert len(repo.list_all()) == 0
    tag_repo.close(); queue.close(); repo.close()

