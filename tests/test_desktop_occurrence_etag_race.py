from planner_desktop.domain.google_occurrence import OccurrenceSyncStatus
from tests.test_desktop_occurrence_sync_engine import make_engine_stack


def test_second_remote_edit_supersedes_keep_planner_decision(tmp_path):
    (
        series, key, _task, desired, master, store, gateway, engine
    ) = make_engine_stack(tmp_path)
    store.enqueue_update(
        series.uid,
        key,
        desired,
        acknowledged_remote_etag='"1"',
    )
    remote = gateway.get_recurring_instance("instance-1")
    remote["summary"] = "Second remote edit"
    gateway.update_recurring_instance("instance-1", remote, '"1"')
    gateway.reset_call_counts()

    result = engine.push_pending()
    assert result.conflicts_detected == 1
    assert gateway.write_call_count == 0
    assert store.get_pending_op(series.uid, key) is None
    change = store.list_occurrence_changes(unresolved_only=True)[0]
    assert change.remote_etag == '"2"'
    assert change.payload["summary"] == "Second remote edit"
    assert store.get_occurrence_link(
        series.uid, key
    ).sync_status is OccurrenceSyncStatus.REMOTE_CHANGED
    store.close()
    master.close()
