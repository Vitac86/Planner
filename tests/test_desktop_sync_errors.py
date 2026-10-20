"""Actionable ordinary-sync failures, with durable generation-safe retries."""
from datetime import datetime
import json

import pytest

from planner_desktop.domain.task import Task
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.usecases.manual_sync_service import ManualSyncService
from planner_desktop.usecases.task_service import DesktopTaskService
from planner_desktop.viewmodels.settings_viewmodel import SettingsViewModel
from planner_desktop.viewmodels.sync_errors_viewmodel import SyncErrorsViewModel


@pytest.fixture
def setup(tmp_path):
    repo = SQLiteTaskRepository(tmp_path / "app.db")
    queue = CalendarSyncStore(repo.db_path)
    service = DesktopTaskService(repo, calendar_queue=queue)
    yield repo, queue, service
    queue.close()
    repo.close()


def scheduled(service, title="Встреча", **kwargs):
    return service.create_task(Task(
        title=title, start=datetime(2030, 1, 2, 10),
        end=datetime(2030, 1, 2, 11), **kwargs,
    ))


def fail(queue, uid, error="HTTP 503. unavailable"):
    op = next(op for op in queue.list_due_ops() if op.task_uid == uid)
    queue.mark_terminal(op.id, error, attempts=5)
    return op.id


def test_shows_task_action_and_reason_without_provider_payload(setup):
    _, queue, service = setup
    task = scheduled(service, "Встреча <b>важная</b>")
    op_id = fail(queue, task.uid, "HTTP 400. raw token=secret eventPayload={private}")
    vm = SyncErrorsViewModel(service)
    row = vm.rows[0]
    assert row["operationId"] == op_id
    assert row["title"] == task.title
    assert row["action"] == "Создать событие в Google"
    assert "отклонил данные" in row["reason"]
    assert row["canOpen"] and not row["canRetry"]
    assert "secret" not in str(row) and "private" not in str(row)
    assert vm.hasErrors


def test_retry_uses_fresh_generation_and_survives_reopen(setup):
    repo, queue, service = setup
    task = scheduled(service)
    old_id = fail(queue, task.uid)
    vm = SyncErrorsViewModel(service)
    requests = []
    vm.syncRequested.connect(lambda: requests.append(True))
    assert vm.retryOperation(old_id)
    assert requests == [True]
    assert queue.list_terminal_ops() == []
    fresh = queue.list_due_ops()[0]
    assert fresh.id > old_id and fresh.attempts == 0 and fresh.last_error is None
    assert fresh.task_uid == task.uid
    # An obsolete worker can neither acknowledge nor fail the new generation.
    queue.remove_op(old_id)
    queue.mark_terminal(old_id, "late failure")
    reopened = CalendarSyncStore(repo.db_path)
    try:
        assert reopened.list_due_ops()[0].id == fresh.id
    finally:
        reopened.close()


def test_stale_click_does_not_replace_newer_operation(setup):
    _, queue, service = setup
    task = scheduled(service)
    stale_id = fail(queue, task.uid)
    vm = SyncErrorsViewModel(service)
    queue.enqueue_create(task.uid)
    expected = queue.snapshot_task_ops(task.uid)
    assert not vm.retryOperation(stale_id)
    assert queue.snapshot_task_ops(task.uid) == expected
    assert "изменилась" in vm.errorText


def test_retry_cannot_replay_if_failure_changed_to_conflict(setup):
    _, queue, service = setup
    task = scheduled(service)
    op_id = fail(queue, task.uid, "HTTP 503")
    queue.mark_terminal(op_id, "HTTP 412")
    with pytest.raises(ValueError, match="Причина ошибки изменилась"):
        queue.retry_terminal_op(op_id, expected_last_error="HTTP 503")
    assert queue.list_terminal_ops()[0].last_error == "HTTP 412"
    assert queue.count_pending_ops() == 0


def test_older_terminal_create_cannot_override_newer_update(setup):
    repo, queue, service = setup
    task = scheduled(service)
    stale_id = fail(queue, task.uid)
    task.google_calendar_event_id = "new-event"
    repo.update(task)
    queue.enqueue_update(task.uid)
    before = queue.snapshot_task_ops(task.uid)
    with pytest.raises(ValueError, match="более новые"):
        queue.retry_terminal_op(stale_id)
    assert queue.snapshot_task_ops(task.uid) == before


