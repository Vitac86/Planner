"""Master-aware pull keeps masters out of Task and preserves instance flow."""
from datetime import date, datetime, timezone

import pytest

from planner_desktop.domain.task import Task
from planner_desktop.repositories.external_series_repository import (
    InMemoryExternalSeriesRepository,
)
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.storage.sqlite_task_repository import SQLiteTaskRepository
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.sync.sync_types import CalendarEvent


@pytest.fixture()
def stack(tmp_path):
    db_path = tmp_path / "desktop.db"
    repo = SQLiteTaskRepository(db_path)
    store = CalendarSyncStore(db_path)
    external = InMemoryExternalSeriesRepository(repo)
    gateway = FakeCalendarGateway(
        base_time=datetime(2026, 7, 15, tzinfo=timezone.utc)
    )
    engine = CalendarSyncEngine(repo, store, gateway, external)
    yield repo, store, external, gateway, engine
    store.close()
    repo.close()


def master_event(remote_id=None, rule="RRULE:FREQ=DAILY;INTERVAL=1", **kwargs):
    defaults = dict(
        id=remote_id,
        summary="Google series",
        start=date(2026, 7, 15),
        end=date(2026, 7, 16),
        is_all_day=True,
        recurrence_lines=(rule,),
    )
    defaults.update(kwargs)
    return CalendarEvent(**defaults)


def test_supported_master_upserts_catalog_and_never_becomes_task(stack):
    repo, store, external, gateway, engine = stack
    created = gateway.insert_event(master_event())
    gateway.reset_call_counts()
    before_queue = store.count_pending_ops()
    assert engine.pull_remote_changes() == 1

    assert repo.get_by_google_event_id(created.id) is None
    catalog = external.get("google", "primary", created.id)
    assert catalog is not None and catalog.is_supported
    assert catalog.parsed_rule is not None
    assert store.count_pending_ops() == before_queue == 0
    assert gateway.write_call_count == 0
    assert engine.last_pull_stats.recurring_masters == 1
    assert engine.last_pull_stats.ordinary_events == 0


def test_master_is_skipped_not_imported_when_optional_catalog_is_absent(tmp_path):
    repo = SQLiteTaskRepository(tmp_path / "desktop.db")
    store = CalendarSyncStore(tmp_path / "desktop.db")
    gateway = FakeCalendarGateway()
    created = gateway.insert_event(master_event())
    engine = CalendarSyncEngine(repo, store, gateway)
    engine.pull_remote_changes()
    assert repo.get_by_google_event_id(created.id) is None
    store.close()
    repo.close()


def test_unsupported_master_preserves_raw_catalog_transport(stack):
    repo, _store, external, gateway, engine = stack
    raw = "RRULE:FREQ=MONTHLY;BYDAY=MO;BYSETPOS=1"
    created = gateway.insert_event(master_event(rule=raw))
    engine.pull_remote_changes()
    item = external.get("google", "primary", created.id)
    assert item.support_status == "unsupported"
    assert item.recurrence_lines == (raw,)
    assert "BYSETPOS" in item.unsupported_reason
    assert repo.get_by_google_event_id(created.id) is None
    assert engine.last_pull_stats.unsupported_masters == 1


def test_instance_still_imports_and_updates_as_task(stack):
    repo, _store, _external, gateway, engine = stack
    instance = gateway.insert_event(CalendarEvent(
        summary="Instance", start=date(2026, 7, 16), end=date(2026, 7, 17),
        is_all_day=True, recurring_event_id="master-x",
        original_start=datetime(2026, 7, 16),
    ))
    engine.pull_remote_changes()
    task = repo.get_by_google_event_id(instance.id)
    assert task.google_calendar_recurring_event_id == "master-x"

    gateway.patch_event(instance.id, {"summary": "Instance updated"})
    engine.pull_remote_changes()
    assert repo.get_by_google_event_id(instance.id).title == "Instance updated"


def test_cancelled_instance_tombstones_only_its_task(stack):
    repo, _store, _external, gateway, engine = stack
    instance = gateway.insert_event(CalendarEvent(
        summary="Instance", start=date(2026, 7, 16), end=date(2026, 7, 17),
        is_all_day=True, recurring_event_id="master-x",
        original_start=datetime(2026, 7, 16),
    ))
    engine.pull_remote_changes()
    task_id = repo.get_by_google_event_id(instance.id).id
    gateway.delete_event(instance.id)
    engine.pull_remote_changes()
    assert repo.get(task_id).is_deleted


def test_cancelled_master_marks_catalog_without_deleting_instance_history(stack):
    repo, _store, external, gateway, engine = stack
    master = gateway.insert_event(master_event())
    instance = gateway.insert_event(CalendarEvent(
        summary="Completed history", start=date(2026, 7, 16),
        end=date(2026, 7, 17), is_all_day=True,
        recurring_event_id=master.id, original_start=datetime(2026, 7, 16),
    ))
    engine.pull_remote_changes()
    task = repo.get_by_google_event_id(instance.id)
    repo.complete(task.id, True)

    gateway.delete_event(master.id)
    engine.pull_remote_changes()
    assert external.get("google", "primary", master.id).is_cancelled
    assert repo.get(task.id).completed
    assert not repo.get(task.id).is_deleted
    assert engine.last_pull_stats.cancelled_masters == 1


def test_cancelled_incomplete_master_stub_is_recognized_from_prior_catalog(stack):
    repo, store, external, gateway, engine = stack
    master = gateway.insert_event(master_event())
    engine.pull_remote_changes()
    # Simulate Google's minimal cancelled payload in a deterministic gateway.
    gateway._events[master.id] = CalendarEvent(
        id=master.id, etag='"2"', status="cancelled"
    )
    gateway._change_log.append(master.id)
    engine.pull_remote_changes()
    assert external.get("google", "primary", master.id).is_cancelled
    assert repo.get_by_google_event_id(master.id) is None
    assert store.count_pending_ops() == 0


def test_catalog_failure_does_not_advance_cursor_or_create_master_task(tmp_path):
    class FailingCatalog(InMemoryExternalSeriesRepository):
        def upsert(self, series):
            raise RuntimeError("catalog write failed")

    repo = SQLiteTaskRepository(tmp_path / "desktop.db")
    store = CalendarSyncStore(tmp_path / "desktop.db")
    gateway = FakeCalendarGateway()
    master = gateway.insert_event(master_event())
    engine = CalendarSyncEngine(repo, store, gateway, FailingCatalog(repo))
    with pytest.raises(RuntimeError, match="catalog write failed"):
        engine.pull_remote_changes()
    assert store.get_sync_cursor() is None
    assert repo.get_by_google_event_id(master.id) is None
    store.close()
    repo.close()


def test_pending_ordinary_task_protection_is_unchanged(stack):
    repo, store, _external, gateway, engine = stack
    remote = gateway.insert_event(CalendarEvent(
        summary="Remote", start=datetime(2026, 7, 15, 10),
        end=datetime(2026, 7, 15, 11),
    ))
    engine.pull_remote_changes()
    task = repo.get_by_google_event_id(remote.id)
    task.title = "Local pending"
    repo.update(task)
    store.enqueue_update(task.uid)
    gateway.patch_event(remote.id, {"summary": "Remote newer"})
    engine.pull_remote_changes()
    assert repo.get_by_uid(task.uid).title == "Local pending"

