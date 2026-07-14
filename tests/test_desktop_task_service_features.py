"""Тесты расширенного use-case-слоя DesktopTaskService: форма редактора,
переходы расписания (schedule/unschedule) и их влияние на Calendar-очередь.

Чистый Python + SQLite во временном каталоге: без сети, без Google,
без окна. Старый app.db не участвует.
"""
from datetime import date, datetime, timedelta

import pytest

from planner_desktop.domain.commands import TaskEditorCommand
from planner_desktop.domain.task import Task
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.usecases.task_service import (
    DesktopTaskService,
    RESCHEDULE_RECURRING_ERROR,
    UNSCHEDULE_RECURRING_ERROR,
)


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "app_desktop.db"


@pytest.fixture()
def repo(db_path):
    repository = SQLiteTaskRepository(db_path)
    yield repository
    repository.close()


@pytest.fixture()
def queue(db_path):
    sync_store = CalendarSyncStore(db_path)
    yield sync_store
    sync_store.close()


@pytest.fixture()
def service(repo, queue):
    return DesktopTaskService(repo, calendar_queue=queue)


def ops(queue):
    return [(op.op, op.task_uid) for op in queue.list_due_ops()]


def editor(**kwargs):
    defaults = dict(title="Задача")
    defaults.update(kwargs)
    return TaskEditorCommand(**defaults)


def scheduled_editor(**kwargs):
    defaults = dict(
        title="Встреча",
        add_to_calendar=True,
        date_text="2026-07-08",
        time_text="10:30",
        duration_text="45",
    )
    defaults.update(kwargs)
    return TaskEditorCommand(**defaults)


# ---- создание через форму редактора -------------------------------------------

def test_create_undated_from_editor(service, repo, queue):
    result = service.create_from_editor(editor(notes="заметка", priority=2))
    assert result.ok
    saved = repo.get(result.task.id)
    assert saved.title == "Задача"
    assert saved.notes == "заметка"
    assert saved.priority == 2
    assert saved.start is None
    assert ops(queue) == []  # без даты — календарю не интересна


def test_create_scheduled_from_editor_enqueues_create(service, queue):
    result = service.create_from_editor(scheduled_editor())
    assert result.ok
    assert result.task.start == datetime(2026, 7, 8, 10, 30)
    assert result.task.duration_minutes == 45
    assert ops(queue) == [("create", result.task.uid)]


def test_create_all_day_from_editor(service, queue):
    result = service.create_from_editor(editor(
        title="Отпуск", add_to_calendar=True, is_all_day=True,
        date_text="2026-07-10",
    ))
    assert result.ok
    assert result.task.is_all_day is True
    assert result.task.start == datetime(2026, 7, 10, 0, 0)
    assert result.task.end == datetime(2026, 7, 11, 0, 0)
    assert ops(queue) == [("create", result.task.uid)]


def test_create_invalid_editor_returns_errors(service, repo, queue):
    result = service.create_from_editor(editor(title="   "))
    assert not result.ok
    assert result.errors
    assert repo.list_all() == []
    assert ops(queue) == []


def test_editor_priority_is_clamped(service, repo):
    result = service.create_from_editor(editor(priority=99))
    assert result.ok
    assert repo.get(result.task.id).priority == 3


# ---- правка через форму редактора ----------------------------------------------

def test_edit_text_fields_persists(service, repo):
    created = service.create_from_editor(editor()).task
    result = service.edit_task(created.uid, editor(
        title="Новое имя", notes="описание", priority=3, completed=True,
    ))
    assert result.ok
    saved = repo.get_by_uid(created.uid)
    assert saved.title == "Новое имя"
    assert saved.notes == "описание"
    assert saved.priority == 3
    assert saved.completed is True


