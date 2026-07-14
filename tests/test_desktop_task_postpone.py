"""Снуз/перенос задач: правильные операции в Calendar-очереди и защита
от дублей быстрых кликов.

Проверяется контракт фазы 1:

- перенос привязанной задачи ставит update;
- перенос недопушенной оставляет один create;
- перенос недатированной (первое планирование) ставит create;
- «Без даты» идёт по проверенному unschedule-потоку (delete события
  или снятие pending create);
- экземпляры повторяющихся серий не переносятся (человекочитаемый отказ);
- удаление привязанной задачи по-прежнему ставит delete;
- busy-защита подавляет повторный быстрый клик.
"""
import json
from datetime import datetime

import pytest

from planner_desktop.domain.task import Task
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.usecases.task_service import (
    DesktopTaskService,
    POSTPONE_RECURRING_ERROR,
)
from planner_desktop.viewmodels.today_viewmodel import TodayViewModel

NOW = datetime(2026, 7, 14, 10, 0)  # вторник


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


def ops(queue):
    return [(op.op, op.task_uid) for op in queue.list_due_ops()]


def make_linked(service, repo, queue, **kwargs):
    """Задача с «допушенным» событием: очередь чиста, event_id есть."""
    defaults = dict(
        title="Встреча",
        start=datetime(2026, 7, 14, 15, 0),
        end=datetime(2026, 7, 14, 16, 0),
        duration_minutes=60,
    )
    defaults.update(kwargs)
    task = service.create_task(Task(**defaults))
    for op in queue.list_due_ops():
        queue.remove_op(op.id)
    task.google_calendar_event_id = "evt-1"
    repo.update(task)
    return repo.get_by_uid(task.uid)


# ---- сервис: постановка операций -----------------------------------------------

def test_postpone_linked_task_enqueues_update(service, repo, queue):
    task = make_linked(service, repo, queue)
    result = service.postpone_task(task.uid, "tomorrow", now=NOW)
    assert result.ok
    assert result.task.start == datetime(2026, 7, 15, 15, 0)
    assert ops(queue) == [("update", task.uid)]


def test_postpone_unpushed_task_keeps_single_create(service, queue):
    task = service.create_task(Task(
        title="Черновик", start=datetime(2026, 7, 14, 9, 0),
        end=datetime(2026, 7, 14, 10, 0), duration_minutes=60))
    service.postpone_task(task.uid, "next_week", now=NOW)
    assert ops(queue) == [("create", task.uid)]


def test_postpone_undated_task_enqueues_create(service, repo, queue):
    task = service.create_task(Task(title="Без даты"))
    assert ops(queue) == []
    result = service.postpone_task(task.uid, "tomorrow", now=NOW)
    assert result.ok
    assert result.task.is_all_day is True
    assert result.task.start == datetime(2026, 7, 15)
    assert ops(queue) == [("create", task.uid)]


def test_postpone_later_today_uses_now(service, repo, queue):
    task = make_linked(service, repo, queue)
    result = service.postpone_task(task.uid, "later_today", now=NOW)
    assert result.ok
    assert result.task.start == datetime(2026, 7, 14, 12, 0)
    assert result.task.is_all_day is False


def test_postpone_unschedule_linked_enqueues_delete_and_unlinks(
        service, repo, queue):
    task = make_linked(service, repo, queue)
    result = service.postpone_task(task.uid, "unschedule", now=NOW)
    assert result.ok
    stored = repo.get_by_uid(task.uid)
    assert stored.start is None
    assert stored.google_calendar_event_id is None
    due = queue.list_due_ops()
    assert [(op.op, op.task_uid) for op in due] == [("delete", task.uid)]
    assert json.loads(due[0].payload_json) == {"event_id": "evt-1"}


def test_postpone_unschedule_unpushed_cancels_create(service, repo, queue):
    task = service.create_task(Task(
        title="Не допушена", start=datetime(2026, 7, 14, 9, 0),
        end=datetime(2026, 7, 14, 10, 0), duration_minutes=60))
    assert ops(queue) == [("create", task.uid)]
    result = service.postpone_task(task.uid, "unschedule", now=NOW)
    assert result.ok
    assert ops(queue) == []
    assert repo.get_by_uid(task.uid).start is None


