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


def test_engine_claims_and_atomically_finalizes_same_external_id(tmp_path):
    env = build_env(tmp_path)
    try:
        plan = env.service.create_adoption_plan(env.remote_id).record
        before = env.gateway.get_recurring_master_resource(env.remote_id)
        result = _engine(env).process_active()
        completed = env.adoption_store.get_plan(plan.id)
        series = env.series.get_by_uid(plan.reserved_series_uid)
        link = env.series_store.get_link(plan.reserved_series_uid)
        after = env.gateway.get_recurring_master_resource(env.remote_id)
        assert result.adoptions_started == 1
        assert result.ownership_claims_written == 1
        assert result.adoptions_finalized == 1
        assert completed.state.value == "completed"
        assert series is not None and series.tags == ()
        assert link.remote_event_id == env.remote_id
        assert link.link_origin == "adopted_external"
        assert link.remote_id_strategy == "external_existing"
        assert link.link_generation == 0
        assert len(env.tasks.list_by_series(series.uid)) == 3
        assert all(
            task.google_calendar_event_id is None
            for task in env.tasks.list_by_series(series.uid)
        )
        assert env.ordinary_store.count_pending_ops() == 0
        assert env.series_store.count_pending_ops() == 0
        assert env.occurrence_store.count_pending_ops() == 0
        for key in ("summary", "description", "start", "end", "recurrence"):
            assert after[key] == before[key]
    finally:
        env.close()


def test_unchanged_retry_does_not_patch_remote_again(tmp_path):
    env = build_env(tmp_path)
    try:
        env.service.create_adoption_plan(env.remote_id)
        _engine(env).process_active()
        env.gateway.reset_call_counts()
        result = _engine(env).process_active()
        assert result.ownership_claims_written == 0
        assert env.gateway.write_call_count == 0
        assert env.gateway.claim_call_count == 0
    finally:
        env.close()


def test_etag_race_marks_conflict_with_zero_claim_write(tmp_path):
    env = build_env(tmp_path)
    try:
        plan = env.service.create_adoption_plan(env.remote_id).record
        env.gateway.patch_event(env.remote_id, {"summary": "changed remotely"})
        env.gateway.reset_call_counts()
        result = _engine(env).process_active()
        assert result.conflicts == 1
        assert env.adoption_store.get_plan(plan.id).state.value == "conflict"
        assert env.gateway.claim_call_count == 0
        assert env.gateway.write_call_count == 0
        assert env.series.get_by_uid(plan.reserved_series_uid) is None
    finally:
        env.close()