def test_edit_scheduled_linked_task_enqueues_update(service, repo, queue):
    created = service.create_from_editor(scheduled_editor()).task
    queue.remove_op(queue.list_due_ops()[0].id)  # create «как будто допушен»
    created.google_calendar_event_id = "evt-1"
    repo.update(created)

    result = service.edit_task(created.uid, scheduled_editor(
        title="Встреча (перенос)", time_text="12:00",
    ))
    assert result.ok
    assert result.task.start == datetime(2026, 7, 8, 12, 0)
    assert ops(queue) == [("update", created.uid)]


def test_edit_linked_title_only_enqueues_update(service, repo, queue):
    created = service.create_from_editor(scheduled_editor()).task
    queue.remove_op(queue.list_due_ops()[0].id)
    created.google_calendar_event_id = "evt-title"
    repo.update(created)

    result = service.edit_task(created.uid, scheduled_editor(title="Новое имя"))
    assert result.ok
    assert ops(queue) == [("update", created.uid)]


def test_edit_linked_priority_and_completion_stay_local(service, repo, queue):
    created = service.create_from_editor(scheduled_editor()).task
    queue.remove_op(queue.list_due_ops()[0].id)
    created.google_calendar_event_id = "evt-local-fields"
    repo.update(created)

    result = service.edit_task(created.uid, scheduled_editor(
        priority=3, completed=True,
    ))
    assert result.ok
    saved = repo.get_by_uid(created.uid)
    assert saved.priority == 3
    assert saved.completed is True
    assert ops(queue) == []


def test_recurring_text_and_local_fields_edit_preserves_schedule(
        service, repo, queue):
    created = service.create_from_editor(scheduled_editor()).task
    queue.remove_op(queue.list_due_ops()[0].id)
    created.google_calendar_event_id = "evt-rec"
    created.google_calendar_recurring_event_id = "series-rec"
    repo.update(created)
    before = (created.start, created.end, created.duration_minutes, created.is_all_day)

    result = service.edit_task(created.uid, scheduled_editor(
        title="Новое имя", notes="Новые заметки", priority=2, completed=True,
    ))
    assert result.ok
    saved = repo.get_by_uid(created.uid)
    assert (saved.start, saved.end, saved.duration_minutes, saved.is_all_day) == before
    assert (saved.title, saved.notes, saved.priority, saved.completed) == (
        "Новое имя", "Новые заметки", 2, True,
    )
    assert ops(queue) == [("update", created.uid)]  # summary/description only


@pytest.mark.parametrize("changes", [
    {"date_text": "2026-07-09"},
    {"time_text": "11:00"},
    {"duration_text": "90"},
    {"is_all_day": True, "time_text": "", "duration_text": ""},
])
def test_recurring_editor_rejects_every_schedule_change(
        changes, service, repo, queue):
    created = service.create_from_editor(scheduled_editor()).task
    queue.remove_op(queue.list_due_ops()[0].id)
    created.google_calendar_event_id = "evt-rec"
    created.google_calendar_recurring_event_id = "series-rec"
    repo.update(created)
    before = (created.start, created.end, created.duration_minutes, created.is_all_day)

    result = service.edit_task(created.uid, scheduled_editor(**changes))
    assert not result.ok
    assert result.errors == [RESCHEDULE_RECURRING_ERROR]
    saved = repo.get_by_uid(created.uid)
    assert (saved.start, saved.end, saved.duration_minutes, saved.is_all_day) == before
    assert ops(queue) == []


def test_edit_moves_date_and_time(service, repo):
    created = service.create_from_editor(scheduled_editor()).task
    result = service.edit_task(created.uid, scheduled_editor(
        date_text="2026-07-15", time_text="09:00", duration_text="30",
    ))
    assert result.ok
    saved = repo.get_by_uid(created.uid)
    assert saved.start == datetime(2026, 7, 15, 9, 0)
    assert saved.end == saved.start + timedelta(minutes=30)


