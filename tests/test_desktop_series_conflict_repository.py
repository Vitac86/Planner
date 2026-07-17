from planner_desktop.domain.series_calendar_link import (
    SeriesCalendarLink,
    SeriesLinkStatus,
    SeriesSyncOpKind,
)
from planner_desktop.domain.series_conflict_resolution import (
    ConflictResolutionStatus,
    SeriesConflictResolution,
)
from planner_desktop.storage.calendar_series_sync_store import (
    CalendarSeriesSyncStore,
)


def _store_with_link(tmp_path, status=SeriesLinkStatus.SYNCED):
    store = CalendarSeriesSyncStore(tmp_path / "desktop.db")
    link = store.create_pending_link(
        SeriesCalendarLink(series_uid="s1", remote_event_id="plrabc"),
        desired_revision=1,
        desired_payload_hash="h0",
        payload={"summary": "Local"},
    )
    if status is not SeriesLinkStatus.PENDING_CREATE:
        store.remove_op(store.get_pending_op("s1").id)
        store.set_link_status("s1", status, remote_etag='"1"')
    return store, store.get_link("s1")


def test_record_conflict_persists_base_and_supersedes_pending_intent(tmp_path):
    store, _ = _store_with_link(tmp_path)
    resolution = store.add_resolution(SeriesConflictResolution(
        series_uid="s1", link_id=1, resolution_kind="keep_planner",
        local_revision_before=1, acknowledged_remote_etag='"1"',
    ))
    assert store.enqueue_update(
        "s1", desired_revision=2, desired_payload_hash="h2",
        payload={"summary": "Edited"},
    )
    stored = store.record_conflict(
        "s1",
        reason="изменён вне Planner",
        remote_etag='"9"',
        remote_payload_hash="rh",
        remote_snapshot_json='{"etag":"\\"9\\""}',
    )
    assert stored.link_status is SeriesLinkStatus.CONFLICT
    assert stored.conflict_remote_etag == '"9"'
    assert stored.conflict_remote_payload_hash == "rh"
    assert stored.conflict_remote_snapshot_json == '{"etag":"\\"9\\""}'
    assert stored.conflict_detected_at is not None
    assert stored.resolved_at is None
    # Pending automatic overwrite removed; stale explicit intent superseded.
    assert store.get_pending_op("s1") is None
    assert store.get_resolution(resolution.id).status == "superseded"


def test_conflict_resolution_update_is_single_and_refreshable(tmp_path):
    store, _ = _store_with_link(tmp_path)
    store.record_conflict("s1", reason="conflict", remote_etag='"9"')
    resolution = store.add_resolution(SeriesConflictResolution(
        series_uid="s1", link_id=1, resolution_kind="keep_planner",
        local_revision_before=1, acknowledged_remote_etag='"9"',
    ))
    assert store.enqueue_conflict_resolution_update(
        "s1", desired_revision=1, desired_payload_hash="h1",
        payload={"summary": "Local"}, acknowledged_remote_etag='"9"',
        resolution_id=resolution.id,
    )
    # Duplicate request refreshes the same queue row.
    assert store.enqueue_conflict_resolution_update(
        "s1", desired_revision=1, desired_payload_hash="h1",
        payload={"summary": "Local"}, acknowledged_remote_etag='"9"',
        resolution_id=resolution.id,
    )
    ops = store.list_ops()
    assert len(ops) == 1
    op = ops[0]
    assert op.op is SeriesSyncOpKind.UPDATE
    assert op.resolution_id == resolution.id
    assert op.acknowledged_remote_etag == '"9"'
    # The link deliberately STAYS in conflict until the write succeeds.
    assert store.get_link("s1").link_status is SeriesLinkStatus.CONFLICT
    assert store.get_link("s1").resolution_kind == "keep_planner"


def test_ordinary_enqueue_update_respects_conflict_resolution(tmp_path):
    store, _ = _store_with_link(tmp_path)
    store.record_conflict("s1", reason="conflict", remote_etag='"9"')
    # Without a chosen resolution an automatic UPDATE is refused.
    assert not store.enqueue_update(
        "s1", desired_revision=2, desired_payload_hash="h2",
        payload={"summary": "Edited"},
    )
    resolution = store.add_resolution(SeriesConflictResolution(
        series_uid="s1", link_id=1, resolution_kind="keep_planner",
        local_revision_before=1, acknowledged_remote_etag='"9"',
    ))
    store.enqueue_conflict_resolution_update(
        "s1", desired_revision=1, desired_payload_hash="h1",
        payload={"summary": "Local"}, acknowledged_remote_etag='"9"',
        resolution_id=resolution.id,
    )
    # A later local edit refreshes the desired payload but keeps the
    # acknowledged conflict base and audit id.
    assert store.enqueue_update(
        "s1", desired_revision=2, desired_payload_hash="h2",
        payload={"summary": "Edited"},
    )
    op = store.get_pending_op("s1")
    assert op.desired_payload_hash == "h2"
    assert op.desired_revision == 2
    assert op.resolution_id == resolution.id
    assert op.acknowledged_remote_etag == '"9"'
    assert store.get_link("s1").link_status is SeriesLinkStatus.CONFLICT


