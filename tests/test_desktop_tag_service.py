from datetime import datetime, timedelta

import pytest

from planner_desktop.domain.tags import (
    MAX_TAGS_PER_TASK,
    TagLimitError,
    TagNameConflictError,
)
from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.tag_repository import InMemoryTagRepository
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.storage.tag_repository import SQLiteTagRepository
from planner_desktop.usecases.tag_service import TagService
from planner_desktop.usecases.task_service import DesktopTaskService


def _memory_service():
    tasks = FakeTaskRepository(seed=False)
    task = tasks.add(Task(title="Задача"))
    return TagService(InMemoryTagRepository(), tasks), task


def test_cyrillic_casefold_uniqueness():
    service, _ = _memory_service()
    first = service.create("Проект")
    with pytest.raises(TagNameConflictError):
        service.create("  ПРОЕКТ ")
    assert service.get_or_create("проект").id == first.id


def test_rename_preserves_assignment_and_delete_only_unlinks():
    service, task = _memory_service()
    tag = service.create("Работа")
    service.set_task_tags(task.uid, [tag.id])
    renamed = service.rename(tag.id, "Проект")
    assert renamed.id == tag.id
    assert [item.name for item in service.tags_for_task(task.uid)] == ["Проект"]
    assert service.delete(tag.id) is True
    assert service.tags_for_task(task.uid) == []
    assert service.task_repository.get_by_uid(task.uid).title == "Задача"


def test_maximum_tags_per_task_is_deterministic():
    service, task = _memory_service()
    tags = [service.create(f"Тег {index}") for index in range(MAX_TAGS_PER_TASK + 1)]
    service.set_task_tags(task.uid, [tag.id for tag in tags[:MAX_TAGS_PER_TASK]])
    with pytest.raises(TagLimitError):
        service.set_task_tags(task.uid, [tag.id for tag in tags])
    assert len(service.tags_for_task(task.uid)) == MAX_TAGS_PER_TASK


def test_tag_only_edits_enqueue_no_calendar_operation(tmp_path):
    db_path = tmp_path / "desktop.db"
    tasks = SQLiteTaskRepository(db_path)
    queue = CalendarSyncStore(db_path)
    task_service = DesktopTaskService(tasks, calendar_queue=queue)
    start = datetime(2026, 7, 14, 9, 0)
    task = task_service.create_task(Task(
        title="Связанная", start=start, end=start + timedelta(hours=1),
        google_calendar_event_id="event-1",
    ))
    for op in queue.list_due_ops():
        queue.remove_op(op.id)

    tag_repo = SQLiteTagRepository(db_path)
    tags = TagService(tag_repo, tasks)
    tag = tags.create("Локальный")
    tags.set_task_tags(task.uid, [tag.id])
    tags.rename(tag.id, "Только Planner")
    tags.remove_tag(task.uid, tag.id)

    assert queue.list_due_ops() == []
    assert tasks.get_by_uid(task.uid).google_calendar_event_id == "event-1"
    tag_repo.close()
    queue.close()
    tasks.close()
