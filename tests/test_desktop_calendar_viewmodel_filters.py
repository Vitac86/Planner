"""Тесты фильтров агенды CalendarViewModel (все / активные / выполненные /
ежедневные), сводки выбранного дня и отметок ежедневных задач на выбранную
дату. Без окна и без сети.
"""
from datetime import date, datetime, timedelta

import pytest

from planner_desktop.domain.task import Task
from planner_desktop.repositories.daily_task_repository import (
    InMemoryDailyTaskRepository,
)
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.calendar_viewmodel import CalendarViewModel


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
    return CalendarViewModel(service=service, daily_service=daily_service)


def add_task_on(service, day: date, title="Задача", hour=10, completed=False):
    start = datetime(day.year, day.month, day.day, hour, 0)
    task = service.create_task(Task(
        title=title, start=start, end=start + timedelta(hours=1),
        duration_minutes=60,
    ))
    if completed:
        service.toggle_completed(task.uid)
    return task


# ---- фильтры агенды -------------------------------------------------------------------

def test_default_filter_shows_all(vm, service):
    today = date.today()
    add_task_on(service, today, "Активная", hour=9)
    add_task_on(service, today, "Готовая", hour=11, completed=True)

    assert vm.filterMode == "all"
    assert [r["title"] for r in vm.selectedDayTasks] == ["Активная", "Готовая"]


def test_active_and_completed_filters(vm, service):
    today = date.today()
    add_task_on(service, today, "Активная", hour=9)
    add_task_on(service, today, "Готовая", hour=11, completed=True)

    vm.setFilter("active")
    assert [r["title"] for r in vm.selectedDayTasks] == ["Активная"]

    vm.setFilter("completed")
    assert [r["title"] for r in vm.selectedDayTasks] == ["Готовая"]

    vm.setFilter("all")
    assert len(vm.selectedDayTasks) == 2


def test_invalid_filter_is_ignored(vm):
    changed = []
    vm.filterChanged.connect(lambda: changed.append(1))
    vm.setFilter("bogus")
    assert vm.filterMode == "all"
    assert changed == []


def test_daily_filter_empties_task_agenda(vm, service):
    add_task_on(service, date.today(), "Активная")
    vm.setFilter("daily")
    assert vm.selectedDayTasks == []


# ---- сводка выбранного дня (не зависит от фильтра) --------------------------------------

def test_summary_counts_ignore_filter(vm, service, daily_service):
    today = date.today()
    add_task_on(service, today, "Активная", hour=9)
    add_task_on(service, today, "Готовая", hour=11, completed=True)
    daily_service.create("Зарядка")

    vm.setFilter("completed")
    assert vm.selectedTaskTotal == 2
    assert vm.selectedActiveCount == 1
    assert vm.selectedCompletedCount == 1
    assert vm.selectedDailyCount == 1


# ---- ежедневные на выбранный день --------------------------------------------------------

def test_selected_day_daily_tasks_rows(vm, daily_service):
    daily_service.create("Зарядка", preferred_time="08:00")

    rows = vm.selectedDayDailyTasks
    assert len(rows) == 1
    assert rows[0]["title"] == "Зарядка"
    assert rows[0]["timeLabel"] == "08:00"
    assert rows[0]["done"] is False


def test_toggle_daily_for_selected_day_not_today(vm, daily_service):
    """Отметка ставится на ВЫБРАННЫЙ день, а не на сегодня."""
    result = daily_service.create("Зарядка")
    uid = result.task.uid

    today = date.today()
    other_index = (today.weekday() + 1) % 7  # другой день той же недели
    other_day = today - timedelta(days=today.weekday()) + timedelta(days=other_index)
    vm.selectDay(other_index)

    daily_mutations = []
    vm.dailyMutated.connect(lambda: daily_mutations.append(1))

    assert vm.toggleDailyCompleted(uid) is True
    assert daily_service.is_completed(uid, other_day) is True
    assert daily_service.is_completed(uid, today) is False
    assert daily_mutations == [1]

    row = vm.selectedDayDailyTasks[0]
    assert row["done"] is True


def test_toggle_daily_unknown_uid_returns_false(vm):
    mutations = []
    vm.dailyMutated.connect(lambda: mutations.append(1))
    assert vm.toggleDailyCompleted("no-such-uid") is False
    assert mutations == []


def test_daily_mask_respected_for_selected_day(vm, daily_service):
    """Ежедневная задача видна только в дни своей маски."""
    today = date.today()
    mask = 1 << today.weekday()  # только сегодняшний день недели
    daily_service.create("Только сегодня", weekdays_mask=mask)

    assert len(vm.selectedDayDailyTasks) == 1
    vm.selectDay((today.weekday() + 1) % 7)
    assert vm.selectedDayDailyTasks == []


# ---- сигналы ------------------------------------------------------------------------------

def test_refresh_daily_does_not_emit_mutated(vm):
    task_mutations, daily_mutations = [], []
    vm.tasksMutated.connect(lambda: task_mutations.append(1))
    vm.dailyMutated.connect(lambda: daily_mutations.append(1))
    vm.refreshDaily()
    assert task_mutations == []
    assert daily_mutations == []
