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


def test_release_removes_markers_detaches_link_and_keeps_local_history(tmp_path):
    env = build_env(tmp_path)
    try:
        plan = env.service.create_adoption_plan(env.remote_id).record
        _engine(env).process_active()
        before_tasks = [task.uid for task in env.tasks.list_by_series(
            plan.reserved_series_uid
        )]
        assert env.service.request_release_adopted_series(
            plan.reserved_series_uid
        ).ok
        result = _engine(env).process_active()
        released = env.adoption_store.get_plan(plan.id)
        link = env.series_store.get_link(
            plan.reserved_series_uid, include_detached=True
        )
        catalog = env.catalog.get("google", "primary", env.remote_id)
        remote = env.gateway.get_recurring_master(env.remote_id)
        assert result.releases == 1
        assert released.state.value == "released"
        assert link.link_status.value == "detached"
        assert env.series.get_by_uid(plan.reserved_series_uid) is not None
        assert [task.uid for task in env.tasks.list_by_series(
            plan.reserved_series_uid
        )] == before_tasks
        assert not catalog.planner_owned
        assert catalog.adopted_series_uid is None
        assert remote.private_extended_properties == {"foreign_key": "keep"}
    finally:
        env.close()


def test_release_etag_race_preserves_link_and_markers(tmp_path):
    env = build_env(tmp_path)
    try:
        plan = env.service.create_adoption_plan(env.remote_id).record
        _engine(env).process_active()
        assert env.service.request_release_adopted_series(
            plan.reserved_series_uid
        ).ok
        env.gateway.patch_event(env.remote_id, {"summary": "external race"})
        env.gateway.reset_call_counts()
        result = _engine(env).process_active()
        link = env.series_store.get_link(plan.reserved_series_uid)
        remote = env.gateway.get_recurring_master(env.remote_id)
        assert result.conflicts == 1
        assert env.adoption_store.get_plan(plan.id).state.value == (
            "release_conflict"
        )
        assert link is not None and link.link_status.value == "synced"
        assert "planner_adoption_id" in remote.private_extended_properties
        assert env.gateway.release_call_count == 1
        assert env.gateway.write_call_count == 0
    finally:
        env.close()
