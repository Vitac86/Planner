"""Тесты новых возможностей TodayViewModel фазы 2: ежедневный чек-лист на
сегодня, умный Quick Add (addQuick) и Quick Add с приоритетом
(addTaskDetailed). QObject работает без QApplication.
"""
from datetime import date

import pytest

from planner_desktop.domain.daily_task import ALL_WEEKDAYS_MASK
from planner_desktop.repositories.daily_task_repository import (
    InMemoryDailyTaskRepository,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel


@pytest.fixture()
def daily_service():
    return DailyTaskService(InMemoryDailyTaskRepository())


@pytest.fixture()
def vm(daily_service):
    return TodayViewModel(FakeTaskRepository(seed=False), daily_service=daily_service)


# ---- ежедневный чек-лист на сегодня ------------------------------------------

def test_daily_tasks_show_todays_occurrences(vm, daily_service):
    # ALL_WEEKDAYS_MASK гарантирует появление независимо от дня недели
    daily_service.create("Зарядка", weekdays_mask=ALL_WEEKDAYS_MASK,
                         preferred_time="08:00")
    rows = vm.dailyTasks
    assert [r["title"] for r in rows] == ["Зарядка"]
    assert rows[0]["done"] is False
    assert rows[0]["timeLabel"] == "08:00"
    assert vm.dailyTotalCount == 1
    assert vm.dailyDoneCount == 0


def test_toggle_daily_marks_done_today(vm, daily_service):
    task = daily_service.create("Итоги", weekdays_mask=ALL_WEEKDAYS_MASK).task
    assert vm.toggleDaily(task.uid) is True
    assert vm.dailyTasks[0]["done"] is True
    assert vm.dailyDoneCount == 1
    # снятие
    assert vm.toggleDaily(task.uid) is True
    assert vm.dailyTasks[0]["done"] is False


def test_toggle_daily_emits_daily_mutated(vm, daily_service):
    task = daily_service.create("X", weekdays_mask=ALL_WEEKDAYS_MASK).task
    mutated = []
    vm.dailyMutated.connect(lambda: mutated.append(1))
    vm.toggleDaily(task.uid)
    assert mutated == [1]


def test_toggle_unknown_daily_returns_false(vm):
    assert vm.toggleDaily("нет-такого") is False


def test_disabled_daily_absent_from_today(vm, daily_service):
    daily_service.create("Скрыто", weekdays_mask=ALL_WEEKDAYS_MASK, enabled=False)
    assert vm.dailyTasks == []


# ---- умный Quick Add ----------------------------------------------------------

def test_add_quick_parses_time(vm):
    assert vm.addQuick("Отчет 15:00", 2) is True
    task = vm.repository.all()[0]
    assert task.title == "Отчет"
    assert task.start is not None
    assert task.start.date() == date.today()
    assert task.start.hour == 15
    assert task.priority == 2


def test_add_quick_plain_text_is_undated(vm):
    assert vm.addQuick("Купить хлеб", 0) is True
    task = vm.repository.all()[0]
    assert task.title == "Купить хлеб"
    assert task.start is None


def test_add_quick_empty_is_rejected(vm):
    assert vm.addQuick("   ", 0) is False
    assert vm.errorMessage != ""
    assert vm.repository.all() == []


# ---- Quick Add с явными полями и приоритетом ----------------------------------

def test_add_task_detailed_sets_priority(vm):
    assert vm.addTaskDetailed("Встреча", "заметка", 3, True, False,
                              "2026-07-08", "10:30", "45") is True
    task = vm.repository.all()[0]
    assert task.title == "Встреча"
    assert task.priority == 3
    assert task.duration_minutes == 45


def test_legacy_add_task_still_works(vm):
    # Обратная совместимость со старым 7-арг слотом (используется тестами).
    assert vm.addTask("Просто", "", False, False, "", "", "") is True
    assert vm.repository.all()[0].title == "Просто"
