from planner_desktop.domain.google_occurrence import (
    OccurrenceSyncStatus,
    canonical_occurrence_payload_fingerprint,
    local_occurrence_to_google_original_start,
)
from planner_desktop.repositories.external_series_repository import (
    InMemoryExternalSeriesRepository,
)
from planner_desktop.repositories.fake_task_repository import FakeTaskRepository
from planner_desktop.repositories.series_repository import (
    InMemorySeriesRepository,
)
from planner_desktop.storage.calendar_sync_store import CalendarSyncStore
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from tests.occurrence_sync_testkit import (
    linked_occurrence_store,
    owned_gateway_with_instance,
    timed_series,
)


def make_pull_stack(tmp_path, *, cancelled=False):
    db = tmp_path / "desktop.db"
    series = timed_series()
    series_repo = InMemorySeriesRepository()
    series_repo.add(series)
    tasks = FakeTaskRepository(seed=False)
    master, occurrence, link = linked_occurrence_store(db, series)
    key = "2026-07-20T09:00@Europe/Moscow"
    occurrence.ensure_occurrence_link(
        series.uid,
        key,
        link,
        local_occurrence_to_google_original_start(series, key),
    )
    ordinary = CalendarSyncStore(db)
    catalog = InMemoryExternalSeriesRepository(tasks)
    gateway = owned_gateway_with_instance(series, key)
    if cancelled:
        remote = gateway.get_recurring_instance("instance-1")
        remote["status"] = "cancelled"
        remote["etag"] = '"2"'
        gateway.seed_recurring_instance(remote)
    pull = CalendarSyncEngine(
        tasks,
        ordinary,
        gateway,
        catalog,
        series_link_store=master,
        occurrence_sync_store=occurrence,
        series_repository=series_repo,
    )
    return (
        series, key, tasks, master, occurrence, ordinary, gateway, pull
    )


def test_changed_linked_instance_is_quarantined_not_ordinary_task(tmp_path):
    (
        series, key, tasks, master, occurrence, ordinary, _gateway, pull
    ) = make_pull_stack(tmp_path)
    pull.pull_remote_changes()
    changes = occurrence.list_occurrence_changes(unresolved_only=True)
    assert len(changes) == 1
    assert changes[0].matched_series_uid == series.uid
    assert changes[0].matched_occurrence_key == key
    assert tasks.get_by_google_event_id("instance-1") is None
    assert ordinary.count_pending_ops() == 0
    assert occurrence.get_occurrence_link(
        series.uid, key
    ).sync_status is OccurrenceSyncStatus.REMOTE_CHANGED
    assert pull.last_pull_stats.linked_instance_changes_quarantined == 1
    occurrence.close()
    ordinary.close()
    master.close()


def test_cancelled_remote_instance_is_quarantined_without_local_tombstone(
    tmp_path
):
    (
        series, key, tasks, master, occurrence, ordinary, _gateway, pull
    ) = make_pull_stack(tmp_path, cancelled=True)
    pull.pull_remote_changes()
    change = occurrence.list_occurrence_changes(unresolved_only=True)[0]
    assert change.status == "cancelled"
    assert tasks.get_by_google_event_id("instance-1") is None
    assert occurrence.get_occurrence_link(
        series.uid, key
    ).sync_status is OccurrenceSyncStatus.REMOTE_CANCELLED
    assert pull.last_pull_stats.occurrence_remote_cancellations == 1
    occurrence.close()
    ordinary.close()
    master.close()


def test_matching_last_synced_exception_is_echo_and_resolves_duplicate(tmp_path):
    (
        series, key, _tasks, master, occurrence, ordinary, gateway, pull
    ) = make_pull_stack(tmp_path)
    remote = gateway.get_recurring_instance("instance-1")
    link = occurrence.get_occurrence_link(series.uid, key)
    link.sync_status = OccurrenceSyncStatus.SYNCED_EXCEPTION
    link.last_synced_remote_hash = canonical_occurrence_payload_fingerprint(
        remote
    )
    occurrence.update_occurrence_link(link)
    pull.pull_remote_changes()
    assert occurrence.list_occurrence_changes(unresolved_only=True) == []
    refreshed = occurrence.get_occurrence_link(series.uid, key)
    assert refreshed.remote_instance_event_id == "instance-1"
    assert refreshed.remote_etag == '"1"'
    assert pull.last_pull_stats.linked_instance_changes_quarantined == 0
    occurrence.close()
    ordinary.close()
    master.close()


def test_quarantine_persistence_failure_does_not_advance_cursor(
    tmp_path, monkeypatch
):
    (
        _series, _key, _tasks, master, occurrence, ordinary, _gateway, pull
    ) = make_pull_stack(tmp_path)
    before = ordinary.get_sync_cursor()

    def fail(_change):
        raise RuntimeError("occurrence quarantine persistence failed")

    monkeypatch.setattr(occurrence, "upsert_occurrence_change", fail)
    import pytest

    with pytest.raises(RuntimeError, match="quarantine persistence failed"):
        pull.pull_remote_changes()
    assert ordinary.get_sync_cursor() == before
    occurrence.close()
    ordinary.close()
    master.close()