def test_explicit_delete_supersedes_resolution_update(tmp_path):
    store, _ = _store_with_link(tmp_path)
    store.record_conflict("s1", reason="conflict", remote_etag='"9"')
    resolution = store.add_resolution(SeriesConflictResolution(
        series_uid="s1", link_id=1, resolution_kind="keep_planner",
        local_revision_before=1, acknowledged_remote_etag='"9"',
    ))
    store.enqueue_conflict_resolution_update(
        "s1", desired_revision=1, desired_payload_hash="h1",
        payload={"summary": "Local"}, acknowledged_remote_etag='"9"',
        resolution_id=resolution.id,
    )
    assert store.enqueue_delete("s1") == "queued"
    op = store.get_pending_op("s1")
    assert op.op is SeriesSyncOpKind.DELETE
    assert op.resolution_id is None
    assert op.acknowledged_remote_etag is None
    assert store.get_resolution(resolution.id).status == "superseded"


def test_complete_conflict_resolution_link_clears_base(tmp_path):
    store, _ = _store_with_link(tmp_path)
    store.record_conflict(
        "s1", reason="conflict", remote_etag='"9"',
        remote_payload_hash="rh", remote_snapshot_json="{}",
    )
    link = store.complete_conflict_resolution_link(
        "s1", remote_etag='"10"', remote_updated_at=None,
        synced_revision=4, synced_payload_hash="h4",
        resolution_kind="keep_planner",
    )
    assert link.link_status is SeriesLinkStatus.SYNCED
    assert link.conflict_remote_etag is None
    assert link.conflict_remote_payload_hash is None
    assert link.conflict_remote_snapshot_json is None
    assert link.conflict_detected_at is None
    assert link.resolved_at is not None
    assert link.resolution_kind == "keep_planner"
    assert link.last_synced_series_revision == 4


def test_detach_link_resolved_preserves_history_and_cancels_ops(tmp_path):
    store, _ = _store_with_link(tmp_path)
    store.record_conflict(
        "s1", reason="conflict", remote_etag='"9"',
        remote_snapshot_json='{"etag":1}',
    )
    resolution = store.add_resolution(SeriesConflictResolution(
        series_uid="s1", link_id=1, resolution_kind="keep_planner",
        local_revision_before=1,
    ))
    store.enqueue_conflict_resolution_update(
        "s1", desired_revision=1, desired_payload_hash="h1",
        payload={}, acknowledged_remote_etag='"9"',
        resolution_id=resolution.id,
    )
    assert store.detach_link_resolved("s1", resolution_kind="disconnect")
    link = store.get_link("s1", include_detached=True)
    assert link.link_status is SeriesLinkStatus.DETACHED
    assert link.resolution_kind == "disconnect"
    assert link.resolved_at is not None
    # Conflict history stays queryable on the detached row.
    assert link.conflict_remote_snapshot_json == '{"etag":1}'
    assert store.get_pending_op("s1") is None
    assert store.get_resolution(resolution.id).status == "superseded"


def test_mark_remote_deleted_supersedes_pending_intent(tmp_path):
    store, _ = _store_with_link(tmp_path)
    store.record_conflict("s1", reason="conflict", remote_etag='"9"')
    resolution = store.add_resolution(SeriesConflictResolution(
        series_uid="s1", link_id=1, resolution_kind="keep_planner",
        local_revision_before=1,
    ))
    store.enqueue_conflict_resolution_update(
        "s1", desired_revision=1, desired_payload_hash="h1",
        payload={}, acknowledged_remote_etag='"9"',
        resolution_id=resolution.id,
    )
    link = store.mark_remote_deleted("s1", error="удалён")
    assert link.link_status is SeriesLinkStatus.REMOTE_DELETED
    assert store.get_pending_op("s1") is None
    assert store.get_resolution(resolution.id).status == "superseded"


def test_resolution_audit_lifecycle_and_counts(tmp_path):
    store = CalendarSeriesSyncStore(tmp_path / "desktop.db")
    first = store.add_resolution(SeriesConflictResolution(
        series_uid="s1", link_id=1, resolution_kind="use_google",
        local_revision_before=1,
    ))
    second = store.add_resolution(SeriesConflictResolution(
        series_uid="s2", link_id=2, resolution_kind="recreate",
        local_revision_before=2,
    ))
    third = store.add_resolution(SeriesConflictResolution(
        series_uid="s3", link_id=3, resolution_kind="keep_planner",
        local_revision_before=1,
    ))
    store.complete_resolution(first.id, local_revision_after=2)
    store.fail_resolution(second.id, "boom")
    store.supersede_resolution(third.id, "changed again")
    counts = store.count_resolutions_by_status()
    assert counts["completed"] == 1
    assert counts["failed"] == 1
    assert counts["superseded"] == 1
    assert counts["pending"] == 0
    assert store.get_pending_resolution("s1") is None
    history = store.list_resolutions()
    assert [item.series_uid for item in history] == ["s3", "s2", "s1"]
    assert store.count_resolutions_completed_after(None, ("use_google",)) == 1
    diag = store.diagnostics()
    assert diag["resolutions_failed"] == 1
    assert diag["resolutions_pending"] == 0
    store.close()


def test_pending_resolution_filter_by_kind(tmp_path):
    store = CalendarSeriesSyncStore(tmp_path / "desktop.db")
    store.add_resolution(SeriesConflictResolution(
        series_uid="s1", link_id=1, resolution_kind="keep_planner",
        local_revision_before=1,
        status=ConflictResolutionStatus.PENDING.value,
    ))
    assert store.get_pending_resolution("s1", kind="keep_planner") is not None
    assert store.get_pending_resolution("s1", kind="use_google") is None
    store.close()
