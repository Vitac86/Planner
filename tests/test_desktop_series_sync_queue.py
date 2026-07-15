from planner_desktop.domain.series_calendar_link import (
    SeriesCalendarLink,
    SeriesLinkStatus,
)
from planner_desktop.storage.calendar_series_sync_store import CalendarSeriesSyncStore


def _connect(store, uid, remote):
    return store.create_pending_link(
        SeriesCalendarLink(series_uid=uid, remote_event_id=remote),
        desired_revision=1,
        desired_payload_hash="h1",
        payload={"summary": "one"},
    )


def test_create_update_and_duplicate_create_coalesce(tmp_path):
    store = CalendarSeriesSyncStore(tmp_path / "desktop.db")
    first = _connect(store, "s1", "plr00001")
    duplicate = _connect(store, "s1", "plr00001")
    assert first.id == duplicate.id
    assert len(store.list_ops()) == 1
    assert store.enqueue_update(
        "s1", desired_revision=2, desired_payload_hash="h2",
        payload={"summary": "latest"},
    )
    op = store.get_pending_op("s1")
    assert op.op.value == "create"
    assert op.desired_revision == 2 and op.desired_payload_hash == "h2"
    assert op.payload["summary"] == "latest"
    store.close()


def test_create_delete_cancels_without_a_remote_operation(tmp_path):
    store = CalendarSeriesSyncStore(tmp_path / "desktop.db")
    _connect(store, "s1", "plr00001")
    assert store.enqueue_delete("s1") == "cancelled_create"
    assert store.get_pending_op("s1") is None
    assert store.get_link("s1") is None
    assert store.get_link("s1", include_detached=True).link_status.value == "detached"
    store.close()


def test_update_update_and_update_delete_coalesce(tmp_path):
    store = CalendarSeriesSyncStore(tmp_path / "desktop.db")
    _connect(store, "s1", "plr00001")
    create = store.get_pending_op("s1")
    store.remove_op(create.id)
    store.set_link_status(
        "s1", SeriesLinkStatus.SYNCED,
        synced_revision=1, synced_payload_hash="h1",
    )
    assert store.enqueue_update(
        "s1", desired_revision=2, desired_payload_hash="h2", payload={"summary": "two"}
    )
    assert store.enqueue_update(
        "s1", desired_revision=3, desired_payload_hash="h3", payload={"summary": "three"}
    )
    op = store.get_pending_op("s1")
    assert op.op.value == "update" and op.desired_revision == 3
    assert store.enqueue_delete("s1") == "queued"
    op = store.get_pending_op("s1")
    assert op.op.value == "delete" and op.desired_payload_hash is None
    assert not store.enqueue_update(
        "s1", desired_revision=4, desired_payload_hash="h4", payload={"summary": "four"}
    )
    store.close()