def test_edit_undated_to_scheduled_enqueues_create(service, queue):
    created = service.create_from_editor(editor()).task
    assert ops(queue) == []

    result = service.edit_task(created.uid, scheduled_editor(title="Задача"))
    assert result.ok
    assert ops(queue) == [("create", created.uid)]


def test_edit_invalid_input_keeps_task_unchanged(service, repo, queue):
    created = service.create_from_editor(scheduled_editor()).task
    result = service.edit_task(created.uid, scheduled_editor(title=""))
    assert not result.ok
    assert result.errors
    saved = repo.get_by_uid(created.uid)
    assert saved.title == "Встреча"
    assert ops(queue) == [("create", created.uid)]  # только исходный create


def test_edit_missing_task_returns_error(service):
    result = service.edit_task("нет-такого-uid", editor())
    assert not result.ok
    assert result.errors


# ---- unschedule: запланирована -> без даты ---------------------------------------

def test_edit_unschedules_unpushed_task_cancels_create(service, repo, queue):
    created = service.create_from_editor(scheduled_editor()).task
    assert ops(queue) == [("create", created.uid)]

    result = service.edit_task(created.uid, editor(title="Встреча"))
    assert result.ok
    saved = repo.get_by_uid(created.uid)
    assert saved.start is None
    assert saved.is_all_day is False
    assert ops(queue) == []  # события не было — недопушенный create снят


def test_edit_unschedules_linked_task_enqueues_event_delete(service, repo, queue):
    created = service.create_from_editor(scheduled_editor()).task
    queue.remove_op(queue.list_due_ops()[0].id)
    created.google_calendar_event_id = "evt-7"
    created.google_calendar_etag = "etag-7"
    repo.update(created)

    result = service.edit_task(created.uid, editor(title="Встреча"))
    assert result.ok
    saved = repo.get_by_uid(created.uid)
    assert saved.start is None
    assert saved.google_calendar_event_id is None  # задача отвязана
    pending = queue.list_due_ops()
    assert [(op.op, op.task_uid) for op in pending] == [("delete", created.uid)]
    # push-движок возьмёт event_id из payload — задача уже отвязана
    assert "evt-7" in (pending[0].payload_json or "")


def test_unschedule_recurring_instance_is_refused(service, repo, queue):
    created = service.create_from_editor(scheduled_editor()).task
    queue.remove_op(queue.list_due_ops()[0].id)
    created.google_calendar_event_id = "evt-9"
    created.google_calendar_recurring_event_id = "recurring-9"
    repo.update(created)

    result = service.edit_task(created.uid, editor(title="Встреча"))
    assert not result.ok
    assert result.errors == [UNSCHEDULE_RECURRING_ERROR]
    saved = repo.get_by_uid(created.uid)
    assert saved.start is not None  # задача не изменилась
    assert ops(queue) == []


def test_unschedule_task_direct(service, repo, queue):
    created = service.create_from_editor(scheduled_editor()).task
    result = service.unschedule_task(created.uid)
    assert result.ok
    assert repo.get_by_uid(created.uid).start is None
    assert ops(queue) == []


def test_unschedule_undated_task_is_noop(service):
    created = service.create_from_editor(editor()).task
    result = service.unschedule_task(created.uid)
    assert result.ok
    assert result.task.start is None


# ---- schedule_task: назначение даты ----------------------------------------------

def test_schedule_task_sets_fields_and_enqueues_create(service, repo, queue):
    created = service.create_from_editor(editor()).task
    start = datetime(2026, 7, 20, 15, 0)

    updated = service.schedule_task(created.uid, start, duration_minutes=90)
    assert updated is not None
    assert updated.start == start
    assert updated.end == start + timedelta(minutes=90)
    assert updated.duration_minutes == 90
    assert ops(queue) == [("create", created.uid)]


def test_schedule_task_all_day(service, queue):
    created = service.create_from_editor(editor()).task
    start = datetime(2026, 7, 21, 0, 0)

    updated = service.schedule_task(created.uid, start, is_all_day=True)
    assert updated.is_all_day is True
    assert updated.end == start + timedelta(days=1)
    assert updated.duration_minutes is None


