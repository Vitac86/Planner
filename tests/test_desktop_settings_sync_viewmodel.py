"""Тесты синк-части SettingsViewModel: состояния кнопок
(отключено/подключено/выполняется), подключение Google, ручной синк,
ошибки в UI. Без окна, без потоков (ручной executor), без сети.
"""
from dataclasses import dataclass, field
from typing import Callable, List, Optional

import pytest

from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.manual_sync_service import (
    ManualSyncResult,
    ManualSyncService,
)
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel


# ---- фейковые зависимости ------------------------------------------------------------

@dataclass
class FakeStatus:
    has_client_secret: bool = True
    has_token: bool = True
    token_path: str = "X:/profile/token.json"
    client_secret_path: str = "X:/profile/secrets/client_secret.json"

    @property
    def connected(self) -> bool:
        return self.has_token


class ManualExecutor:
    """submit() копит работу; тест сам решает, когда её «завершить».

    Позволяет детерминированно проверять состояние «выполняется»
    без потоков и Qt-событийного цикла.
    """

    def __init__(self):
        self.queue: List = []

    def submit(self, fn: Callable, callback: Callable) -> None:
        self.queue.append((fn, callback))

    def run_next(self) -> None:
        fn, callback = self.queue.pop(0)
        try:
            outcome = fn()
        except Exception as exc:
            outcome = exc
        callback(outcome)


@dataclass
class FakeSyncService:
    result: ManualSyncResult = field(
        default_factory=lambda: ManualSyncResult(ok=True, pushed=1, pulled=2))
    calls: int = 0

    def run_once(self) -> ManualSyncResult:
        self.calls += 1
        return self.result


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


def make_vm(task_service, *, status: Optional[FakeStatus] = None,
            sync_service=None, connector=None,
            executor: Optional[ManualExecutor] = None) -> SettingsViewModel:
    current = status if status is not None else FakeStatus()
    return SettingsViewModel(
        task_service,
        manual_sync_service=sync_service,
        connection_checker=lambda: current,
        connector=connector or (lambda: object()),
        executor=executor or ManualExecutor(),
    )


# ---- состояния кнопок -----------------------------------------------------------------

def test_disconnected_state_disables_sync_enables_connect(task_service):
    vm = make_vm(task_service, status=FakeStatus(has_token=False),
                 sync_service=FakeSyncService())
    assert vm.googleConnected is False
    assert vm.manualSyncEnabled is False
    assert vm.connectEnabled is True
    assert "не подключён" in vm.connectionStatusText


def test_no_client_secret_disables_connect_and_shows_path(task_service):
    status = FakeStatus(has_client_secret=False, has_token=False)
    vm = make_vm(task_service, status=status, sync_service=FakeSyncService())
    assert vm.connectEnabled is False
    assert status.client_secret_path in vm.connectionStatusText


def test_connected_state_enables_sync(task_service):
    vm = make_vm(task_service, sync_service=FakeSyncService())
    assert vm.googleConnected is True
    assert vm.manualSyncEnabled is True


def test_without_sync_service_button_stays_disabled(task_service):
    vm = make_vm(task_service, sync_service=None)
    assert vm.manualSyncEnabled is False


# ---- ручной синк -----------------------------------------------------------------------

def test_sync_now_running_state_and_success(task_service):
    executor = ManualExecutor()
    fake_sync = FakeSyncService()
    vm = make_vm(task_service, sync_service=fake_sync, executor=executor)
    mutated, toasts = [], []
    vm.tasksMutated.connect(lambda: mutated.append(1))
    vm.toastMessage.connect(toasts.append)

    vm.syncNow()

    # работа отправлена в фон: кнопки выключены, статус «выполняется»
    assert vm.syncRunning is True
    assert vm.syncBusy is True
    assert vm.manualSyncEnabled is False
    assert len(executor.queue) == 1

    executor.run_next()  # «фон» завершился

    assert fake_sync.calls == 1
    assert vm.syncRunning is False
    assert vm.manualSyncEnabled is True   # кнопка восстановлена
    assert vm.lastSyncError == ""
    assert mutated == [1]                 # страницы задач будут освежены
    assert toasts and "Синхронизировано" in toasts[0]


