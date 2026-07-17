import json

import pytest

from tests.test_desktop_series_conflict_service import make_conflict, make_stack


def test_pull_refreshes_snapshot_while_conflict_unresolved(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    first = stack.store.get_link("s1")
    stack.gateway.patch_event(stack.remote_id, {"summary": "Third version"})
    stack.pull.pull_remote_changes()
    link = stack.store.get_link("s1")
    assert link.link_status.value == "conflict"
    assert link.conflict_remote_etag != first.conflict_remote_etag
    snapshot = json.loads(link.conflict_remote_snapshot_json)
    assert snapshot["summary"] == "Third version"
    # Local series untouched; no automatic UPDATE queued.
    assert stack.series_repo.get_by_uid("s1").title == "Local authoritative"
    assert stack.store.count_pending_ops() == 0
    stack.store.close(); stack.ordinary.close()


def test_pull_supersedes_acknowledged_resolution_attempt(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    resolved = stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    assert stack.store.count_pending_ops() == 1
    stack.gateway.patch_event(stack.remote_id, {"summary": "Newer remote"})
    stack.pull.pull_remote_changes()
    # The acknowledged decision became stale: op removed, audit superseded.
    assert stack.store.count_pending_ops() == 0
    audit = stack.store.get_resolution(resolved.resolution.id)
    assert audit.status == "superseded"
    link = stack.store.get_link("s1")
    assert link.link_status.value == "conflict"
    assert "Newer remote" in link.conflict_remote_snapshot_json
    stack.store.close(); stack.ordinary.close()


def test_reappeared_remote_deleted_master_is_not_relinked(tmp_path):
    stack = make_stack(tmp_path)
    stack.gateway.delete_recurring_master(stack.remote_id)
    stack.pull.pull_remote_changes()
    assert stack.store.get_link("s1").link_status.value == "remote_deleted"
    # The master unexpectedly reappears at the OLD id (e.g. restored in the
    # Google UI).  No automatic relink is allowed.
    dead = stack.gateway._events[stack.remote_id]
    dead.status = "confirmed"
    stack.gateway.patch_event(stack.remote_id, {"summary": "Restored remotely"})
    stack.pull.pull_remote_changes()
    link = stack.store.get_link("s1")
    assert link.link_status.value == "remote_deleted"
    assert link.conflict_reason == "remote_reappeared"
    assert "Restored remotely" in (link.conflict_remote_snapshot_json or "")
    assert "проверьте" in link.last_error
    # Catalog keeps the diagnostic copy; no ops were queued.
    catalog_row = stack.catalog.get("google", "primary", stack.remote_id)
    assert catalog_row.title == "Restored remotely"
    assert stack.store.count_pending_ops() == 0
    data = stack.conflicts.get_remote_deleted("s1")
    assert data["reappeared"] is True
    stack.store.close(); stack.ordinary.close()


def test_cursor_not_advanced_when_conflict_persistence_fails(
    tmp_path, monkeypatch
):
    stack = make_stack(tmp_path)
    cursor_before = stack.ordinary.get_sync_cursor()
    stack.gateway.patch_event(stack.remote_id, {"summary": "Changed"})

    def crash(*args, **kwargs):
        raise RuntimeError("db unavailable")

    monkeypatch.setattr(stack.store, "record_conflict", crash)
    with pytest.raises(RuntimeError):
        stack.pull.pull_remote_changes()
    # The pull cursor did not advance, so the change replays safely.
    assert stack.ordinary.get_sync_cursor() == cursor_before
    monkeypatch.undo()
    stack.pull.pull_remote_changes()
    assert stack.store.get_link("s1").link_status.value == "conflict"
    stack.store.close(); stack.ordinary.close()


def test_conflict_never_self_heals_from_pull_echo(tmp_path):
    """After a superseded push stored the newest remote etag as the conflict
    base, the next pull sees an etag+stale-marker match.  That echo must not
    silently clear the conflict — only an explicit resolution may."""
    stack = make_stack(tmp_path)
    make_conflict(stack)
    stack.conflicts.resolve_keep_planner("s1", confirmed=True)
    stack.gateway.patch_event(stack.remote_id, {"summary": "Changed again"})
    result = stack.engine.push_pending()
    assert result.resolution_superseded == 1
    # Full manual-cycle order: push (above) then pull sees the same event.
    stack.pull.pull_remote_changes()
    link = stack.store.get_link("s1")
    assert link.link_status.value == "conflict"
    stack.pull.pull_remote_changes()
    assert stack.store.get_link("s1").link_status.value == "conflict"
    stack.store.close(); stack.ordinary.close()


def test_pull_never_overwrites_local_series_during_conflict(tmp_path):
    stack = make_stack(tmp_path)
    make_conflict(stack)
    for round_number in range(3):
        stack.gateway.patch_event(
            stack.remote_id, {"summary": f"Round {round_number}"}
        )
        stack.pull.pull_remote_changes()
    series = stack.series_repo.get_by_uid("s1")
    assert series.title == "Local authoritative"
    assert series.revision == 1
    assert stack.store.count_pending_ops() == 0
    stack.store.close(); stack.ordinary.close()
