from datetime import datetime

from planner_desktop.domain.google_occurrence import (
    OccurrenceSyncStatus,
    local_occurrence_to_google_original_start,
)
from planner_desktop.domain.task import Task
from planner_desktop.storage.calendar_series_occurrence_sync_store import (
    CalendarSeriesOccurrenceSyncStore,
)
from planner_desktop.sync.calendar_series_occurrence_mapper import (
    build_desired_occurrence_payload,
)
from planner_desktop.sync.calendar_series_occurrence_sync_engine import (
    CalendarSeriesOccurrenceSyncEngine,
)
from tests.occurrence_sync_testkit import (
    linked_occurrence_store,
    owned_gateway_with_instance,
    timed_series,
)


def make_engine_stack(tmp_path, store_cls=CalendarSeriesOccurrenceSyncStore):
    db = tmp_path / "desktop.db"
    series = timed_series()
    master_store, original_store, link = linked_occurrence_store(db, series)
    if store_cls is CalendarSeriesOccurrenceSyncStore:
        store = original_store
    else:
        original_store.close()
        store = store_cls(db)
    key = "2026-07-20T09:00@Europe/Moscow"
    identity = local_occurrence_to_google_original_start(series, key)
    store.ensure_occurrence_link(series.uid, key, link, identity)
    task = Task(
        title="Planner moved",
        notes="Planner notes",
        start=datetime(2026, 7, 20, 10),
        end=datetime(2026, 7, 20, 10, 45),
        duration_minutes=45,
        series_uid=series.uid,
        occurrence_key=key,
        is_series_exception=True,
    )
    desired = build_desired_occurrence_payload(
        task, series, link.link_generation
    )
    gateway = owned_gateway_with_instance(series, key)
    engine = CalendarSeriesOccurrenceSyncEngine(store, gateway)
    return series, key, task, desired, master_store, store, gateway, engine


def test_update_writes_one_instance_preserves_google_fields_and_not_master(tmp_path):
    (
        series, key, task, desired, master, store, gateway, engine
    ) = make_engine_stack(tmp_path)
    master_before = gateway.get_recurring_master("master-1")
    store.enqueue_update(series.uid, key, desired)
    result = engine.push_pending()
    assert result.updates_pushed == 1
    assert gateway.write_call_count == 1
    remote = gateway.get_recurring_instance("instance-1")
    assert remote["summary"] == task.title
    assert remote["attendees"] == [{"email": "kept@example.test"}]
    assert remote["location"] == "Keep this"
    assert "recurrence" not in remote
    assert gateway.get_recurring_master("master-1") == master_before
    link = store.get_occurrence_link(series.uid, key)
    assert link.sync_status is OccurrenceSyncStatus.SYNCED_EXCEPTION
    assert link.remote_instance_event_id == "instance-1"
    assert store.get_pending_op(series.uid, key) is None
    store.close()
    master.close()


def test_cancel_persists_remote_identity_and_cancelled_link(tmp_path):
    (
        series, key, _task, desired, master, store, gateway, engine
    ) = make_engine_stack(tmp_path)
    store.enqueue_cancel(series.uid, key, desired)
    result = engine.push_pending()
    assert result.cancellations_pushed == 1
    assert gateway.get_recurring_instance("instance-1")["status"] == "cancelled"
    link = store.get_occurrence_link(series.uid, key)
    assert link.sync_status is OccurrenceSyncStatus.CANCELLED
    assert link.is_cancelled_remote
    assert link.remote_instance_event_id == "instance-1"
    store.close()
    master.close()


def test_wrong_parent_known_instance_becomes_terminal_without_write(tmp_path):
    (
        series, key, _task, desired, master, store, gateway, engine
    ) = make_engine_stack(tmp_path)
    gateway.seed_recurring_instance(
        {
            **gateway.get_recurring_instance("instance-1"),
            "id": "wrong-parent",
            "recurringEventId": "other-master",
        }
    )
    occurrence_link = store.get_occurrence_link(series.uid, key)
    occurrence_link.remote_instance_event_id = "wrong-parent"
    store.update_occurrence_link(occurrence_link)
    store.enqueue_update(series.uid, key, desired)
    result = engine.push_pending()
    assert result.terminal == 1
    assert gateway.write_call_count == 0
    assert store.count_terminal_ops() == 1
    store.close()
    master.close()
