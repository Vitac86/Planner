from tests.series_adoption_testkit import build_env


def test_service_creates_one_local_plan_without_network_or_queues(tmp_path):
    env = build_env(tmp_path)
    try:
        env.gateway.reset_call_counts()
        first = env.service.create_adoption_plan(env.remote_id)
        second = env.service.create_adoption_plan(env.remote_id)
        assert first.ok and second.ok
        assert first.record.id == second.record.id
        assert first.record.reserved_series_uid == second.record.reserved_series_uid
        assert env.gateway.list_call_count == 0
        assert env.gateway.write_call_count == 0
        assert env.series.list_all() == []
        assert env.series_store.list_links() == []
        assert env.series_store.list_ops() == []
        assert env.ordinary_store.count_pending_ops() == 0
        assert env.occurrence_store.count_pending_ops() == 0
    finally:
        env.close()


def test_service_returns_structured_blocked_reasons(tmp_path):
    env = build_env(tmp_path, extras={"attendees": [{"email": "x"}]})
    try:
        result = env.service.create_adoption_plan(env.remote_id)
        assert not result.ok
        assert result.eligibility is not None
        assert "attendees" in {
            item.code for item in result.eligibility.blocked_reasons
        }
    finally:
        env.close()


def test_pending_plan_cancels_locally_with_zero_google_calls(tmp_path):
    env = build_env(tmp_path)
    try:
        plan = env.service.create_adoption_plan(env.remote_id).record
        env.gateway.reset_call_counts()
        cancelled = env.service.cancel_unstarted_adoption(plan.id)
        assert cancelled.ok and cancelled.changed
        assert cancelled.record.state.value == "rolled_back"
        assert env.gateway.list_call_count == 0
        assert env.gateway.write_call_count == 0
    finally:
        env.close()
