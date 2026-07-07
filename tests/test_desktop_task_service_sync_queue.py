"""Тесты use-case-слоя DesktopTaskService и его интеграции с TodayViewModel.

Проверяется одно: локальные CRUD-операции ставят правильные Calendar-операции
в локальную очередь (или не ставят). Никакой сети и Google API —
push выполняет движок отдельно и здесь не участвует.
"""
from datetime import datetime, timedelta

import pytest

from planner_desktop.domain.task import Task
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel


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


def timed_task(**kwargs):
    defaults = dict(
        title="Встреча",
        start=datetime(2026, 7, 8, 10, 30),
        end=datetime(2026, 7, 8, 11, 30),
        duration_minutes=60,
    )
    defaults.update(kwargs)
    return Task(**defaults)


def ops(queue):
    return [(op.op, op.task_uid) for op in queue.list_due_ops()]


# ---- create ------------------------------------------------------------------

def test_create_scheduled_task_enqueues_calendar_create(service, queue):
    task = service.create_task(timed_task())
    assert ops(queue) == [("create", task.uid)]


def test_create_all_day_task_enqueues_calendar_create(service, queue):
    task = service.create_task(Task(
        title="Отпуск",
        start=datetime(2026, 7, 10, 0, 0),
        end=datetime(2026, 7, 11, 0, 0),
        is_all_day=True,
    ))
    assert ops(queue) == [("create", task.uid)]


def test_create_undated_task_enqueues_nothing(service, queue):
    service.create_task(Task(title="Без даты"))
    assert ops(queue) == []


# ---- update ------------------------------------------------------------------

def test_update_task_with_event_id_enqueues_update(service, repo, queue):
    task = service.create_task(timed_task())
    queue.remove_op(queue.list_due_ops()[0].id)  # create «как будто допушен»
    task.google_calendar_event_id = "evt-1"
    task.title = "Встреча (перенос)"
    service.update_task(task)
    assert ops(queue) == [("update", task.uid)]


def test_update_scheduled_task_without_event_id_enqueues_create(service, queue):
    task = service.create_task(Task(title="Черновик"))  # без даты — очередь пуста
    task.start = datetime(2026, 7, 9, 9, 0)
    task.end = task.start + timedelta(hours=1)
    service.update_task(task)
    assert ops(queue) == [("create", task.uid)]


def test_update_while_create_pending_keeps_single_op(service, queue):
    task = service.create_task(timed_task())
    task.title = "Правка до push-а"
    service.update_task(task)
    assert ops(queue) == [("create", task.uid)]  # одной операции достаточно


# ---- complete: локально, без календаря -------------------------------------------

def test_complete_task_enqueues_nothing(service, repo, queue):
    task = service.create_task(timed_task())
    queue.remove_op(queue.list_due_ops()[0].id)
    assert service.complete_task(task.id) is True
    assert repo.get(task.id).completed is True
    assert ops(queue) == []  # галочка не уходит в календарь (решение фазы 1)


def test_toggle_completed_enqueues_nothing(service, repo, queue):
    task = service.create_task(timed_task())
    queue.remove_op(queue.list_due_ops()[0].id)
    assert service.toggle_completed(task.uid) is True
    assert ops(queue) == []


# ---- delete ------------------------------------------------------------------

def test_delete_synced_task_enqueues_delete(service, repo, queue):
    task = service.create_task(timed_task())
    queue.remove_op(queue.list_due_ops()[0].id)
    task.google_calendar_event_id = "evt-1"
    repo.update(task)

    assert service.delete_task(task.id) is True
    assert ops(queue) == [("delete", task.uid)]
    assert repo.get(task.id).is_deleted is True  # тумбстоун, не стирание


def test_delete_replaces_pending_update_with_delete(service, repo, queue):
    task = service.create_task(timed_task())
    queue.remove_op(queue.list_due_ops()[0].id)
    task.google_calendar_event_id = "evt-1"
    service.update_task(task)
    assert ops(queue) == [("update", task.uid)]

    service.delete_task(task.id)
    assert ops(queue) == [("delete", task.uid)]


def test_delete_never_synced_task_cancels_pending_create(service, repo, queue):
    task = service.create_task(timed_task())
    assert ops(queue) == [("create", task.uid)]

    service.delete_task(task.id)
    assert ops(queue) == []  # события не было — и delete не нужен
    assert repo.get(task.id).is_deleted is True


def test_service_without_queue_works(repo):
    """Сервис без очереди (демо-режим/старые тесты) просто делает CRUD."""
    service = DesktopTaskService(repo)
    task = service.create_task(timed_task())
    assert repo.get(task.id) is not None
    assert service.delete_task(task.id) is True


# ---- интеграция с TodayViewModel (Quick Add) ---------------------------------------

def test_quick_add_scheduled_task_enqueues_calendar_create(service, repo, queue):
    vm = TodayViewModel(service=service)
    assert vm.addTask("Встреча", "", True, False,
                      "2026-07-08", "10:30", "45") is True
    task = repo.list_all()[0]
    assert ops(queue) == [("create", task.uid)]


def test_quick_add_all_day_task_enqueues_calendar_create(service, repo, queue):
    vm = TodayViewModel(service=service)
    assert vm.addTask("Отпуск", "", True, True, "2026-07-10", "", "") is True
    task = repo.list_all()[0]
    assert task.is_all_day is True
    assert ops(queue) == [("create", task.uid)]


def test_quick_add_undated_task_enqueues_nothing(service, queue):
    vm = TodayViewModel(service=service)
    assert vm.addTask("Купить хлеб", "", False, False, "", "", "") is True
    assert ops(queue) == []


def test_quick_add_invalid_input_enqueues_nothing(service, queue):
    vm = TodayViewModel(service=service)
    assert vm.addTask("", "", True, False, "2026-07-08", "10:30", "") is False
    assert ops(queue) == []


def test_viewmodel_without_service_still_works():
    """Обратная совместимость: старый вызов TodayViewModel(repository)."""
    from planner_desktop.repositories.fake_task_repository import FakeTaskRepository

    vm = TodayViewModel(FakeTaskRepository(seed=False))
    assert vm.addTask("Задача", "", False, False, "", "", "") is True
    assert len(vm.repository.all()) == 1
