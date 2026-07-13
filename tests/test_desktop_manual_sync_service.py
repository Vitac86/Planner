"""Тесты ManualSyncService: один цикл push+pull, структурный результат,
запрет одновременных запусков, честные ошибки. Всё на FakeCalendarGateway
и изолированной SQLite в tmp_path — ни сети, ни OAuth, ни Google.
"""
from datetime import datetime, timedelta

import pytest

from planner_desktop.domain.task import Task
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.sync.sync_types import (
    RetryableGatewayError,
    TerminalGatewayError,
)
from planner_desktop.usecases.manual_sync_service import (
    LAST_SYNC_AT_KEY,
    LAST_SYNC_ERROR_KEY,
    LAST_SYNC_SUMMARY_KEY,
    SYNC_ALREADY_RUNNING_ERROR,
    ManualSyncService,
)
from planner_desktop.usecases.task_service import DesktopTaskService


@pytest.fixture()
def db_path(tmp_path):
    return tmp_path / "app_desktop.db"


@pytest.fixture()
def repo(db_path):
    repository = SQLiteTaskRepository(db_path)
    yield repository
    repository.close()


@pytest.fixture()
def store(db_path):
    sync_store = CalendarSyncStore(db_path)
    yield sync_store
    sync_store.close()


@pytest.fixture()
def task_service(repo, store):
    return DesktopTaskService(repo, calendar_queue=store)


@pytest.fixture()
def gateway():
    return FakeCalendarGateway()


@pytest.fixture()
def sync(repo, store, gateway):
    return ManualSyncService(repo, store, gateway_provider=lambda: gateway)


def add_scheduled(task_service, title="Встреча", hour=10):
    start = datetime(2026, 7, 14, hour, 0)
    return task_service.create_task(Task(
        title=title, start=start, end=start + timedelta(hours=1),
        duration_minutes=60,
    ))


# ---- успешный цикл --------------------------------------------------------------------

def test_run_once_pushes_and_pulls(sync, task_service, store, repo, gateway):
    task = add_scheduled(task_service)
    assert store.count_pending_ops() == 1

    result = sync.run_once()

    assert result.ok is True
    assert result.pushed == 1
    assert result.pulled == 1  # эхо собственного создания вернулось pull-ом
    assert result.pending_before == 1
    assert result.pending_after == 0
    assert result.terminal_ops == 0
    assert result.cursor_updated is True
    assert result.error == ""
    assert result.started_at is not None and result.finished_at is not None
    # событие реально «в календаре», задача привязана
    assert repo.get_by_uid(task.uid).google_calendar_event_id is not None
    assert len(gateway.events) == 1


def test_summary_is_human_readable(sync, task_service):
    add_scheduled(task_service)
    result = sync.run_once()
    assert result.summary.startswith("Синхронизировано")
    assert "отправлено 1" in result.summary


def test_last_sync_state_is_persisted(sync, task_service, store):
    add_scheduled(task_service)
    result = sync.run_once()
    assert result.ok is True
    assert store.get_state(LAST_SYNC_AT_KEY) is not None
    assert store.get_state(LAST_SYNC_SUMMARY_KEY) == result.summary
    assert store.get_state(LAST_SYNC_ERROR_KEY) is None


def test_pull_creates_task_from_phone_event(sync, repo, gateway):
    from planner_desktop.sync.sync_types import CalendarEvent

    gateway.insert_event(CalendarEvent(
        summary="С телефона", start=datetime(2026, 7, 15, 9, 0),
        end=datetime(2026, 7, 15, 10, 0)))
    result = sync.run_once()
    assert result.ok is True and result.pulled == 1
    titles = [t.title for t in repo.list_all()]
    assert "С телефона" in titles


# ---- ошибки ----------------------------------------------------------------------------

