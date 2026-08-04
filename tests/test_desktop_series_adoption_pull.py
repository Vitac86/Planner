import pytest

from planner_desktop.domain.series_adoption import (
    planner_adoption_private_properties,
)
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from planner_desktop.usecases.external_series_service import ExternalSeriesService
from tests.series_adoption_testkit import build_env


def _engine(env):
    return CalendarSyncEngine(
        env.tasks,
        env.ordinary_store,
        env.gateway,
        env.catalog,
        series_link_store=env.series_store,
        occurrence_sync_store=env.occurrence_store,
        series_repository=env.series,
        split_store=env.split_store,
        adoption_store=env.adoption_store,
    )


def test_pending_plan_keeps_external_row_and_creates_no_local_series(tmp_path):
    env = build_env(tmp_path)
    try:
        plan = env.service.create_adoption_plan(env.remote_id).record
        env.gateway._record_change(env.gateway._events[env.remote_id])
        _engine(env).pull_remote_changes()
        row = env.catalog.get("google", "primary", env.remote_id)
        assert row is not None
        assert row.adoption_state == "pending"
        assert env.series.get_by_uid(plan.reserved_series_uid) is None
        assert env.tasks.get_by_google_event_id(env.remote_id) is None
    finally:
        env.close()


def test_orphan_planner_marker_is_diagnostic_only(tmp_path):
    env = build_env(tmp_path)
    try:
        remote = env.gateway._events[env.remote_id]
        remote.private_extended_properties.update(
            planner_adoption_private_properties(
                adoption_id=999,
                series_uid="orphan-series",
                revision=1,
                payload_hash="orphan-hash",
            )
        )
        env.gateway._bump_etag(remote)
        env.gateway._record_change(remote)
        _engine(env).pull_remote_changes()
        row = ExternalSeriesService(env.catalog).rows()[0]
        assert row["orphanOwnershipMarker"]
        assert row["ownershipText"] == "Возможная незавершённая связь Planner"
        assert env.series.get_by_uid("orphan-series") is None
        assert env.tasks.get_by_google_event_id(env.remote_id) is None
    finally:
        env.close()


def test_unexpected_pending_edit_conflicts_without_ordinary_task(tmp_path):
    env = build_env(tmp_path)
    try:
        plan = env.service.create_adoption_plan(env.remote_id).record
        env.gateway.patch_event(env.remote_id, {"summary": "outside edit"})
        _engine(env).pull_remote_changes()
        assert env.adoption_store.get_plan(plan.id).state.value == "conflict"
        assert env.series.get_by_uid(plan.reserved_series_uid) is None
        assert env.tasks.get_by_google_event_id(env.remote_id) is None
    finally:
        env.close()


def test_pull_cursor_does_not_advance_when_adoption_persistence_fails(
    tmp_path, monkeypatch
):
    env = build_env(tmp_path)
    try:
        env.gateway.patch_event(env.remote_id, {"summary": "new catalog value"})
        before = env.ordinary_store.get_sync_cursor()
        monkeypatch.setattr(
            env.catalog,
            "upsert",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("catalog persistence failure")
            ),
        )
        with pytest.raises(RuntimeError):
            _engine(env).pull_remote_changes()
        assert env.ordinary_store.get_sync_cursor() == before
    finally:
        env.close()
