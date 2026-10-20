"""Deterministic scheduling and real persisted queue integration, no Google."""
from PySide6.QtCore import QObject, Signal
import pytest

from planner_desktop.viewmodels.auto_sync import AutoSyncScheduler
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.manual_sync_service import ManualSyncService, ManualSyncResult
from planner_desktop.usecases.task_service import DesktopTaskService


class Timer(QObject):
    timeout = Signal()

    def __init__(self):
        super().__init__()
        self.delay = None

    def setSingleShot(self, value):
        pass

    def start(self, delay):
        self.delay = delay

    def stop(self):
        self.delay = None

    def isActive(self):
        return self.delay is not None

    def fire(self):
        assert self.isActive()
        self.stop()
        self.timeout.emit()


class Executor:
    def __init__(self):
        self.jobs = []

    def submit(self, fn, callback):
        self.jobs.append((fn, callback))

    def finish(self, outcome=None):
        fn, callback = self.jobs.pop(0)
        callback(fn() if outcome is None else outcome)


class Connection:
    connected = True
    has_client_secret = True
    token_path = "unused/token.json"
    client_secret_path = "unused/client_secret.json"


@pytest.fixture
def harness(tmp_path):
    repo = SQLiteTaskRepository(tmp_path / "app.db")
    store = CalendarSyncStore(repo.db_path)
    service = DesktopTaskService(repo, calendar_queue=store)
    gateway = FakeCalendarGateway()
    sync = ManualSyncService(repo, store, lambda: gateway)
    timer = Timer()
    scheduler = AutoSyncScheduler(timer=timer)
    executor = Executor()
    connection = Connection()
    vm = SettingsViewModel(service, manual_sync_service=sync,
                           executor=executor, connection_checker=lambda: connection,
                           connector=lambda: setattr(connection, "connected", True),
                           auto_sync_scheduler=scheduler)
    # Same signal wiring as MainWindow, including settings' local mutations.
    vm.tasksMutated.connect(vm.scheduleAutoSync)
    yield vm, scheduler, timer, executor, service, gateway, connection
    scheduler.stop()
    store.close()
    repo.close()


def test_constructor_and_refresh_never_start_network(harness):
    vm, scheduler, timer, executor, *_ = harness
    vm.refresh()
    vm.scheduleAutoSync()
    assert not timer.isActive()
    assert not executor.jobs


def test_startup_debounce_and_periodic_pull(harness):
    vm, scheduler, timer, executor, service, gateway, _ = harness
    vm.startAutoSync()
    assert timer.delay == 1000
    timer.fire()
    assert vm.syncRunning
    executor.finish()
    assert timer.delay == 60000
    # Rapid edits share one delayed cycle; pull notifications never recurse.
    from datetime import date
    from planner_desktop.domain.task import Task
    service.create_task(Task(title="Тест автосинка", start=date(2026, 10, 19),
                             end=date(2026, 10, 20), is_all_day=True))
    for _ in range(3):
        vm.scheduleAutoSync()
    assert timer.delay == 1000
    timer.fire()
    assert len(executor.jobs) == 1
    executor.finish()
    assert len(gateway.events) == 1
    assert service.count_pending_ops() == 0
    assert timer.delay == 60000
    timer.fire()
    assert len(executor.jobs) == 1


def test_edits_during_sync_schedule_followup_without_overlap(harness):
    vm, scheduler, timer, executor, *_ = harness
    vm.startAutoSync()
    timer.fire()
    vm.syncNow()
    vm.scheduleAutoSync()
    vm.scheduleAutoSync()
    assert len(executor.jobs) == 1
    executor.finish()
    assert timer.delay == 1000
    timer.fire()
    assert len(executor.jobs) == 1


def test_offline_backoff_is_not_reset_by_editing_and_recovers(harness):
    vm, scheduler, timer, executor, *_ = harness
    toasts = []
    vm.toastMessage.connect(toasts.append)
    vm.startAutoSync()
    timer.fire()
    executor.finish(ManualSyncResult(ok=False, error="Нет сети"))
    assert timer.delay == 5000
    assert "повторим автоматически" in vm.syncStatusText
    vm.scheduleAutoSync()
    assert timer.delay == 5000
    timer.fire()
    executor.finish(RuntimeError("Нет сети"))
    assert timer.delay == 10000
    # A manual retry uses the same lock, can run immediately, and recovers.
    vm.syncNow()
    assert not timer.isActive()
    executor.finish()
    assert timer.delay == 60000
    assert vm.lastSyncError == ""
    assert len(toasts) == 1


def test_auto_success_does_not_show_toast_and_pending_ops_retry(harness):
    vm, scheduler, timer, executor, *_ = harness
    toasts = []
    vm.toastMessage.connect(toasts.append)
    vm.startAutoSync()
    timer.fire()
    executor.finish(ManualSyncResult(ok=True, pending_after=1))
    assert timer.delay == 5000
    assert toasts == []


def test_disabled_setting_survives_restart_and_manual_sync_works(harness):
    vm, scheduler, timer, executor, service, *_ = harness
    vm.startAutoSync()
    vm.setAutoSyncEnabled(False)
    vm.scheduleAutoSync()
    assert not timer.isActive()
    other = SettingsViewModel(service)
    assert other.autoSyncEnabled is False
    vm.syncNow()
    executor.finish()
    assert not timer.isActive()
    vm.setAutoSyncEnabled(True)
    assert timer.delay == 1000


def test_disable_during_running_cycle_does_not_schedule_next(harness):
    vm, scheduler, timer, executor, *_ = harness
    vm.startAutoSync()
    timer.fire()
    vm.scheduleAutoSync()
    vm.setAutoSyncEnabled(False)
    executor.finish()
    assert not timer.isActive()
    assert not vm.syncRunning


def test_disconnected_profile_waits_without_oauth_then_syncs_after_connect(harness):
    vm, scheduler, timer, executor, service, gateway, connection = harness
    connection.connected = False
    vm.startAutoSync()
    timer.fire()
    assert not executor.jobs
    assert timer.delay == 60000
    vm.connectGoogle()
    assert len(executor.jobs) == 1
    executor.finish()
    assert timer.delay == 1000
    timer.fire()
    assert vm.syncRunning


def test_shutdown_stops_timer_even_when_inflight_cycle_finishes(harness):
    vm, scheduler, timer, executor, *_ = harness
    vm.startAutoSync()
    timer.fire()
    vm.stopAutoSync()
    executor.finish()
    assert not timer.isActive()


def test_settings_queue_actions_schedule_without_task_mutation(harness):
    from types import SimpleNamespace
    vm, scheduler, timer, executor, *_ = harness
    vm.startAutoSync()
    timer.fire()
    executor.finish()
    assert timer.delay == 60000
    vm._run_occurrence_resolution(SimpleNamespace(ok=True), mutates_tasks=False)
    assert timer.delay == 1000
    timer.fire()
    executor.finish()
    vm._run_remote_split_action(SimpleNamespace(ok=True))
    assert timer.delay == 1000