def test_gateway_provider_failure_is_user_facing_error(repo, store, task_service):
    def broken_provider():
        raise RuntimeError("Google Calendar не подключён: нет token.json")

    add_scheduled(task_service)
    sync = ManualSyncService(repo, store, gateway_provider=broken_provider)
    result = sync.run_once()

    assert result.ok is False
    assert "не подключён" in result.error
    assert result.pending_before == 1
    assert result.pending_after == 1  # очередь не тронута
    assert store.get_state(LAST_SYNC_ERROR_KEY) == result.error


def test_retryable_push_failure_keeps_cycle_ok(sync, task_service, store, gateway):
    """Временная ошибка одной операции — requeue, но цикл завершается."""
    add_scheduled(task_service)
    gateway.fail_next(RetryableGatewayError("HTTP 503"))

    result = sync.run_once()

    assert result.ok is True
    assert result.pushed == 0
    assert result.pending_after == 1  # операция ждёт ретрая с бэкоффом
    assert result.terminal_ops == 0


def test_terminal_push_failure_reports_dead_letter(sync, task_service, gateway):
    add_scheduled(task_service)
    gateway.fail_next(TerminalGatewayError("HTTP 400"))

    result = sync.run_once()

    assert result.ok is True
    assert result.pending_after == 0
    assert result.terminal_ops == 1  # dead-letter виден в сводке


def test_pull_failure_reports_error_and_releases_lock(repo, store, task_service, gateway):
    class ExplodingPullGateway:
        def list_changes(self, cursor):
            raise RetryableGatewayError("сеть пропала")

    sync = ManualSyncService(repo, store,
                             gateway_provider=lambda: ExplodingPullGateway())
    result = sync.run_once()
    assert result.ok is False
    assert "прервана" in result.error
    assert sync.is_running is False  # блокировка снята — можно повторить

    # повторный запуск после ошибки работает
    assert sync.run_once().ok is False


# ---- одновременные запуски -----------------------------------------------------------------

def test_second_simultaneous_run_is_refused(repo, store, sync, gateway):
    """Реентерабельный вызов во время работающего цикла честно отклоняется
    (threading.Lock не реентерабелен — вложенный acquire не проходит)."""
    inner_results = []

    class ReentrantGateway(FakeCalendarGateway):
        def list_changes(self, cursor):
            inner_results.append(sync.run_once())  # «второй клик» во время синка
            return super().list_changes(cursor)

    reentrant = ReentrantGateway()
    sync._gateway_provider = lambda: reentrant

    outer = sync.run_once()

    assert outer.ok is True
    assert len(inner_results) == 1
    assert inner_results[0].ok is False
    assert inner_results[0].error == SYNC_ALREADY_RUNNING_ERROR


def test_is_running_false_when_idle(sync):
    assert sync.is_running is False


# ---- запуск из фонового потока (регрессия: sqlite3 не переносится) ---------------------

def test_for_db_path_runs_from_background_thread(db_path, task_service, gateway):
    """GUI запускает run_once() в Qt-потоке пула: сервис ОБЯЗАН открывать
    свои SQLite-соединения в потоке выполнения (for_db_path), иначе
    sqlite3.ProgrammingError «created in a different thread»."""
    import threading

    add_scheduled(task_service)
    sync = ManualSyncService.for_db_path(db_path, gateway_provider=lambda: gateway)

    results = []
    worker = threading.Thread(target=lambda: results.append(sync.run_once()))
    worker.start()
    worker.join(timeout=30)

    assert results, "фоновый запуск не завершился"
    result = results[0]
    assert result.ok is True, result.error
    assert result.pushed == 1
    assert len(gateway.events) == 1


def test_for_db_path_persists_last_sync_state(db_path, task_service, store, gateway):
    add_scheduled(task_service)
    sync = ManualSyncService.for_db_path(db_path, gateway_provider=lambda: gateway)
    result = sync.run_once()
    assert result.ok is True
    # сводка видна и через ДРУГОЕ соединение (фикстура store)
    assert store.get_state(LAST_SYNC_AT_KEY) is not None
    assert store.get_state(LAST_SYNC_SUMMARY_KEY) == result.summary
