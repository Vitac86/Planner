"""Тесты DailyTasksViewModel: CRUD ежедневных задач, сигналы, валидация.
QObject работает без QApplication — окно не открывается.
"""
import pytest

from planner_desktop.domain.daily_task import ALL_WEEKDAYS_MASK
from planner_desktop.repositories.daily_task_repository import (
    InMemoryDailyTaskRepository,
)
from planner_desktop.usecases.daily_task_service import DailyTaskService
from planner_desktop.viewmodels.daily_tasks_viewmodel import DailyTasksViewModel


@pytest.fixture()
def vm():
    return DailyTasksViewModel(DailyTaskService(InMemoryDailyTaskRepository()))


def test_save_creates_item(vm):
    assert vm.save("", "Зарядка", "", True, ALL_WEEKDAYS_MASK, "08:00") is True
    assert vm.count == 1
    row = vm.items[0]
    assert row["title"] == "Зарядка"
    assert row["timeText"] == "08:00"
    assert row["weekdaysText"] == "Каждый день"


def test_save_invalid_sets_editor_error(vm):
    assert vm.save("", "", "", True, ALL_WEEKDAYS_MASK, "") is False
    assert vm.editorError != ""
    assert vm.count == 0
    # успешное сохранение очищает ошибку
    assert vm.save("", "Ok", "", True, ALL_WEEKDAYS_MASK, "") is True
    assert vm.editorError == ""


def test_save_rejects_empty_weekday_mask(vm):
    assert vm.save("", "Ничего", "", True, 0, "") is False
    assert vm.editorError != ""
    assert vm.count == 0


def test_edit_existing(vm):
    vm.save("", "Старое", "", True, ALL_WEEKDAYS_MASK, "")
    uid = vm.items[0]["uid"]
    assert vm.save(uid, "Новое", "заметка", False, 0b0000001, "07:30") is True
    row = vm.items[0]
    assert row["title"] == "Новое"
    assert row["enabled"] is False
    assert row["weekdaysMask"] == 0b0000001
    assert row["timeText"] == "07:30"


def test_editor_data_for_roundtrip(vm):
    vm.save("", "Пункт", "n", True, 0b0000101, "09:00")
    uid = vm.items[0]["uid"]
    data = vm.editorDataFor(uid)
    assert data["exists"] is True
    assert data["title"] == "Пункт"
    assert data["weekdaysMask"] == 0b0000101
    assert data["timeText"] == "09:00"


def test_editor_data_for_missing(vm):
    data = vm.editorDataFor("нет-такого")
    assert data["exists"] is False
    assert data["weekdaysMask"] == ALL_WEEKDAYS_MASK


def test_set_enabled_toggles(vm):
    vm.save("", "Пункт", "", True, ALL_WEEKDAYS_MASK, "")
    uid = vm.items[0]["uid"]
    assert vm.setEnabled(uid, False) is True
    assert vm.items[0]["enabled"] is False


def test_remove(vm):
    vm.save("", "Пункт", "", True, ALL_WEEKDAYS_MASK, "")
    uid = vm.items[0]["uid"]
    assert vm.remove(uid) is True
    assert vm.count == 0
    assert vm.remove(uid) is False


def test_mutations_emit_signals(vm):
    mutated, toasts = [], []
    vm.mutated.connect(lambda: mutated.append(1))
    vm.toastMessage.connect(toasts.append)
    vm.save("", "Пункт", "", True, ALL_WEEKDAYS_MASK, "")
    uid = vm.items[0]["uid"]
    vm.remove(uid)
    assert len(mutated) == 2
    assert toasts == ["Ежедневная задача сохранена", "Ежедневная задача удалена"]
