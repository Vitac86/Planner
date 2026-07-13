"""Тесты SettingsViewModel и статистики очереди CalendarSyncStore:
разбивка ожидающих операций по типу, «последнее локальное изменение»,
диагностика. Без окна и без сети; ручной синк всегда выключен.
"""
from datetime import datetime, timedelta

import pytest

from planner_desktop.domain.task import Task
from planner_desktop.repositories.daily_task_repository import (
    InMemoryDailyTaskRepository,
)
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.schema import SCHEMA_VERSION
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel


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


@pytest.fixture()
def daily_service():
    return DailyTaskService(InMemoryDailyTaskRepository(seed=False))


@pytest.fixture()
def vm(service, daily_service):
    return SettingsViewModel(service, daily_service=daily_service)


def add_scheduled(service, title="Встреча", days=0):
    start = (datetime.now() + timedelta(days=days)).replace(
        hour=10, minute=0, second=0, microsecond=0)
    return service.create_task(Task(
        title=title, start=start, end=start + timedelta(hours=1),
        duration_minutes=60,
    ))


# ---- CalendarSyncStore: статистика очереди --------------------------------------------

def test_count_pending_by_op_breakdown(queue):
    assert queue.count_pending_by_op() == {"create": 0, "update": 0, "delete": 0}

    queue.enqueue_create("uid-a")
    queue.enqueue_create("uid-b")
    queue.enqueue_delete("uid-c", payload={"event_id": "evt"})
    assert queue.count_pending_by_op() == {"create": 2, "update": 0, "delete": 1}


def test_latest_pending_created_at(queue):
    assert queue.latest_pending_created_at() is None
    queue.enqueue_create("uid-a")
    stamp = queue.latest_pending_created_at()
    assert stamp is not None


# ---- SettingsViewModel: свойства ------------------------------------------------------

def test_pending_breakdown_properties(vm, service, queue):
    add_scheduled(service, "Первая")
    add_scheduled(service, "Вторая", days=1)
    queue.enqueue_delete("uid-x", payload={"event_id": "evt"})

    assert vm.pendingOpsCount == 3
    assert vm.pendingCreateCount == 2
    assert vm.pendingUpdateCount == 0
    assert vm.pendingDeleteCount == 1
    assert vm.terminalOpsCount == 0


def test_last_local_change_placeholder_and_value(vm, service):
    assert vm.lastLocalChange == "—"
    add_scheduled(service)
    assert vm.lastLocalChange != "—"


def test_diagnostics_counters(vm, service, daily_service):
    task = add_scheduled(service)
    service.create_task(Task(title="Локальная без даты"))
    daily_service.create("Зарядка")

    assert vm.schemaVersion == SCHEMA_VERSION
    assert vm.taskCount == 2
    assert vm.dailyTaskCount == 1

    # тумбстоун исключается из числа активных
    service.delete_task_by_uid(task.uid)
    assert vm.taskCount == 1


def test_diagnostics_text_mentions_key_facts(vm, service):
    add_scheduled(service)
    text = vm.diagnosticsText
    assert "Путь БД" in text
    assert f"Версия схемы: {SCHEMA_VERSION}" in text
    assert "Операций в очереди: 1" in text
    assert "Dead-letter: 0" in text


def test_manual_sync_is_disabled_without_real_gateway(vm):
    """Жёсткое требование фазы: реального Google-шлюза нет, автосинка нет —
    кнопка ручного синка всегда выключена."""
    assert vm.manualSyncEnabled is False
    assert vm.manualSyncNote != ""


def test_refresh_emits_state_changed(vm):
    changed = []
    vm.stateChanged.connect(lambda: changed.append(1))
    vm.refresh()
    assert changed == [1]
