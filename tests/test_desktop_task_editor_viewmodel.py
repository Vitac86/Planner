"""Редактор задач через ViewModel: создание/правка всех режимов, переходы
расписания, невалидный ввод, пресеты формы, сигналы обновления и
персистентность после переоткрытия репозитория.

ViewModel-и — QObject без окна: тесты идут без QApplication.
"""
from datetime import datetime

import pytest

from planner_desktop.domain.task import Task
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.calendar_viewmodel import CalendarViewModel
from planner_desktop.viewmodels.history_viewmodel import HistoryViewModel
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel

NOW = datetime(2026, 7, 14, 10, 5)  # вторник


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
    store = CalendarSyncStore(db_path)
    yield store
    store.close()


@pytest.fixture()
def service(repo, queue):
    return DesktopTaskService(repo, calendar_queue=queue)


@pytest.fixture()
def vm(service):
    return TodayViewModel(service=service, now_provider=lambda: NOW)


def ops(queue):
    return [(op.op, op.task_uid) for op in queue.list_due_ops()]


def save(vm, uid="", title="Задача", notes="", priority=0, scheduled=False,
         all_day=False, date_text="", time_text="", duration_text="",
         completed=False):
    return vm.saveEditor(uid, title, notes, priority, scheduled, all_day,
                         date_text, time_text, duration_text, completed)


# ---- создание в каждом режиме ---------------------------------------------------

def test_create_undated(vm, repo, queue):
    assert save(vm, title="Идея") is True
    task = repo.list_undated()[0]
    assert task.start is None and not task.is_all_day
    assert ops(queue) == []


def test_create_all_day(vm, repo, queue):
    assert save(vm, title="Отпуск", scheduled=True, all_day=True,
                date_text="2026-07-20") is True
    task = repo.get_by_uid(ops(queue)[0][1])
    assert task.is_all_day is True
    assert task.start == datetime(2026, 7, 20)
    assert ops(queue)[0][0] == "create"


def test_create_timed_with_duration(vm, repo, queue):
    assert save(vm, title="Встреча", scheduled=True,
                date_text="2026-07-14", time_text="15:00",
                duration_text="90") is True
    uid = ops(queue)[0][1]
    task = repo.get_by_uid(uid)
    assert task.start == datetime(2026, 7, 14, 15, 0)
    assert task.duration_minutes == 90
    assert task.end == datetime(2026, 7, 14, 16, 30)


# ---- правка всех полей --------------------------------------------------------------

def test_edit_all_fields(vm, repo):
    save(vm, title="Черновик")
    uid = repo.list_undated()[0].uid
    assert save(vm, uid=uid, title="Готово", notes="описание", priority=2,
                completed=True) is True
    task = repo.get_by_uid(uid)
    assert (task.title, task.notes, task.priority, task.completed) == \
        ("Готово", "описание", 2, True)
    assert task.completed_at is not None


# ---- переходы расписания --------------------------------------------------------------

def test_undated_becomes_scheduled_enqueues_create(vm, repo, queue):
    save(vm, title="Без даты")
    uid = repo.list_undated()[0].uid
    assert save(vm, uid=uid, title="Без даты", scheduled=True,
                date_text="2026-07-15", time_text="10:00") is True
    assert ops(queue) == [("create", uid)]
    assert repo.get_by_uid(uid).start == datetime(2026, 7, 15, 10, 0)


def test_scheduled_becomes_undated_via_editor(vm, repo, queue, service):
    save(vm, title="Встреча", scheduled=True,
         date_text="2026-07-15", time_text="10:00")
    uid = ops(queue)[0][1]
    # событие «как будто допушено» и привязано
    queue.remove_op(queue.list_due_ops()[0].id)
    task = repo.get_by_uid(uid)
    task.google_calendar_event_id = "evt-9"
    repo.update(task)

    assert save(vm, uid=uid, title="Встреча", scheduled=False) is True
    task = repo.get_by_uid(uid)
    assert task.start is None and task.google_calendar_event_id is None
    assert ops(queue) == [("delete", uid)]


def test_all_day_becomes_timed(vm, repo, queue):
    save(vm, title="День", scheduled=True, all_day=True,
         date_text="2026-07-20")
    uid = ops(queue)[0][1]
    assert save(vm, uid=uid, title="День", scheduled=True, all_day=False,
                date_text="2026-07-20", time_text="09:00",
                duration_text="30") is True
    task = repo.get_by_uid(uid)
    assert task.is_all_day is False
    assert task.start == datetime(2026, 7, 20, 9, 0)
    assert task.duration_minutes == 30


def test_timed_becomes_all_day(vm, repo, queue):
    save(vm, title="Встреча", scheduled=True,
         date_text="2026-07-14", time_text="15:00")
    uid = ops(queue)[0][1]
    assert save(vm, uid=uid, title="Встреча", scheduled=True, all_day=True,
                date_text="2026-07-14") is True
    task = repo.get_by_uid(uid)
    assert task.is_all_day is True
    assert task.start == datetime(2026, 7, 14)
    assert task.duration_minutes is None
    assert task.end == datetime(2026, 7, 15)  # эксклюзивный конец