def test_schedule_task_all_day_normalizes_midnight(service):
    created = service.create_from_editor(editor()).task
    updated = service.schedule_task(
        created.uid, datetime(2026, 7, 21, 15, 45), is_all_day=True
    )
    assert updated.start == datetime(2026, 7, 21)
    assert updated.end == datetime(2026, 7, 22)


@pytest.mark.parametrize("duration", [0, -1])
def test_schedule_task_rejects_non_positive_duration(
        duration, service, repo, queue):
    created = service.create_from_editor(editor()).task
    assert service.schedule_task(
        created.uid, datetime(2026, 7, 21, 10, 0),
        duration_minutes=duration,
    ) is None
    assert repo.get_by_uid(created.uid).start is None
    assert ops(queue) == []


def test_public_schedule_task_refuses_recurring_instance(
        service, repo, queue):
    created = service.create_from_editor(scheduled_editor()).task
    queue.remove_op(queue.list_due_ops()[0].id)
    created.google_calendar_event_id = "evt-rec"
    created.google_calendar_recurring_event_id = "series-rec"
    repo.update(created)
    before = created.start

    assert service.schedule_task(
        created.uid, datetime(2026, 7, 25, 18, 0), duration_minutes=60
    ) is None
    assert repo.get_by_uid(created.uid).start == before
    assert ops(queue) == []


def test_schedule_then_unschedule_is_deterministic(service, repo, queue):
    """schedule -> unschedule возвращает задачу в исходное локальное состояние
    и не оставляет операций в очереди (событие так и не было создано)."""
    created = service.create_from_editor(editor()).task
    service.schedule_task(created.uid, datetime(2026, 7, 22, 9, 0))
    assert ops(queue) == [("create", created.uid)]

    service.unschedule_task(created.uid)
    saved = repo.get_by_uid(created.uid)
    assert saved.start is None
    assert saved.end is None
    assert saved.duration_minutes is None
    assert ops(queue) == []


# ---- галочка и удаление ------------------------------------------------------------

def test_complete_task_persists_across_reopen(service, db_path):
    created = service.create_from_editor(editor()).task
    assert service.complete_task(created.id) is True

    reopened = SQLiteTaskRepository(db_path)
    try:
        assert reopened.get(created.id).completed is True
    finally:
        reopened.close()


def test_delete_by_uid_tombstones_and_enqueues_delete(service, repo, queue):
    created = service.create_from_editor(scheduled_editor()).task
    queue.remove_op(queue.list_due_ops()[0].id)
    created.google_calendar_event_id = "evt-3"
    repo.update(created)

    assert service.delete_task_by_uid(created.uid) is True
    assert repo.get(created.id).is_deleted is True  # тумбстоун, не стирание
    assert ops(queue) == [("delete", created.uid)]


def test_delete_by_uid_unknown_returns_false(service):
    assert service.delete_task_by_uid("нет-такого") is False


# ---- статистика очереди --------------------------------------------------------------

def test_queue_stats_reported(service, queue):
    a = service.create_from_editor(scheduled_editor()).task
    b = service.create_from_editor(scheduled_editor(title="Вторая")).task
    assert service.count_pending_ops() == 2
    assert service.pending_task_uids() == {a.uid, b.uid}
    assert service.count_terminal_ops() == 0

    queue.mark_terminal(queue.list_due_ops()[0].id, "постоянная ошибка")
    assert service.count_pending_ops() == 1
    assert service.count_terminal_ops() == 1


def test_service_without_queue_reports_zero_stats(repo):
    service = DesktopTaskService(repo)
    assert service.has_sync_queue is False
    assert service.count_pending_ops() == 0
    assert service.count_terminal_ops() == 0
    assert service.pending_task_uids() == set()
    assert service.sync_cursor() is None
