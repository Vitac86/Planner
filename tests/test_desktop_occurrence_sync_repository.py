from planner_desktop.domain.google_occurrence import (
    OccurrenceSyncStatus,
    local_occurrence_to_google_original_start,
)
from tests.occurrence_sync_testkit import linked_occurrence_store, timed_series


def test_occurrence_link_and_queue_survive_reopen(tmp_path):
    db = tmp_path / "desktop.db"
    series = timed_series()
    master_store, store, link = linked_occurrence_store(db, series)
    key = "2026-07-20T09:00@Europe/Moscow"
    identity = local_occurrence_to_google_original_start(series, key)
    stored = store.ensure_occurrence_link(series.uid, key, link, identity)
    assert stored.sync_status is OccurrenceSyncStatus.LOCAL_ONLY
    store.enqueue_update(
        series.uid,
        key,
        {
            "summary": "changed",
            "description": "",
            "start": identity.to_google(),
            "end": {
                "dateTime": "2026-07-20T09:30:00+03:00",
                "timeZone": "Europe/Moscow",
            },
            "status": "confirmed",
        },
    )
    store.close()
    master_store.close()

    from planner_desktop.storage.calendar_series_occurrence_sync_store import (
        CalendarSeriesOccurrenceSyncStore,
    )

    reopened = CalendarSeriesOccurrenceSyncStore(db)
    assert reopened.get_occurrence_link(series.uid, key).identity == identity
    assert reopened.get_pending_op(series.uid, key).payload["summary"] == "changed"
    reopened.close()


def test_one_active_occurrence_link_per_generation(tmp_path):
    series = timed_series()
    master_store, store, link = linked_occurrence_store(
        tmp_path / "desktop.db", series
    )
    key = "2026-07-20T09:00@Europe/Moscow"
    identity = local_occurrence_to_google_original_start(series, key)
    first = store.ensure_occurrence_link(series.uid, key, link, identity)
    second = store.ensure_occurrence_link(series.uid, key, link, identity)
    assert first.id == second.id
    assert len(store.list_occurrence_links(series_uid=series.uid)) == 1
    store.close()
    master_store.close()