# ---- невалидный ввод ------------------------------------------------------------------

def test_invalid_empty_title_keeps_dialog_data(vm, repo):
    assert save(vm, title="   ") is False
    assert vm.editorError != ""
    assert repo.list_all() == []


def test_invalid_time_reports_error(vm, repo):
    assert save(vm, title="Встреча", scheduled=True,
                date_text="2026-07-14", time_text="25:99") is False
    assert "ЧЧ:ММ" in vm.editorError
    assert repo.list_all() == []


def test_editing_missing_task_reports_error(vm):
    assert save(vm, uid="no-such-uid", title="Призрак") is False
    assert vm.editorError != ""


# ---- пресеты формы через ViewModel -------------------------------------------------------

def test_apply_editor_preset_today_from_undated(vm):
    result = vm.applyEditorPreset("today", "none", "", "")
    assert result["ok"] is True
    assert result["mode"] == "allday"
    assert result["dateText"] == "2026-07-14"


def test_apply_editor_preset_evening(vm):
    result = vm.applyEditorPreset("evening", "allday", "2026-07-20", "")
    assert result == {"ok": True, "mode": "timed", "dateText": "2026-07-20",
                      "timeText": "19:00", "error": ""}


def test_apply_editor_preset_plus_hour_refused_without_time(vm):
    result = vm.applyEditorPreset("plus_hour", "none", "", "")
    assert result["ok"] is False
    assert result["error"]


def test_new_scheduled_defaults_slot(vm):
    result = vm.newScheduledDefaults()
    assert result["ok"] is True
    assert result["mode"] == "timed"
    assert result["dateText"] == "2026-07-14"
    assert result["timeText"] == "11:00"


def test_editor_payload_carries_mode(vm, repo, queue):
    save(vm, title="Встреча", scheduled=True,
         date_text="2026-07-14", time_text="15:00")
    uid = ops(queue)[0][1]
    assert vm.editorDataFor(uid)["mode"] == "timed"
    save(vm, title="Идея")
    undated_uid = repo.list_undated()[0].uid
    assert vm.editorDataFor(undated_uid)["mode"] == "none"


# ---- сигналы и обновление моделей ----------------------------------------------------------

def test_save_emits_refresh_signals(vm):
    changed, mutated, toasts = [], [], []
    vm.tasksChanged.connect(lambda: changed.append(1))
    vm.tasksMutated.connect(lambda: mutated.append(1))
    vm.toastMessage.connect(toasts.append)
    assert save(vm, title="Сигналы") is True
    assert changed and mutated
    assert toasts == ["Сохранено"]


def test_stats_refresh_after_create_edit_delete_restore(service, repo):
    vm = TodayViewModel(service=service, now_provider=lambda: NOW)
    today_text = datetime.now().strftime("%Y-%m-%d")
    assert save(vm, title="Дело дня", scheduled=True, all_day=True,
                date_text=today_text) is True
    assert vm.todayCount == 1 and vm.completedTodayCount == 0

    uid = repo.list_today()[0].uid
    assert save(vm, uid=uid, title="Дело дня", scheduled=True, all_day=True,
                date_text=today_text, completed=True) is True
    assert vm.completedTodayCount == 1

    assert vm.restoreTask(uid) is True
    assert vm.completedTodayCount == 0

    assert vm.deleteTask(uid) is True
    assert vm.todayCount == 0


def test_history_and_calendar_share_editor_contract(service, repo):
    """Общий диалог: слоты редактора одинаковы у всех трёх ViewModel."""
    from planner_desktop.usecases.daily_task_service import DailyTaskService
    from planner_desktop.repositories.daily_task_repository import (
        InMemoryDailyTaskRepository,
    )
    daily = DailyTaskService(InMemoryDailyTaskRepository())
    cal_vm = CalendarViewModel(service=service, daily_service=daily)
    hist_vm = HistoryViewModel(service, daily)

    assert cal_vm.saveEditor("", "Из календаря", "", 0, False, False,
                             "", "", "", False) is True
    uid = repo.list_undated()[0].uid
    assert hist_vm.editorDataFor(uid)["title"] == "Из календаря"
    assert hist_vm.saveEditor(uid, "Правка из истории", "", 1, False, False,
                              "", "", "", False) is True
    assert repo.get_by_uid(uid).title == "Правка из истории"


# ---- персистентность после переоткрытия ------------------------------------------------------

def test_saved_task_survives_repository_reopen(db_path):
    repo = SQLiteTaskRepository(db_path)
    queue = CalendarSyncStore(db_path)
    vm = TodayViewModel(service=DesktopTaskService(repo, calendar_queue=queue))
    assert save(vm, title="Переживу рестарт", scheduled=True,
                date_text="2026-07-21", time_text="08:30",
                duration_text="45") is True
    uid = repo.list_all()[0].uid
    queue.close()
    repo.close()

    reopened = SQLiteTaskRepository(db_path)
    try:
        task = reopened.get_by_uid(uid)
        assert task is not None
        assert task.title == "Переживу рестарт"
        assert task.start == datetime(2026, 7, 21, 8, 30)
        assert task.duration_minutes == 45
    finally:
        reopened.close()
