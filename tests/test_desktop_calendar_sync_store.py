"""Тесты локальной очереди Calendar-операций (planner_desktop).

Временная БД (tmp_path), без сети и без старого Planner/app.db.
Часы подменяются, чтобы детерминированно проверять бэкофф.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from planner_desktop.storage import calendar_sync_store as store_module
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 7, 7, 12, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += timedelta(seconds=seconds)


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "app_desktop.db"


@pytest.fixture()
def clock():
    return FakeClock()


@pytest.fixture()
def store(db_path, clock):
    sync_store = CalendarSyncStore(db_path, clock=clock)
    yield sync_store
    sync_store.close()


# ---- схема -----------------------------------------------------------------

def test_store_creates_tables(store, db_path):
    with sqlite3.connect(str(db_path)) as connection:
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert "desktop_pending_calendar_ops" in tables
    assert "desktop_sync_state" in tables


def test_store_coexists_with_repository_on_same_db(db_path, clock):
    """create_schema идемпотентен: репозиторий и очередь делят один файл."""
    repository = SQLiteTaskRepository(db_path)
    sync_store = CalendarSyncStore(db_path, clock=clock)
    sync_store.enqueue_create("uid-1")
    assert len(sync_store.list_due_ops()) == 1
    sync_store.close()
    repository.close()
    # повторное открытие не ломает уже созданную схему
    reopened = CalendarSyncStore(db_path, clock=clock)
    assert len(reopened.list_due_ops()) == 1
    reopened.close()


def test_store_touches_only_its_own_db_file(tmp_path, clock):
    db_path = tmp_path / "isolated" / "app_desktop.db"
    sync_store = CalendarSyncStore(db_path, clock=clock)
    sync_store.enqueue_create("uid-1")
    sync_store.close()
    created = sorted(p.name for p in db_path.parent.iterdir())
    assert created == ["app_desktop.db"]


# ---- постановка операций -----------------------------------------------------

def test_enqueue_create_and_list_due(store, clock):
    store.enqueue_create("uid-1")
    ops = store.list_due_ops()
    assert len(ops) == 1
    op = ops[0]
    assert op.op == "create"
    assert op.task_uid == "uid-1"
    assert op.attempts == 0
    assert op.last_error is None
    assert op.status == "pending"
    assert op.created_at == clock.now
    assert op.next_try_at == clock.now


def test_duplicate_pending_op_not_enqueued(store):
    store.enqueue_create("uid-1")
    store.enqueue_create("uid-1")
    store.enqueue_update("uid-2")
    store.enqueue_update("uid-2")
    assert len(store.list_due_ops()) == 2


def test_update_not_enqueued_while_create_pending(store):
    """push читает актуальное состояние задачи — create уже допушит правку."""
    store.enqueue_create("uid-1")
    store.enqueue_update("uid-1")
    ops = store.list_due_ops()
    assert [op.op for op in ops] == ["create"]


def test_delete_supersedes_pending_create_and_update(store):
    store.enqueue_create("uid-1")
    store.enqueue_delete("uid-1", payload={"event_id": "evt-9"})
    ops = store.list_due_ops()
    assert [op.op for op in ops] == ["delete"]
    assert "evt-9" in ops[0].payload_json


def test_cancel_pending_ops(store):
    store.enqueue_create("uid-1")
    store.enqueue_update("uid-2")
    store.cancel_pending_ops("uid-1")
    assert [op.task_uid for op in store.list_due_ops()] == ["uid-2"]


def test_has_pending_op(store):
    assert store.has_pending_op("uid-1") is False
    store.enqueue_update("uid-1")
    assert store.has_pending_op("uid-1") is True


# ---- завершение и ретраи ------------------------------------------------------

def test_remove_op_after_successful_push(store):
    store.enqueue_create("uid-1")
    op = store.list_due_ops()[0]
    store.remove_op(op.id)
    assert store.list_due_ops() == []
    assert store.has_pending_op("uid-1") is False


def test_requeue_sets_backoff_and_error(store, clock):
    store.enqueue_create("uid-1")
    op = store.list_due_ops()[0]

    store.requeue_op(op.id, "временная ошибка")
    # до наступления next_try_at операция не «должна» выполняться
    assert store.list_due_ops() == []
    assert store.has_pending_op("uid-1") is True  # но задача всё ещё грязная

    clock.advance(store_module.RETRY_BASE_DELAY_SECONDS + 1)
    retried = store.list_due_ops()
    assert len(retried) == 1
    assert retried[0].attempts == 1
    assert retried[0].last_error == "временная ошибка"


def test_backoff_grows_with_attempts(store, clock):
    store.enqueue_create("uid-1")
    op = store.list_due_ops()[0]
    store.requeue_op(op.id, "ошибка 1")
    clock.advance(store_module.RETRY_BASE_DELAY_SECONDS + 1)
    store.requeue_op(op.id, "ошибка 2")
    # после второй попытки задержка удваивается: базовой уже мало
    clock.advance(store_module.RETRY_BASE_DELAY_SECONDS + 1)
    assert store.list_due_ops() == []
    clock.advance(store_module.RETRY_BASE_DELAY_SECONDS)
    assert len(store.list_due_ops()) == 1


def test_requeue_becomes_terminal_after_max_attempts(store, clock):
    """Бесконечных ретраев нет: после MAX_ATTEMPTS операция — dead-letter."""
    store.enqueue_create("uid-1")
    op = store.list_due_ops()[0]
    for attempt in range(store_module.MAX_ATTEMPTS):
        store.requeue_op(op.id, f"ошибка {attempt}")
        clock.advance(store_module.RETRY_MAX_DELAY_SECONDS + 1)

    assert store.list_due_ops() == []
    terminal = store.list_terminal_ops()
    assert len(terminal) == 1
    assert terminal[0].status == "terminal"
    assert terminal[0].attempts == store_module.MAX_ATTEMPTS
    # terminal-операция не «размораживается» временем
    clock.advance(10 * store_module.RETRY_MAX_DELAY_SECONDS)
    assert store.list_due_ops() == []


def test_mark_terminal_directly(store, clock):
    store.enqueue_update("uid-1")
    op = store.list_due_ops()[0]
    store.mark_terminal(op.id, "постоянная ошибка (400)")
    assert store.list_due_ops() == []
    assert store.has_pending_op("uid-1") is False
    terminal = store.list_terminal_ops()
    assert terminal[0].last_error == "постоянная ошибка (400)"


# ---- состояние синка ------------------------------------------------------------

def test_sync_cursor_roundtrip(store):
    assert store.get_sync_cursor() is None
    store.set_sync_cursor("42")
    assert store.get_sync_cursor() == "42"
    store.set_sync_cursor("43")
    assert store.get_sync_cursor() == "43"


def test_generic_state_roundtrip(store):
    assert store.get_state("нет-такого") is None
    store.set_state("ключ", "значение")
    assert store.get_state("ключ") == "значение"


def test_queue_persists_across_reopen(db_path, clock):
    first = CalendarSyncStore(db_path, clock=clock)
    first.enqueue_create("uid-1")
    first.set_sync_cursor("7")
    first.close()

    second = CalendarSyncStore(db_path, clock=clock)
    try:
        assert [op.task_uid for op in second.list_due_ops()] == ["uid-1"]
        assert second.get_sync_cursor() == "7"
    finally:
        second.close()


# ---- изоляция от старого приложения ----------------------------------------------

def test_default_store_path_is_isolated_desktop_db(tmp_path, monkeypatch, clock):
    """Очередь по умолчанию живёт в PlannerDesktop/app_desktop.db,
    а не в старом Planner/app.db."""
    monkeypatch.setenv("PLANNER_DESKTOP_DATA_DIR", str(tmp_path / "desktop_data"))
    sync_store = CalendarSyncStore(clock=clock)
    try:
        assert sync_store.db_path.name == "app_desktop.db"
        assert sync_store.db_path.parent == tmp_path / "desktop_data"
    finally:
        sync_store.close()
