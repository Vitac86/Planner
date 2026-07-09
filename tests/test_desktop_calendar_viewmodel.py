"""Тесты CalendarViewModel: недельная навигация, список выбранного дня,
создание/правка/удаление с страницы календаря. Без окна и без сети.
"""
from datetime import date, datetime, timedelta

import pytest

from planner_desktop.domain.task import Task
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
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
def vm(service):
    return CalendarViewModel(service=service)


def add_task_on(service, day: date, title="Задача", hour=10):
    start = datetime(day.year, day.month, day.day, hour, 0)
    return service.create_task(Task(
        title=title, start=start, end=start + timedelta(hours=1),
        duration_minutes=60,
    ))


# ---- начальное состояние ------------------------------------------------------------

def test_starts_on_current_week_with_today_selected(vm):
    today = date.today()
    assert vm.isCurrentWeek is True
    assert vm.selectedIndex == today.weekday()
    assert vm.selectedDateText == today.strftime("%Y-%m-%d")
    assert len(vm.weekDays) == 7
    assert vm.weekDays[today.weekday()]["isToday"] is True
    assert vm.weekDays[today.weekday()]["isSelected"] is True


def test_week_title_format(vm):
    assert vm.weekTitle.startswith("Неделя ")
    assert "—" in vm.weekTitle


# ---- список выбранного дня ------------------------------------------------------------

def test_selected_day_tasks_lists_only_that_day(vm, service):
    today = date.today()
    add_task_on(service, today, title="Сегодняшняя")
    add_task_on(service, today + timedelta(days=14), title="Через две недели")

    titles = [row["title"] for row in vm.selectedDayTasks]
    assert titles == ["Сегодняшняя"]


def test_select_day_changes_task_list(vm, service):
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    add_task_on(service, monday, title="Понедельничная", hour=9)

    vm.selectDay(0)
    assert vm.selectedIndex == 0
    assert [row["title"] for row in vm.selectedDayTasks] == ["Понедельничная"]
    assert vm.selectedDateText == monday.strftime("%Y-%m-%d")


def test_week_days_show_task_counts(vm, service):
    today = date.today()
    add_task_on(service, today, title="a", hour=9)
    add_task_on(service, today, title="b", hour=11)
    assert vm.weekDays[today.weekday()]["taskCount"] == 2


# ---- навигация по неделям ----------------------------------------------------------------

def test_next_and_previous_week(vm):
    title_now = vm.weekTitle
    vm.nextWeek()
    assert vm.isCurrentWeek is False
    assert vm.weekTitle != title_now
    vm.previousWeek()
    assert vm.isCurrentWeek is True
    assert vm.weekTitle == title_now


def test_go_to_today_resets_week_and_selection(vm):
    vm.nextWeek()
    vm.selectDay(0)
    vm.goToToday()
    today = date.today()
    assert vm.isCurrentWeek is True
    assert vm.selectedIndex == today.weekday()
    assert vm.selectedDateText == today.strftime("%Y-%m-%d")


def test_navigation_finds_tasks_on_other_weeks(vm, service):
    future_day = date.today() + timedelta(days=7)
    add_task_on(service, future_day, title="Будущая")

    vm.nextWeek()
    vm.selectDay(future_day.weekday())
    assert [row["title"] for row in vm.selectedDayTasks] == ["Будущая"]


# ---- создание задачи на выбранный день -----------------------------------------------------

def test_save_editor_creates_task_for_selected_day(vm, queue):
    assert vm.saveEditor("", "Планёрка", "", 1, True, False,
                         vm.selectedDateText, "15:00", "30", False) is True
    titles = [row["title"] for row in vm.selectedDayTasks]
    assert titles == ["Планёрка"]
    assert len(queue.list_due_ops()) == 1  # create встал в очередь


def test_save_editor_invalid_sets_editor_error(vm):
    assert vm.saveEditor("", "", "", 0, False, False, "", "", "", False) is False
    assert vm.editorError != ""


# ---- действия над задачами с страницы календаря ----------------------------------------------

def test_toggle_and_delete_from_calendar(vm, service, repo):
    task = add_task_on(service, date.today(), title="Календарная")

    assert vm.toggleCompleted(task.uid) is True
    assert repo.get_by_uid(task.uid).completed is True

    assert vm.deleteTask(task.uid) is True
    assert vm.selectedDayTasks == []
    assert repo.get(task.id).is_deleted is True


def test_editor_data_for_from_calendar(vm, service):
    task = add_task_on(service, date.today(), title="Календарная")
    data = vm.editorDataFor(task.uid)
    assert data["exists"] is True
    assert data["scheduled"] is True
    assert data["dateText"] == date.today().strftime("%Y-%m-%d")


# ---- сигналы ------------------------------------------------------------------------------------

def test_mutations_emit_tasks_mutated(vm, service):
    mutations = []
    vm.tasksMutated.connect(lambda: mutations.append(1))
    task = add_task_on(service, date.today())
    vm.toggleCompleted(task.uid)
    vm.deleteTask(task.uid)
    assert len(mutations) == 2


def test_refresh_does_not_emit_tasks_mutated(vm):
    mutated = []
    vm.tasksMutated.connect(lambda: mutated.append(1))
    vm.refresh()
    assert mutated == []
