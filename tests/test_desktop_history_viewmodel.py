"""Тесты HistoryViewModel: строки для QML, фильтр диапазона, «вернуть
в работу», контракт общего редактора. Без окна и без сети.
"""
from datetime import date, datetime, timedelta, timezone

import pytest

from planner_desktop.domain.daily_task import DailyTask
from planner_desktop.domain.task import Task
from planner_desktop.repositories.daily_task_repository import (
    InMemoryDailyTaskRepository,
)
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.history_viewmodel import HistoryViewModel


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
    return HistoryViewModel(service, daily_service)


def add_completed(repo, title, done_on: date, hour=12):
    task = repo.add(Task(title=title))
    task.set_completed(
        True,
        when=datetime(done_on.year, done_on.month, done_on.day, hour,
                      0, tzinfo=timezone.utc),
    )
    repo.update(task)
    return task


# ---- строки для QML -----------------------------------------------------------------

def test_empty_history(vm):
    assert vm.isEmpty is True
    assert vm.totalCount == 0
    assert vm.groups == []


def test_groups_expose_qml_friendly_rows(vm, repo):
    today = date.today()
    add_completed(repo, "Готовая", today)

    groups = vm.groups
    assert len(groups) == 1
    group = groups[0]
    assert group["dateISO"] == today.isoformat()
    assert group["relLabel"] == "Сегодня"
    assert group["count"] == 1

    entry = group["entries"][0]
    assert entry["title"] == "Готовая"
    assert entry["kind"] == "task"
    assert entry["isDaily"] is False
    assert entry["canReopen"] is True
    assert "priorityLabel" in entry and "doneAt" in entry
    assert vm.totalCount == 1
    assert vm.isEmpty is False


def test_yesterday_gets_relative_label(vm, repo):
    add_completed(repo, "Вчерашняя", date.today() - timedelta(days=1))
    assert vm.groups[0]["relLabel"] == "Вчера"


# ---- фильтр диапазона ---------------------------------------------------------------

def test_default_range_is_7_days(vm):
    assert vm.rangeDays == 7


def test_set_range_filters_history(vm, repo):
    today = date.today()
    add_completed(repo, "Свежая", today)
    add_completed(repo, "Старая", today - timedelta(days=40))

    assert vm.totalCount == 1  # 7 дней по умолчанию
    vm.setRange(0)
    assert vm.rangeDays == 0
    assert vm.totalCount == 2


def test_set_range_rejects_unknown_values(vm):
    changed = []
    vm.rangeChanged.connect(lambda: changed.append(1))
    vm.setRange(13)
    assert vm.rangeDays == 7
    assert changed == []


# ---- «вернуть в работу» ---------------------------------------------------------------

def test_reopen_task_uncompletes_and_refreshes(vm, repo):
    task = add_completed(repo, "Готовая", date.today())
    mutated, toasts = [], []
    vm.tasksMutated.connect(lambda: mutated.append(1))
    vm.toastMessage.connect(toasts.append)

    assert vm.reopenTask(task.uid) is True
    stored = repo.get_by_uid(task.uid)
    assert stored.completed is False
    assert stored.completed_at is None
    assert vm.isEmpty is True
    assert len(mutated) == 1
    assert toasts != []


def test_reopen_rejects_unknown_and_uncompleted(vm, repo, service):
    open_task = service.create_task(Task(title="В работе"))
    assert vm.reopenTask("no-such-uid") is False
    assert vm.reopenTask(open_task.uid) is False


def test_reopen_rejects_daily_uid(vm, daily_service):
    result = daily_service.create("Зарядка")
    daily_service.set_completed(result.task.uid, date.today(), True)
    # uid ежедневной задачи не является разовой задачей — реопен невозможен
    assert vm.reopenTask(result.task.uid) is False
    assert vm.totalCount == 1  # отметка осталась в журнале


# ---- контракт общего редактора --------------------------------------------------------

def test_editor_contract_matches_other_pages(vm, repo):
    task = add_completed(repo, "Готовая", date.today())

    data = vm.editorDataFor(task.uid)
    assert data["exists"] is True
    assert data["completed"] is True

    assert vm.saveEditor(task.uid, "Переименованная", "", 2, False, False,
                         "", "", "", True) is True
    assert repo.get_by_uid(task.uid).title == "Переименованная"


def test_save_editor_invalid_sets_error(vm, repo):
    task = add_completed(repo, "Готовая", date.today())
    assert vm.saveEditor(task.uid, "", "", 0, False, False,
                         "", "", "", True) is False
    assert vm.editorError != ""
    vm.clearEditorError()
    assert vm.editorError == ""


# ---- сигналы --------------------------------------------------------------------------

def test_refresh_does_not_emit_tasks_mutated(vm):
    mutated = []
    vm.tasksMutated.connect(lambda: mutated.append(1))
    vm.refresh()
    assert mutated == []
