from planner_desktop.sync.calendar_series_adoption_engine import (
    CalendarSeriesAdoptionEngine,
)
from planner_desktop.sync.calendar_sync_engine import CalendarSyncEngine
from tests.series_adoption_testkit import build_env


def _adoption_engine(env):
    return CalendarSeriesAdoptionEngine(
        env.adoption_store,
        env.series,
        env.tasks,
        env.series_store,
        env.catalog,
        env.gateway,
    )


def _pull_engine(env):
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


def test_claim_echo_is_not_orphan_or_ordinary_task(tmp_path, monkeypatch):
    env = build_env(tmp_path)
    try:
        plan = env.service.create_adoption_plan(env.remote_id).record

        def fail(*args, **kwargs):
            raise RuntimeError("local finalize unavailable")

        monkeypatch.setattr(
            env.adoption_store,
            "finalize_external_series_adoption_atomic",
            fail,
        )
        _adoption_engine(env).process_active()
        assert env.adoption_store.get_plan(plan.id).state.value == (
            "local_finalize_pending"
        )
        pulled = _pull_engine(env).pull_remote_changes()
        refreshed = env.adoption_store.get_plan(plan.id)
        assert pulled >= 1
        assert refreshed.state.value == "local_finalize_pending"
        assert refreshed.claimed_remote_etag
        assert env.tasks.get_by_google_event_id(env.remote_id) is None
        assert env.series.get_by_uid(plan.reserved_series_uid) is None
    finally:
        env.close()


def test_unexpected_claimed_visible_edit_becomes_conflict(tmp_path, monkeypatch):
    env = build_env(tmp_path)
    try:
        plan = env.service.create_adoption_plan(env.remote_id).record
        monkeypatch.setattr(
            env.adoption_store,
            "finalize_external_series_adoption_atomic",
            lambda *args, **kwargs: (_ for _ in ()).throw(
                RuntimeError("local finalize unavailable")
            ),
        )
        _adoption_engine(env).process_active()
        env.gateway.patch_event(env.remote_id, {"summary": "outside edit"})
        _pull_engine(env).pull_remote_changes()
        assert env.adoption_store.get_plan(plan.id).state.value == "conflict"
        assert env.series.get_by_uid(plan.reserved_series_uid) is None
        assert env.tasks.get_by_google_event_id(env.remote_id) is None
    finally:
        env.close()
