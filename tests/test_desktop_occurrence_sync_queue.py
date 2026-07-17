import pytest

from planner_desktop.domain.google_occurrence import (
    OccurrenceOperationKind,
    OccurrenceSyncStatus,
    local_occurrence_to_google_original_start,
)
from tests.occurrence_sync_testkit import linked_occurrence_store, timed_series


def _stack(tmp_path):
    series = timed_series()
    master, store, link = linked_occurrence_store(
        tmp_path / "desktop.db", series
    )
    key = "2026-07-20T09:00@Europe/Moscow"
    identity = local_occurrence_to_google_original_start(series, key)
    store.ensure_occurrence_link(series.uid, key, link, identity)
    return series, key, master, store


def _payload(title):
    return {
        "summary": title,
        "description": "",
        "start": {
            "dateTime": "2026-07-20T09:00:00+03:00",
            "timeZone": "Europe/Moscow",
        },
        "end": {
            "dateTime": "2026-07-20T09:30:00+03:00",
            "timeZone": "Europe/Moscow",
        },
        "status": "confirmed",
    }


def test_update_update_coalesces_and_duplicate_is_noop(tmp_path):
    series, key, master, store = _stack(tmp_path)
    assert store.enqueue_update(series.uid, key, _payload("one"))
    first = store.get_pending_op(series.uid, key)
    assert not store.enqueue_update(series.uid, key, _payload("one"))
    assert store.enqueue_update(series.uid, key, _payload("two"))
    latest = store.get_pending_op(series.uid, key)
    assert latest.id == first.id
    assert latest.payload["summary"] == "two"
    assert store.count_pending_ops() == 1
    store.close()
    master.close()


def test_update_cancel_coalesces_and_cancel_update_requires_restore(tmp_path):
    series, key, master, store = _stack(tmp_path)
    store.enqueue_update(series.uid, key, _payload("one"))
    assert store.enqueue_cancel(series.uid, key, _payload("one"))
    op = store.get_pending_op(series.uid, key)
    assert op.op is OccurrenceOperationKind.CANCEL
    assert store.get_occurrence_link(
        series.uid, key
    ).sync_status is OccurrenceSyncStatus.PENDING_CANCEL
    with pytest.raises(ValueError, match="explicitly restored"):
        store.enqueue_update(series.uid, key, _payload("restored"))
    assert store.enqueue_update(
        series.uid,
        key,
        _payload("restored"),
        allow_cancelled_restore=True,
    )
    assert store.get_pending_op(
        series.uid, key
    ).op is OccurrenceOperationKind.UPDATE
    store.close()
    master.close()


def test_terminal_is_visible_and_only_explicit_retry_requeues(tmp_path):
    series, key, master, store = _stack(tmp_path)
    store.enqueue_update(series.uid, key, _payload("one"))
    op = store.get_pending_op(series.uid, key)
    assert store.mark_terminal(op.id, "identity failed")
    assert store.list_due_ops() == []
    assert store.count_terminal_ops() == 1
    assert store.retry_terminal_operation(op.id)
    assert store.count_terminal_ops() == 0
    assert len(store.list_due_ops()) == 1
    store.close()
    master.close()
