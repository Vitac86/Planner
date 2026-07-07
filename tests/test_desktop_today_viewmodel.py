"""Тесты Quick Add нового десктопа (planner_desktop).

Чистый Python: QObject создаётся без QApplication, никакое окно не
открывается. Проверяем и доменную валидацию (domain/commands.py),
и её QML-обёртку (TodayViewModel.addTask).
"""
from datetime import datetime, time, timedelta

import pytest

from planner_desktop.domain.commands import (
    QuickAddCommand,
    execute_quick_add,
    validate_quick_add,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel


@pytest.fixture()
def vm():
    return TodayViewModel(FakeTaskRepository(seed=False))


def add(vm, title="", notes="", calendar=False, all_day=False,
        date_text="", time_text="", duration_text=""):
    return vm.addTask(title, notes, calendar, all_day,
                      date_text, time_text, duration_text)


# ---- пустое название -------------------------------------------------------

def test_empty_title_rejected(vm):
    assert add(vm, title="") is False
    assert vm.errorMessage != ""
    assert vm.repository.all() == []


def test_whitespace_title_rejected(vm):
    assert add(vm, title="   ") is False
    assert vm.errorMessage != ""
    assert vm.repository.all() == []


# ---- задача без даты -------------------------------------------------------

def test_title_only_undated_task_accepted(vm):
    assert add(vm, title="Купить хлеб") is True
    assert vm.errorMessage == ""
    tasks = vm.repository.all()
    assert len(tasks) == 1
    task = tasks[0]
    assert task.title == "Купить хлеб"
    assert task.start is None
    assert task.is_all_day is False
    # задача без даты не претендует на телефонный календарь
    assert task.google_calendar_event_id is None
    assert [row["title"] for row in vm.undatedTasks] == ["Купить хлеб"]


# ---- календарная задача: дата/время обязательны ----------------------------

def test_calendar_task_requires_date(vm):
    assert add(vm, title="Встреча", calendar=True,
               date_text="", time_text="10:00") is False
    assert "дат" in vm.errorMessage.lower()
    assert vm.repository.all() == []


def test_calendar_task_requires_time_unless_all_day(vm):
    assert add(vm, title="Встреча", calendar=True,
               date_text="2026-07-08", time_text="") is False
    assert "врем" in vm.errorMessage.lower()
    assert vm.repository.all() == []


def test_calendar_task_rejects_garbage_date(vm):
    assert add(vm, title="Встреча", calendar=True,
               date_text="завтра", time_text="10:00") is False
    assert vm.repository.all() == []


def test_timed_calendar_task_accepted(vm):
    assert add(vm, title="Встреча", calendar=True,
               date_text="2026-07-08", time_text="10:30",
               duration_text="45") is True
    task = vm.repository.all()[0]
    assert task.start == datetime(2026, 7, 8, 10, 30)
    assert task.duration_minutes == 45
    assert task.end == task.start + timedelta(minutes=45)
    assert task.is_all_day is False


# ---- all-day: достаточно даты, семантика date/date -------------------------

def test_all_day_task_accepts_date_only(vm):
    assert add(vm, title="Отпуск", calendar=True, all_day=True,
               date_text="2026-07-10", time_text="") is True
    task = vm.repository.all()[0]
    assert task.is_all_day is True
    # только дата: время начала — полночь, конец эксклюзивный (+1 день)
    assert task.start == datetime(2026, 7, 10, 0, 0)
    assert task.end == datetime(2026, 7, 11, 0, 0)
    assert task.duration_minutes is None


def test_all_day_task_still_requires_date(vm):
    assert add(vm, title="Отпуск", calendar=True, all_day=True,
               date_text="") is False
    assert vm.repository.all() == []


# ---- длительность ----------------------------------------------------------

def test_non_numeric_duration_rejected(vm):
    assert add(vm, title="Встреча", calendar=True,
               date_text="2026-07-08", time_text="10:00",
               duration_text="abc") is False
    assert "длительн" in vm.errorMessage.lower()
    assert vm.repository.all() == []


def test_zero_and_negative_duration_rejected(vm):
    for bad in ("0", "-30"):
        assert add(vm, title="Встреча", calendar=True,
                   date_text="2026-07-08", time_text="10:00",
                   duration_text=bad) is False
    assert vm.repository.all() == []


def test_invalid_duration_rejected_even_without_calendar(vm):
    assert add(vm, title="Задача", duration_text="xx") is False
    assert vm.repository.all() == []


# ---- валидная задача попадает в фейковый репозиторий -----------------------

def test_valid_task_lands_in_fake_repository(vm):
    assert add(vm, title="A") is True
    assert add(vm, title="B", calendar=True, all_day=True,
               date_text="2026-07-09") is True
    titles = sorted(t.title for t in vm.repository.all())
    assert titles == ["A", "B"]


def test_error_cleared_after_successful_add(vm):
    add(vm, title="")            # ошибка
    assert vm.errorMessage != ""
    add(vm, title="Нормальная")  # успех очищает ошибку
    assert vm.errorMessage == ""


def test_invalid_input_never_raises(vm):
    """Никакой битый ввод не должен выбрасывать исключение (UI не виснет)."""
    bad_inputs = [
        dict(title=""),
        dict(title="X", calendar=True),
        dict(title="X", calendar=True, date_text="9999-99-99", time_text="99:99"),
        dict(title="X", calendar=True, date_text="2026-07-08",
             time_text="10:00", duration_text="1e9later"),
        dict(title="X", duration_text="99999999999999999999"),
    ]
    for kwargs in bad_inputs:
        assert add(vm, **kwargs) is False
        assert vm.errorMessage != ""


# ---- доменная валидация напрямую (без Qt-обёртки) ---------------------------

def test_validate_quick_add_collects_all_errors():
    command = QuickAddCommand(title="", add_to_calendar=True,
                              date_text="", time_text="",
                              duration_text="abc")
    errors = validate_quick_add(command)
    assert len(errors) == 4  # титул, дата, время, длительность


def test_execute_quick_add_defaults_duration():
    result = execute_quick_add(QuickAddCommand(
        title="Встреча", add_to_calendar=True,
        date_text="2026-07-08", time_text="09:00"))
    assert result.ok
    assert result.task.duration_minutes == 60  # длительность по умолчанию
