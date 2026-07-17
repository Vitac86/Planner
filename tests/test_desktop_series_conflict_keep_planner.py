import pytest

from planner_desktop.sync.sync_types import (
    RetryableGatewayError,
    TerminalGatewayError,
)
from tests.test_desktop_series_conflict_service import make_conflict, make_stack


def test_keep_planner_success_overwrites_remote_and_clears_conflict(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    resolved = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    assert resolved.ok
    result = stack.engine.push_pending()
    assert result.resolved_keep_planner == 1
    assert result.updated == 1
    assert result.conflicts == 0
    remote = stack.gateway.get_recurring_master(stack.remote_id)
    assert remote.summary == "Local authoritative"
    link = stack.store.get_link("s1")
    assert link.link_status.value == "synced"
    assert link.conflict_remote_etag is None
    assert link.conflict_remote_snapshot_json is None
    assert link.resolution_kind == "keep_planner"
    assert link.remote_etag == remote.etag
    audit = stack.store.get_resolution(resolved.resolution.id)
    assert audit.status == "completed"
    assert audit.remote_etag_after == remote.etag
    assert stack.store.count_pending_ops() == 0
    stack.store.close(); stack.ordinary.close()


def test_second_remote_edit_prevents_overwrite_and_supersedes(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    resolved = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    # Race: another remote edit lands before the manual sync runs.
    stack.gateway.patch_event(stack.remote_id, {"summary": "Changed again"})
    newest_etag = stack.gateway.get_recurring_master(stack.remote_id).etag
    result = stack.engine.push_pending()
    assert result.resolution_superseded == 1
    assert result.resolved_keep_planner == 0
    # No patch happened: remote keeps its newest foreign edit.
    remote = stack.gateway.get_recurring_master(stack.remote_id)
    assert remote.summary == "Changed again"
    link = stack.store.get_link("s1")
    assert link.link_status.value == "conflict"
    # The stored conflict base now points at the NEWEST remote state.
    assert link.conflict_remote_etag == newest_etag
    assert "Changed again" in (link.conflict_remote_snapshot_json or "")
    audit = stack.store.get_resolution(resolved.resolution.id)
    assert audit.status == "superseded"
    assert stack.store.count_pending_ops() == 0
    # A new explicit decision is possible against the refreshed base.
    again = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    assert again.ok and again.resolution.id != resolved.resolution.id
    final = stack.engine.push_pending()
    assert final.resolved_keep_planner == 1
    assert stack.gateway.get_recurring_master(
        stack.remote_id
    ).summary == "Local authoritative"
    stack.store.close(); stack.ordinary.close()


def test_ownership_mismatch_never_patches_foreign_master(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    resolved = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    # The remote master is replaced by a foreign owner between decision and push.
    stack.gateway._events[stack.remote_id].private_extended_properties[
        "planner_series_uid"
    ] = "someone-else"
    writes_before = stack.gateway.write_call_count
    result = stack.engine.push_pending()
    assert result.terminal == 1
    assert result.resolution_failed == 1
    assert stack.gateway.write_call_count == writes_before
    audit = stack.store.get_resolution(resolved.resolution.id)
    assert audit.status == "failed"
    # Terminal error remains visible in the dead-letter queue.
    assert stack.store.count_terminal_ops() == 1
    stack.store.close(); stack.ordinary.close()


def test_master_deleted_before_overwrite_fails_resolution(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    resolved = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    stack.gateway.delete_recurring_master(stack.remote_id)
    result = stack.engine.push_pending()
    assert result.resolution_failed == 1
    assert stack.store.get_link("s1").link_status.value == "remote_deleted"
    assert stack.store.get_resolution(resolved.resolution.id).status == "failed"
    assert stack.store.count_pending_ops() == 0
    stack.store.close(); stack.ordinary.close()


def test_remote_success_local_failure_reconciles_without_second_patch(
    tmp_path, monkeypatch
):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    resolved = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    original = stack.store.complete_conflict_resolution_link

    def crash_once(*args, **kwargs):
        monkeypatch.setattr(
            stack.store, "complete_conflict_resolution_link", original
        )
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(
        stack.store, "complete_conflict_resolution_link", crash_once
    )
    with pytest.raises(RuntimeError):
        stack.engine.push_pending()
    # The remote write happened; the queue row and audit survive for replay.
    assert stack.gateway.get_recurring_master(
        stack.remote_id
    ).summary == "Local authoritative"
    assert stack.store.count_pending_ops() == 1
    assert stack.store.get_resolution(resolved.resolution.id).is_pending

    writes_before = stack.gateway.write_call_count
    result = stack.engine.push_pending()
    assert result.resolved_keep_planner == 1
    assert result.items[0].reconciled is True
    # Deterministic reconciliation: no second patch was issued.
    assert stack.gateway.write_call_count == writes_before
    assert stack.store.get_link("s1").link_status.value == "synced"
    assert stack.store.get_resolution(resolved.resolution.id).status == "completed"
    assert stack.store.count_pending_ops() == 0
    stack.store.close(); stack.ordinary.close()


def test_local_edit_after_decision_updates_payload_keeps_base(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    resolved = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    acknowledged = stack.store.get_pending_op("s1").acknowledged_remote_etag
    # A later local edit while the decision waits for manual sync.
    series = stack.series_repo.get_by_uid("s1")
    from planner_desktop.domain.recurrence import replace_series
    edited = replace_series(
        series, title="Edited after decision", revision=series.revision + 1
    )
    stack.series_repo.update(edited)
    from planner_desktop.sync.calendar_series_mapper import (
        master_event_to_owned_payload,
        master_payload_hash,
        series_to_master_event,
    )
    event = series_to_master_event(edited)
    assert stack.store.enqueue_update(
        "s1",
        desired_revision=edited.revision,
        desired_payload_hash=master_payload_hash(event),
        payload=master_event_to_owned_payload(event),
    )
    op = stack.store.get_pending_op("s1")
    assert op.resolution_id == resolved.resolution.id
    assert op.acknowledged_remote_etag == acknowledged
    result = stack.engine.push_pending()
    assert result.resolved_keep_planner == 1
    assert stack.gateway.get_recurring_master(
        stack.remote_id
    ).summary == "Edited after decision"
    stack.store.close(); stack.ordinary.close()


def test_retry_exhaustion_leaves_visible_failed_resolution(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    resolved = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    for _ in range(20):
        stack.gateway.fail_next(RetryableGatewayError("временно недоступно"))
    # Exhaust bounded retries deterministically.
    from planner_desktop.storage.calendar_sync_store import MAX_ATTEMPTS
    for _ in range(MAX_ATTEMPTS + 1):
        op = stack.store.get_pending_op("s1")
        if op is None:
            break
        stack.store._connection.execute(
            "UPDATE pending_calendar_series_ops SET next_try_at = "
            "'2000-01-01T00:00:00+00:00' WHERE id = ?", (op.id,)
        )
        stack.store._connection.commit()
        stack.engine.push_pending()
    assert stack.store.count_terminal_ops() == 1
    assert stack.store.get_resolution(resolved.resolution.id).status == "failed"
    stack.store.close(); stack.ordinary.close()


def test_terminal_gateway_error_fails_resolution(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    resolved = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    stack.gateway.fail_next(TerminalGatewayError("постоянная ошибка"))
    result = stack.engine.push_pending()
    assert result.terminal == 1
    assert result.resolution_failed == 1
    assert stack.store.get_resolution(resolved.resolution.id).status == "failed"
    stack.store.close(); stack.ordinary.close()
