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


def test_atomic_finalize_rolls_back_every_local_row_on_failure(tmp_path, monkeypatch):
    env = build_env(tmp_path)
    try:
        plan = env.service.create_adoption_plan(env.remote_id).record
        original = env.adoption_store.finalize_external_series_adoption_atomic
        calls = {"count": 0}

        def fail_once(*args, **kwargs):
            calls["count"] += 1
            if calls["count"] == 1:
                raise RuntimeError("injected local persistence failure")
            return original(*args, **kwargs)

        monkeypatch.setattr(
            env.adoption_store,
            "finalize_external_series_adoption_atomic",
            fail_once,
        )
        first = _engine(env).process_active()
        pending = env.adoption_store.get_plan(plan.id)
        assert pending.state.value == "local_finalize_pending"
        assert env.series.get_by_uid(plan.reserved_series_uid) is None
        assert env.series_store.get_link(plan.reserved_series_uid) is None
        assert env.gateway.claim_call_count == 1

        env.gateway.reset_call_counts()
        second = _engine(env).process_active()
        completed = env.adoption_store.get_plan(plan.id)
        assert completed.state.value == "completed"
        assert second.reconciliations == 1
        assert env.gateway.claim_call_count == 0
        assert env.gateway.write_call_count == 0
        assert env.series.get_by_uid(plan.reserved_series_uid) is not None
        assert len(env.series_store.list_links()) == 1
    finally:
        env.close()


def test_finalize_never_creates_duplicate_taskseries(tmp_path):
    env = build_env(tmp_path)
    try:
        plan = env.service.create_adoption_plan(env.remote_id).record
        _engine(env).process_active()
        _engine(env).process_active()
        assert [item.uid for item in env.series.list_all()] == [
            plan.reserved_series_uid
        ]
        assert len(env.series_store.list_links()) == 1
    finally:
        env.close()
