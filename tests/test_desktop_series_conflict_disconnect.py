from tests.test_desktop_series_conflict_service import make_conflict, make_stack


def test_disconnect_keeps_both_sides_untouched(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    resolved = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    remote_before = stack.gateway.get_recurring_master(stack.remote_id)
    writes = stack.gateway.write_call_count
    lists = stack.gateway.list_call_count

    result = stack.conflicts.resolve_disconnect("s1")
    assert result.ok

    # The Google master is untouched: no calls, same etag/content.
    assert stack.gateway.write_call_count == writes
    assert stack.gateway.list_call_count == lists
    remote_after = stack.gateway.get_recurring_master(stack.remote_id)
    assert remote_after.etag == remote_before.etag
    assert remote_after.summary == remote_before.summary

    # The local TaskSeries is untouched and now local-only.
    series = stack.series_repo.get_by_uid("s1")
    assert series.title == "Local authoritative"
    assert series.revision == 1
    assert not series.is_deleted

    # Pending explicit resolution cancelled; link detached with history.
    assert stack.store.count_pending_ops() == 0
    link = stack.store.get_link("s1", include_detached=True)
    assert link.link_status.value == "detached"
    assert link.resolution_kind == "disconnect"
    assert link.conflict_remote_snapshot_json  # history preserved
    assert stack.store.get_link("s1") is None  # no active link remains
    assert stack.store.get_resolution(resolved.resolution.id).status == "superseded"

    # External catalog entry and audit history preserved.
    assert stack.catalog.get("google", "primary", stack.remote_id) is not None
    kinds = [item.resolution_kind for item in stack.store.list_resolutions("s1")]
    assert "disconnect" in kinds
    stack.store.close(); stack.ordinary.close()


def test_disconnect_requires_conflict_or_remote_deleted(tmp_path):
    stack = make_stack(tmp_path)
    refused = stack.conflicts.resolve_disconnect("s1")  # link is synced
    assert not refused.ok
    assert stack.store.get_link("s1").link_status.value == "synced"
    stack.store.close(); stack.ordinary.close()


def test_disconnect_works_for_remote_deleted_link(tmp_path):
    stack = make_stack(tmp_path)
    stack.gateway.delete_recurring_master(stack.remote_id)
    stack.pull.pull_remote_changes()
    result = stack.conflicts.resolve_disconnect("s1")
    assert result.ok
    link = stack.store.get_link("s1", include_detached=True)
    assert link.link_status.value == "detached"
    assert link.resolution_kind == "keep_local"
    assert not stack.series_repo.get_by_uid("s1").is_deleted
    stack.store.close(); stack.ordinary.close()


def test_reconnect_possible_after_disconnect(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    assert stack.conflicts.resolve_disconnect("s1").ok
    # The series is local-only again; B2 reconnect validation applies as-is.
    reconnect = stack.links.connect_to_google("s1")
    assert reconnect.ok
    stack.store.close(); stack.ordinary.close()