def test_postpone_recurring_instance_is_refused(service, repo, queue):
    task = make_linked(service, repo, queue)
    task.google_calendar_recurring_event_id = "series-1"
    repo.update(task)
    before = repo.get_by_uid(task.uid).start

    result = service.postpone_task(task.uid, "tomorrow", now=NOW)
    assert not result.ok
    assert result.errors == [POSTPONE_RECURRING_ERROR]
    assert repo.get_by_uid(task.uid).start == before
    assert ops(queue) == []  # ничего не ставится


def test_postpone_unknown_action_is_refused(service, repo, queue):
    task = make_linked(service, repo, queue)
    result = service.postpone_task(task.uid, "nonsense", now=NOW)
    assert not result.ok
    assert ops(queue) == []


def test_delete_linked_task_still_enqueues_delete(service, repo, queue):
    task = make_linked(service, repo, queue)
    assert service.delete_task_by_uid(task.uid) is True
    assert ops(queue) == [("delete", task.uid)]


def test_restore_task_returns_completed_to_work(service, repo):
    task = service.create_task(Task(title="Сделано"))
    service.toggle_completed(task.uid)
    assert repo.get_by_uid(task.uid).completed is True
    assert service.restore_task(task.uid) is True
    assert repo.get_by_uid(task.uid).completed is False
    assert service.restore_task(task.uid) is False  # уже в работе


# ---- persistable quick scheduling presets -------------------------------------

def test_task_preset_linked_task_enqueues_update(service, repo, queue):
    task = make_linked(service, repo, queue)
    result = service.apply_scheduling_preset(task.uid, "tomorrow", now=NOW)
    assert result.ok
    assert result.task.start == datetime(2026, 7, 15, 15, 0)
    assert ops(queue) == [("update", task.uid)]


def test_task_preset_undated_task_enqueues_create(service, repo, queue):
    task = service.create_task(Task(title="Без даты"))
    result = service.apply_scheduling_preset(task.uid, "evening", now=NOW)
    assert result.ok
    assert result.task.start == datetime(2026, 7, 14, 19, 0)
    assert result.task.duration_minutes == 60
    assert ops(queue) == [("create", task.uid)]


def test_task_preset_unschedule_linked_enqueues_delete(service, repo, queue):
    task = make_linked(service, repo, queue)
    result = service.apply_scheduling_preset(task.uid, "unschedule", now=NOW)
    assert result.ok
    assert repo.get_by_uid(task.uid).start is None
    assert ops(queue) == [("delete", task.uid)]


def test_task_presets_expose_enabled_state(service, repo, queue):
    timed = make_linked(service, repo, queue)
    undated = service.create_task(Task(title="Без даты"))
    recurring = make_linked(service, repo, queue, title="Серия")
    recurring.google_calendar_recurring_event_id = "series-1"
    repo.update(recurring)
    vm = make_vm(service)

    timed_actions = {item["id"]: item["enabled"]
                     for item in vm.taskPresetsFor(timed.uid)}
    assert timed_actions["plus_hour"] is True
    assert timed_actions["unschedule"] is True

    undated_actions = {item["id"]: item["enabled"]
                       for item in vm.taskPresetsFor(undated.uid)}
    assert undated_actions["plus_hour"] is False
    assert undated_actions["unschedule"] is False
    assert undated_actions["today"] is True

    assert all(not item["enabled"] for item in vm.taskPresetsFor(recurring.uid))


def test_task_preset_recurring_is_refused_in_service_and_vm(
        service, repo, queue):
    task = make_linked(service, repo, queue)
    task.google_calendar_recurring_event_id = "series-1"
    repo.update(task)
    before = task.start

    result = service.apply_scheduling_preset(task.uid, "tomorrow", now=NOW)
    assert not result.ok
    assert repo.get_by_uid(task.uid).start == before

    vm = make_vm(service)
    errors = []
    vm.toastError.connect(errors.append)
    assert vm.applyTaskPreset(task.uid, "tomorrow") is False
    assert errors
    assert repo.get_by_uid(task.uid).start == before
    assert ops(queue) == []


def test_vm_task_preset_emits_one_toast_and_dedupes(service, repo, queue):
    task = make_linked(service, repo, queue)
    clock = [0.0]
    vm = make_vm(service, clock=lambda: clock[0])
    toasts = []
    vm.toastMessage.connect(toasts.append)

    assert vm.applyTaskPreset(task.uid, "tomorrow") is True
    clock[0] += 0.05
    assert vm.applyTaskPreset(task.uid, "tomorrow") is False
    assert toasts == ["Расписание обновлено"]
    assert ops(queue) == [("update", task.uid)]


