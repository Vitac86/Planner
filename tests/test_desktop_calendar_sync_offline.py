"""Offline time is not a failed operation; real errors still have a retry limit."""
from datetime import datetime, timedelta
from http.client import RemoteDisconnected

import pytest

from planner_desktop.domain.google_occurrence import local_occurrence_to_google_original_start
from planner_desktop.domain.series_calendar_link import SeriesCalendarLink
from planner_desktop.domain.task import Task, utc_now
from planner_desktop.storage.calendar_series_sync_store import CalendarSeriesSyncStore
from planner_desktop.storage.calendar_sync_store import (
    CalendarSyncStore, MAX_ATTEMPTS, RETRY_MAX_DELAY_SECONDS,
)
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.sync.google_calendar_gateway import _classify
from planner_desktop.sync.sync_types import NetworkUnavailableError, RetryableGatewayError
from tests.occurrence_sync_testkit import linked_occurrence_store, timed_series


@pytest.mark.parametrize("error", [
    ConnectionError("offline"), TimeoutError("timeout"),
    OSError("DNS unavailable"), RemoteDisconnected("peer closed"),
])
def test_transport_failures_have_separate_retry_policy(error):
    assert isinstance(_classify(error, "Calendar request"), NetworkUnavailableError)


def test_unknown_adapter_error_keeps_bounded_retry_policy():
    error = _classify(ValueError("invalid adapter result"), "Calendar request")
    assert isinstance(error, RetryableGatewayError)
    assert not isinstance(error, NetworkUnavailableError)


@pytest.fixture(params=["ordinary", "series", "occurrence"])
def queued_store(request, tmp_path):
    now = [utc_now() + timedelta(days=1)]
    path = tmp_path / "offline.db"
    master = None
    if request.param == "ordinary":
        store = CalendarSyncStore(path, clock=lambda: now[0])
        store.enqueue_create("task-1")
    elif request.param == "series":
        store = CalendarSeriesSyncStore(path, clock=lambda: now[0])
        store.create_pending_link(
            SeriesCalendarLink(series_uid="series-1", remote_event_id="master-1"),
            desired_revision=1, desired_payload_hash="hash",
            payload={"summary": "Series"},
        )
    else:
        series = timed_series()
        master, store, link = linked_occurrence_store(path, series)
        store._clock = lambda: now[0]
        key = "2026-07-20T09:00@Europe/Moscow"
        store.ensure_occurrence_link(
            series.uid, key, link, local_occurrence_to_google_original_start(series, key),
        )
        store.enqueue_update(series.uid, key, {"summary": "Instance"})
    yield store, now
    store.close()
    if master is not None:
        master.close()


def test_long_outage_keeps_operations_pending_without_using_retry_budget(queued_store):
    store, now = queued_store
    for _ in range(MAX_ATTEMPTS * 3):
        op = store.list_due_ops()[0]
        assert op.attempts == 0
        store.requeue_op(op.id, "Offline", consume_attempt=False)
        assert store.count_terminal_ops() == 0
        assert store.list_due_ops() == []  # outage cannot create a busy retry loop
        now[0] += timedelta(seconds=RETRY_MAX_DELAY_SECONDS + 1)
    assert store.list_due_ops()[0].attempts == 0


def test_operation_failures_still_exhaust_retry_budget(queued_store):
    store, now = queued_store
    for _ in range(MAX_ATTEMPTS):
        op = store.list_due_ops()[0]
        store.requeue_op(op.id, "Repeated operation failure")
        now[0] += timedelta(seconds=RETRY_MAX_DELAY_SECONDS + 1)
    assert store.list_due_ops() == []
    assert store.count_terminal_ops() == 1


def test_task_sync_resumes_after_long_outage_without_user_action(tmp_path):
    now = [utc_now()]
    path = tmp_path / "recover.db"
    repo = SQLiteTaskRepository(path)
    store = CalendarSyncStore(path, clock=lambda: now[0])
    gateway = FakeCalendarGateway()
    engine = CalendarSyncEngine(repo, store, gateway)
    try:
        task = repo.add(Task(
            title="Added offline", start=datetime(2026, 10, 20, 10),
            end=datetime(2026, 10, 20, 11),
        ))
        engine.handle_local_task_created(task)
        for _ in range(MAX_ATTEMPTS * 3):
            gateway.fail_next(NetworkUnavailableError("Network unavailable"))
            engine.push_pending()
            assert store.count_pending_ops() == 1
            assert store.count_terminal_ops() == 0
            now[0] += timedelta(seconds=RETRY_MAX_DELAY_SECONDS + 1)
        engine.sync_once()
        synced = repo.get(task.id)
        assert gateway.get_event(synced.google_calendar_event_id).summary == task.title
        assert store.count_pending_ops() == 0
        assert store.count_terminal_ops() == 0
    finally:
        store.close()
        repo.close()