@pytest.mark.parametrize("state", ["missing", "deleted", "undated"])
def test_create_retry_checks_current_task_state(setup, state):
    repo, queue, service = setup
    task = scheduled(service)
    op_id = fail(queue, task.uid)
    if state == "missing":
        repo.hard_delete_by_uid(task.uid)
    else:
        if state == "deleted":
            task.mark_deleted()
        else:
            task.start = None
            task.end = None
        repo.update(task)
    vm = SyncErrorsViewModel(service)
    assert not vm.rows[0]["canRetry"]
    assert not vm.retryOperation(op_id)
    assert queue.count_terminal_ops() == 1
    if state in {"missing", "deleted"}:
        assert not vm.rows[0]["canOpen"]
        assert vm.rows[0]["taskState"]


def test_cleanup_retry_keeps_exact_old_target_and_new_task_work(setup):
    _, queue, service = setup
    task = scheduled(service, google_calendar_event_id="new-event")
    queue.cancel_pending_ops(task.uid)
    queue.enqueue_event_cleanup(task.uid, "old-event")
    op_id = fail(queue, task.uid)
    queue.enqueue_update(task.uid)
    new_id = queue.retry_terminal_op(op_id)
    operations = queue.list_due_ops()
    assert len(operations) == 2
    cleanup = next(op for op in operations if op.id == new_id)
    assert json.loads(cleanup.payload_json) == {"event_id": "old-event", "cleanup": True}
    assert any(op.op == "update" for op in operations)


@pytest.mark.parametrize("missing", [False, True])
def test_deleted_or_missing_task_can_retry_explicit_remote_delete(setup, missing):
    repo, queue, service = setup
    task = scheduled(service, google_calendar_event_id="old-event")
    service.delete_task_by_uid(task.uid)
    if missing:
        repo.hard_delete_by_uid(task.uid)
    op_id = fail(queue, task.uid)
    vm = SyncErrorsViewModel(service)
    row = vm.rows[0]
    assert row["canRetry"] and not row["canOpen"]
    assert row["action"] == "Удалить событие в Google"
    assert vm.retryOperation(op_id)
    assert json.loads(queue.list_due_ops()[0].payload_json)["event_id"] == "old-event"


@pytest.mark.parametrize("another_owner", [False, True])
def test_delete_cannot_remove_event_now_owned_by_live_task(setup, another_owner):
    repo, queue, service = setup
    task = scheduled(service, google_calendar_event_id="same-event")
    service.delete_task_by_uid(task.uid)
    op_id = fail(queue, task.uid)
    if another_owner:
        scheduled(service, "Новая задача", google_calendar_event_id="same-event")
    else:
        task.deleted_at = None
        repo.update(task)
    vm = SyncErrorsViewModel(service)
    assert not next(row for row in vm.rows if row["operationId"] == op_id)["canRetry"]
    assert not vm.retryOperation(op_id)
    assert "снова связано" in vm.errorText
    assert any(op.id == op_id for op in queue.list_terminal_ops())


def test_delete_without_explicit_target_is_never_inferred_from_task(setup):
    _, queue, service = setup
    task = scheduled(service, google_calendar_event_id="must-not-delete")
    queue.enqueue_delete(task.uid)
    op_id = fail(queue, task.uid)
    with pytest.raises(ValueError, match="однозначно"):
        queue.retry_terminal_op(op_id)
    assert queue.count_terminal_ops() == 1


def test_redundant_delete_generation_cannot_be_retried(setup):
    _, queue, service = setup
    queue.enqueue_delete("missing", {"event_id": "old-event"})
    op_id = fail(queue, "missing")
    queue.enqueue_event_cleanup("missing", "old-event")
    before = queue.snapshot_task_ops("missing")
    with pytest.raises(ValueError, match="более новой"):
        queue.retry_terminal_op(op_id)
    assert queue.snapshot_task_ops("missing") == before


def test_busy_blocks_retry_without_mutating_queue(setup):
    _, queue, service = setup
    task = scheduled(service)
    op_id = fail(queue, task.uid)
    vm = SyncErrorsViewModel(service, busy_provider=lambda: True)
    before = queue.snapshot_task_ops(task.uid)
    assert not vm.retryOperation(op_id)
    assert queue.snapshot_task_ops(task.uid) == before


