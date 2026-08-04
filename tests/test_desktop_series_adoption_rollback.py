from planner_desktop.sync.calendar_series_adoption_engine import (
    CalendarSeriesAdoptionEngine,
)
from tests.series_adoption_testkit import build_env


def _engine(env):
    return CalendarSeriesAdoptionEngine(
        env.adoption_store,
        env.series,
        env.tasks,
        env.series_store,
        env.catalog,
        env.gateway,
    )


def _leave_claimed(env, monkeypatch):
    plan = env.service.create_adoption_plan(env.remote_id).record
    monkeypatch.setattr(
        env.adoption_store,
        "finalize_external_series_adoption_atomic",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            RuntimeError("local finalize unavailable")
        ),
    )
    _engine(env).process_active()
    return plan


def test_claimed_plan_explicit_rollback_restores_private_properties(
    tmp_path, monkeypatch
):
    env = build_env(tmp_path)
    try:
        plan = _leave_claimed(env, monkeypatch)
        assert env.service.request_adoption_rollback(plan.id).ok
        result = _engine(env).process_active()
        rolled = env.adoption_store.get_plan(plan.id)
        remote = env.gateway.get_recurring_master(env.remote_id)
        assert result.rollbacks == 1
        assert rolled.state.value == "rolled_back"
        assert remote.private_extended_properties == {"foreign_key": "keep"}
        assert env.series.get_by_uid(plan.reserved_series_uid) is None
    finally:
        env.close()


def test_rollback_etag_race_never_removes_markers(tmp_path, monkeypatch):
    env = build_env(tmp_path)
    try:
        plan = _leave_claimed(env, monkeypatch)
        assert env.service.request_adoption_rollback(plan.id).ok
        env.gateway.patch_event(env.remote_id, {"summary": "raced"})
        env.gateway.reset_call_counts()
        result = _engine(env).process_active()
        remote = env.gateway.get_recurring_master(env.remote_id)
        assert result.conflicts == 1
        assert env.adoption_store.get_plan(plan.id).state.value == "conflict"
        assert "planner_adoption_id" in remote.private_extended_properties
        assert env.gateway.release_call_count == 0
        assert env.gateway.write_call_count == 0
    finally:
        env.close()
