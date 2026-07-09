"""Тесты действий TodayViewModel: статистика шапки, редактор, удаление,
галочка, сигналы. QObject работает без QApplication — окно не открывается.
"""
from datetime import date

import pytest

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
def vm(repo, queue):
    return TodayViewModel(service=DesktopTaskService(repo, calendar_queue=queue))


def today_text() -> str:
    return date.today().strftime("%Y-%m-%d")


def save_new(vm, title="Задача", scheduled=False, all_day=False,
             date_text="", time_text="", duration_text="",
             priority=0, notes="", completed=False):
    return vm.saveEditor("", title, notes, priority, scheduled, all_day,
                         date_text, time_text, duration_text, completed)


# ---- статистика шапки -----------------------------------------------------------

def test_stats_counts(vm):
    assert save_new(vm, "Сегодняшняя", scheduled=True,
                    date_text=today_text(), time_text="10:00") is True
    assert save_new(vm, "Без даты") is True
    assert save_new(vm, "Ещё без даты") is True

    assert vm.todayCount == 1
    assert vm.undatedCount == 2
    assert vm.completedTodayCount == 0
    # одна календарная задача -> одна pending-операция create
    assert vm.pendingSyncCount == 1
    assert vm.hasSyncQueue is True


def test_completed_stat_updates_on_toggle(vm):
    save_new(vm, "Сегодняшняя", scheduled=True,
             date_text=today_text(), time_text="10:00")
    uid = vm.todayTasks[0]["uid"]
    assert vm.toggleCompleted(uid) is True
    assert vm.completedTodayCount == 1
    assert vm.toggleCompleted(uid) is True
    assert vm.completedTodayCount == 0


def test_header_date_text_is_russian(vm):
    text = vm.headerDateText
    assert str(date.today().year) in text
    assert "," in text


# ---- строки списков --------------------------------------------------------------

def test_rows_expose_sync_badge_fields(vm):
    save_new(vm, "Календарная", scheduled=True,
             date_text=today_text(), time_text="09:00", priority=2)
    save_new(vm, "Локальная")

    today_row = vm.todayTasks[0]
    assert today_row["hasPendingSync"] is True   # create ещё не допушен
    assert today_row["isLinked"] is False
    assert today_row["priority"] == 2
    assert today_row["priorityLabel"] == "Средний"

    undated_row = vm.undatedTasks[0]
    assert undated_row["hasPendingSync"] is False


# ---- редактор: создание ------------------------------------------------------------

def test_save_editor_creates_undated_task(vm, repo):
    assert save_new(vm, "Новая", notes="текст", priority=1) is True
    assert vm.editorError == ""
    task = repo.list_all()[0]
    assert task.title == "Новая"
    assert task.priority == 1
    assert task.start is None


def test_save_editor_creates_scheduled_task_and_enqueues(vm, repo, queue):
    assert save_new(vm, "Встреча", scheduled=True,
                    date_text="2026-07-08", time_text="10:30",
                    duration_text="45") is True
    task = repo.list_all()[0]
    assert [(op.op, op.task_uid) for op in queue.list_due_ops()] == \
        [("create", task.uid)]


def test_save_editor_invalid_input_sets_editor_error(vm, repo):
    assert save_new(vm, "") is False
    assert vm.editorError != ""
    assert repo.list_all() == []

    # успешное сохранение очищает ошибку
    assert save_new(vm, "Нормальная") is True
    assert vm.editorError == ""


def test_save_editor_never_raises_on_garbage(vm):
    assert vm.saveEditor("", "X", "", 0, True, False,
                         "9999-99-99", "99:99", "мусор", False) is False
    assert vm.editorError != ""


# ---- редактор: правка ---------------------------------------------------------------

def test_editor_data_for_roundtrip(vm):
    save_new(vm, "Встреча", scheduled=True, date_text="2026-07-08",
             time_text="10:30", duration_text="45", priority=3)
    uid = [r["uid"] for r in vm.todayTasks + vm.undatedTasks]
    # задача не на сегодня — достанем через репозиторий
    task = vm.repository.list_all()[0]
    data = vm.editorDataFor(task.uid)
    assert data["exists"] is True
    assert data["title"] == "Встреча"
    assert data["scheduled"] is True
    assert data["dateText"] == "2026-07-08"
    assert data["timeText"] == "10:30"
    assert data["durationText"] == "45"
    assert data["priority"] == 3
    assert data["isRecurringInstance"] is False


def test_editor_data_for_missing_task(vm):
    data = vm.editorDataFor("нет-такого")
    assert data["exists"] is False


def test_save_editor_edits_existing_task(vm, repo):
    save_new(vm, "Старое имя")
    task = repo.list_all()[0]

    assert vm.saveEditor(task.uid, "Новое имя", "заметка", 2, False, False,
                         "", "", "", True) is True
    saved = repo.get_by_uid(task.uid)
    assert saved.title == "Новое имя"
    assert saved.priority == 2
    assert saved.completed is True


def test_save_editor_moves_undated_to_scheduled(vm, repo, queue):
    save_new(vm, "Черновик")
    task = repo.list_all()[0]

    assert vm.saveEditor(task.uid, "Черновик", "", 0, True, False,
                         "2026-07-09", "09:00", "", False) is True
    saved = repo.get_by_uid(task.uid)
    assert saved.start is not None
    assert [(op.op, op.task_uid) for op in queue.list_due_ops()] == \
        [("create", task.uid)]


# ---- удаление -------------------------------------------------------------------------

def test_delete_task_tombstones(vm, repo):
    save_new(vm, "Удаляемая")
    task = repo.list_all()[0]
    assert vm.deleteTask(task.uid) is True
    assert repo.list_all() == []
    assert repo.get(task.id).is_deleted is True


def test_delete_unknown_task_returns_false(vm):
    assert vm.deleteTask("нет-такого") is False


# ---- сигналы ----------------------------------------------------------------------------

def test_mutations_emit_tasks_mutated(vm):
    mutations = []
    vm.tasksMutated.connect(lambda: mutations.append(1))
    save_new(vm, "Задача")
    uid = vm.repository.list_all()[0].uid
    vm.toggleCompleted(uid)
    vm.deleteTask(uid)
    assert len(mutations) == 3


def test_toast_messages_emitted(vm):
    toasts = []
    vm.toastMessage.connect(toasts.append)
    save_new(vm, "Задача")
    uid = vm.repository.list_all()[0].uid
    vm.deleteTask(uid)
    assert toasts == ["Сохранено", "Задача удалена"]


def test_refresh_emits_tasks_changed_without_mutation(vm):
    changed, mutated = [], []
    vm.tasksChanged.connect(lambda: changed.append(1))
    vm.tasksMutated.connect(lambda: mutated.append(1))
    vm.refresh()
    assert changed == [1]
    assert mutated == []