@pytest.mark.parametrize("error", ["HTTP 400", "HTTP 404", "HTTP 410", "HTTP 409", "HTTP 412"])
def test_permanent_validation_or_conflict_requires_checking_task(setup, error):
    _, queue, service = setup
    task = scheduled(service)
    op_id = fail(queue, task.uid, error)
    vm = SyncErrorsViewModel(service)
    assert not vm.rows[0]["canRetry"]
    assert not vm.retryOperation(op_id)
    assert queue.count_pending_ops() == 0 and queue.count_terminal_ops() == 1


@pytest.mark.parametrize("error", ["HTTP 401", "HTTP 403. forbidden", "invalid_grant: revoked"])
def test_auth_errors_offer_reconnection(setup, error):
    _, queue, service = setup
    task = scheduled(service)
    fail(queue, task.uid, error)
    vm = SyncErrorsViewModel(service)
    assert vm.reconnectNeeded and vm.rows[0]["reconnect"]
    assert "Google" in vm.rows[0]["reason"]


def test_pending_failure_explains_automatic_retry_without_resetting_attempts(setup):
    _, queue, service = setup
    task = scheduled(service)
    op = queue.list_due_ops()[0]
    queue.requeue_op(op.id, "HTTP 429. quota")
    vm = SyncErrorsViewModel(service)
    row = vm.rows[0]
    assert row["statusText"] == "Ожидает повторной попытки"
    assert "Повтор уже запланирован" in row["hint"]
    assert not row["canRetry"] and not vm.retryOperation(op.id)
    assert queue.snapshot_task_ops(task.uid)[0]["attempts"] == 1


def test_global_auth_error_is_explained_without_raw_secrets(setup):
    _, _, service = setup
    vm = SyncErrorsViewModel(service, last_error_provider=lambda: "invalid_grant token=secret")
    assert vm.rows == [] and vm.hasErrors and vm.reconnectNeeded
    assert "Подключите Google заново" in vm.cycleErrorReason
    assert "secret" not in vm.cycleErrorReason


def test_open_task_rechecks_deleted_state(setup):
    _, queue, service = setup
    task = scheduled(service)
    fail(queue, task.uid)
    vm = SyncErrorsViewModel(service)
    opened = []
    vm.openTaskRequested.connect(opened.append)
    vm.openTask(task.uid)
    assert opened == [task.uid]
    service.delete_task_by_uid(task.uid)
    vm.openTask(task.uid)
    assert opened == [task.uid]
    assert "уже удалена" in vm.errorText


def test_demo_has_no_error_queue_or_retry():
    service = DesktopTaskService(FakeTaskRepository())
    vm = SyncErrorsViewModel(service)
    assert not vm.hasErrors and vm.rows == []
    assert not vm.retryOperation(1)


def test_explicit_retry_uses_settings_shared_executor_with_auto_disabled(setup):
    repo, queue, service = setup
    task = scheduled(service)
    op_id = fail(queue, task.uid)
    gateway = FakeCalendarGateway()
    sync = ManualSyncService(repo, queue, lambda: gateway)

    class Connection:
        connected = True
        has_client_secret = True
        token_path = "unused"
        client_secret_path = "unused"

    class Executor:
        jobs = []

        def submit(self, fn, callback):
            self.jobs.append((fn, callback))

    executor = Executor()
    settings = SettingsViewModel(service, manual_sync_service=sync,
                                 executor=executor, connection_checker=Connection)
    settings.setAutoSyncEnabled(False)
    vm = SyncErrorsViewModel(service, busy_provider=lambda: settings.syncBusy,
                            last_error_provider=lambda: settings.lastSyncError)
    vm.syncRequested.connect(settings.syncNow)
    settings.syncStateChanged.connect(vm.refresh)
    assert vm.retryOperation(op_id)
    assert settings.syncBusy and len(executor.jobs) == 1
    assert not vm.retryOperation(op_id)
    fn, done = executor.jobs.pop()
    done(fn())
    assert not settings.syncBusy and not vm.hasErrors
    assert queue.count_pending_ops() == 0
    assert len(gateway.events) == 1