def test_second_click_while_running_is_ignored(task_service):
    executor = ManualExecutor()
    fake_sync = FakeSyncService()
    vm = make_vm(task_service, sync_service=fake_sync, executor=executor)

    vm.syncNow()
    vm.syncNow()  # повторный клик, пока работает

    assert len(executor.queue) == 1  # вторая работа не поставлена
    executor.run_next()
    assert fake_sync.calls == 1


def test_sync_failure_surfaces_error_and_restores_button(task_service):
    executor = ManualExecutor()
    fake_sync = FakeSyncService(
        result=ManualSyncResult(ok=False, error="Google Calendar не подключён"))
    vm = make_vm(task_service, sync_service=fake_sync, executor=executor)

    vm.syncNow()
    executor.run_next()

    assert vm.syncRunning is False
    assert vm.manualSyncEnabled is True
    assert "не подключён" in vm.lastSyncError


def test_unexpected_exception_surfaces_and_restores(task_service):
    executor = ManualExecutor()

    class ExplodingSync:
        def run_once(self):
            raise RuntimeError("неожиданное")

    vm = make_vm(task_service, sync_service=ExplodingSync(), executor=executor)
    vm.syncNow()
    executor.run_next()

    assert vm.syncBusy is False
    assert "неожиданное" in vm.lastSyncError


def test_sync_now_when_disconnected_sets_error_without_submitting(task_service):
    executor = ManualExecutor()
    vm = make_vm(task_service, status=FakeStatus(has_token=False),
                 sync_service=FakeSyncService(), executor=executor)
    vm.syncNow()
    assert executor.queue == []  # ничего не запущено
    assert "не подключён" in vm.lastSyncError


# ---- подключение Google -------------------------------------------------------------------

def test_connect_google_success_flow(task_service):
    executor = ManualExecutor()
    status = FakeStatus(has_token=False)
    calls = []

    def connector():
        calls.append(1)
        status.has_token = True  # OAuth сохранил токен в профиль
        return object()

    vm = make_vm(task_service, status=status, connector=connector,
                 executor=executor, sync_service=FakeSyncService())

    vm.connectGoogle()
    assert vm.connectRunning is True
    assert vm.connectEnabled is False

    executor.run_next()

    assert calls == [1]
    assert vm.connectRunning is False
    assert vm.googleConnected is True
    assert vm.manualSyncEnabled is True
    assert vm.lastSyncError == ""


def test_connect_failure_surfaces_error_and_restores(task_service):
    executor = ManualExecutor()

    def connector():
        raise RuntimeError("пользователь закрыл браузер")

    vm = make_vm(task_service, status=FakeStatus(has_token=False),
                 connector=connector, executor=executor)
    vm.connectGoogle()
    executor.run_next()

    assert vm.connectRunning is False
    assert vm.connectEnabled is True
    assert "Подключение не удалось" in vm.lastSyncError


def test_connect_without_secret_sets_error_without_submitting(task_service):
    executor = ManualExecutor()
    vm = make_vm(task_service,
                 status=FakeStatus(has_client_secret=False, has_token=False),
                 executor=executor)
    vm.connectGoogle()
    assert executor.queue == []
    assert "client_secret.json" in vm.lastSyncError


# ---- сводка последнего синка ----------------------------------------------------------------

def test_last_sync_info_read_from_persisted_state(task_service, repo, store):
    """Сводка, сохранённая ManualSyncService, видна после «перезапуска» VM."""
    gateway = FakeCalendarGateway()
    real_sync = ManualSyncService(repo, store, gateway_provider=lambda: gateway)
    real_sync.run_once()

    vm = make_vm(task_service, sync_service=None)
    assert vm.lastSyncAt != "—"
    assert vm.lastSyncSummary.startswith("Синхронизировано")
    assert vm.lastSyncError == ""
