import pytest

from planner_desktop.storage.calendar_series_occurrence_sync_store import (
    CalendarSeriesOccurrenceSyncStore,
)
from tests.test_desktop_occurrence_sync_engine import make_engine_stack


class FailFirstFinalizeStore(CalendarSeriesOccurrenceSyncStore):
    fail_once = True

    def finalize_success(self, *args, **kwargs):
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("local occurrence persistence failed")
        return super().finalize_success(*args, **kwargs)


def test_remote_success_local_failure_reconciles_without_second_write(tmp_path):
    (
        series, key, _task, desired, master, store, gateway, engine
    ) = make_engine_stack(tmp_path, FailFirstFinalizeStore)
    store.enqueue_update(series.uid, key, desired)
    with pytest.raises(RuntimeError, match="local occurrence persistence"):
        engine.push_pending()
    assert gateway.write_call_count == 1
    assert store.get_pending_op(series.uid, key) is not None

    result = engine.push_pending()
    assert result.updates_pushed == 1
    assert result.reconciled == 1
    assert gateway.write_call_count == 1
    assert store.get_pending_op(series.uid, key) is None
    store.close()
    master.close()


def test_stale_markers_alone_do_not_fake_reconciliation(tmp_path):
    (
        series, key, _task, desired, master, store, gateway, engine
    ) = make_engine_stack(tmp_path)
    remote = gateway.get_recurring_instance("instance-1")
    remote["extendedProperties"] = desired["extendedProperties"]
    remote["summary"] = "Foreign edit after marker"
    remote["etag"] = '"2"'
    gateway.seed_recurring_instance(remote)
    gateway.reset_call_counts()
    store.enqueue_update(series.uid, key, desired)
    result = engine.push_pending()
    assert result.updates_pushed == 1
    assert result.reconciled == 0
    assert gateway.write_call_count == 1
    assert gateway.get_recurring_instance("instance-1")["summary"] == (
        desired["summary"]
    )
    store.close()
    master.close()