# ---- ViewModel: тосты, снуз-меню, busy-защита -------------------------------------

def make_vm(service, clock=None):
    return TodayViewModel(service=service, now_provider=lambda: NOW,
                          clock=clock)


def test_vm_postpone_emits_toast_and_mutation(service, repo, queue):
    task = make_linked(service, repo, queue)
    vm = make_vm(service)
    toasts, mutated = [], []
    vm.toastMessage.connect(toasts.append)
    vm.tasksMutated.connect(lambda: mutated.append(1))
    assert vm.postponeTask(task.uid, "tomorrow") is True
    assert toasts == ["Задача перенесена"]
    assert mutated


def test_vm_postpone_recurring_reports_error_toast(service, repo, queue):
    task = make_linked(service, repo, queue)
    task.google_calendar_recurring_event_id = "series-1"
    repo.update(task)
    vm = make_vm(service)
    errors = []
    vm.toastError.connect(errors.append)
    assert vm.postponeTask(task.uid, "tomorrow") is False
    assert errors == [POSTPONE_RECURRING_ERROR]


def test_vm_pick_action_never_reaches_service(service, repo, queue):
    task = make_linked(service, repo, queue)
    vm = make_vm(service)
    assert vm.postponeTask(task.uid, "pick") is False
    assert ops(queue) == []


def test_snooze_actions_for_task_states(service, repo, queue):
    vm = make_vm(service)
    linked = make_linked(service, repo, queue)
    undated = service.create_task(Task(title="Без даты"))
    recurring = make_linked(service, repo, queue, title="Серия")
    recurring.google_calendar_recurring_event_id = "series-1"
    repo.update(recurring)

    by_id = {a["id"]: a["enabled"] for a in vm.snoozeActionsFor(linked.uid)}
    assert all(by_id.values())  # обычной задаче можно всё

    by_id = {a["id"]: a["enabled"] for a in vm.snoozeActionsFor(undated.uid)}
    assert by_id["unschedule"] is False  # снимать нечего
    assert by_id["tomorrow"] is True

    by_id = {a["id"]: a["enabled"] for a in vm.snoozeActionsFor(recurring.uid)}
    assert not any(by_id.values())

    assert vm.snoozeActionsFor("no-such") == []


def test_busy_guard_blocks_duplicate_rapid_postpone(service, repo, queue):
    task = make_linked(service, repo, queue)
    fake_time = [0.0]
    vm = make_vm(service, clock=lambda: fake_time[0])

    assert vm.postponeTask(task.uid, "tomorrow") is True
    fake_time[0] += 0.1  # быстрый повторный клик
    assert vm.postponeTask(task.uid, "tomorrow") is False
    assert ops(queue) == [("update", task.uid)]  # операция одна

    fake_time[0] += 1.0  # осознанный повтор спустя время — работает
    assert vm.postponeTask(task.uid, "tomorrow") is True


def test_busy_guard_blocks_duplicate_rapid_delete(service, repo, queue):
    task = make_linked(service, repo, queue)
    fake_time = [0.0]
    vm = make_vm(service, clock=lambda: fake_time[0])
    deleted, toasts = [], []
    vm.toastMessage.connect(toasts.append)

    assert vm.deleteTask(task.uid) is True
    fake_time[0] += 0.05
    assert vm.deleteTask(task.uid) is False
    assert toasts == ["Задача удалена"]  # один тост, одно удаление


def test_busy_flag_blocks_reentrant_operation(service, repo, queue):
    """Пока операция выполняется (busy=True), вторая не начинается."""
    task = make_linked(service, repo, queue)
    vm = make_vm(service)
    results = []

    original = service.postpone_task

    def reentrant(uid, action, now=None):
        # имитация «клика во время операции»
        results.append(vm.postponeTask(task.uid, "next_week"))
        return original(uid, action, now=now)

    service.postpone_task = reentrant
    try:
        assert vm.postponeTask(task.uid, "tomorrow") is True
    finally:
        service.postpone_task = original
    assert results == [False]
    assert ops(queue) == [("update", task.uid)]
