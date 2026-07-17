import re

from planner_desktop.domain.series_calendar_link import (
    deterministic_remote_event_id,
)
from planner_desktop.domain.series_conflict_resolution import (
    deterministic_remote_event_id_for_generation,
    next_link_generation_proposal,
)
from planner_desktop.sync.sync_types import RetryableGatewayError
from tests.test_desktop_series_conflict_service import make_stack


def test_generation_id_formula_is_deterministic_and_prefixed():
    for generation in range(4):
        first = deterministic_remote_event_id_for_generation("abc", generation)
        second = deterministic_remote_event_id_for_generation("abc", generation)
        assert first == second
        assert re.fullmatch(r"plr[0-9a-v]+", first)
    assert deterministic_remote_event_id_for_generation("abc", 0) == (
        deterministic_remote_event_id("abc")
    )
    ids = {
        deterministic_remote_event_id_for_generation("abc", generation)
        for generation in range(6)
    }
    assert len(ids) == 6


def test_generation_ids_differ_between_series():
    assert deterministic_remote_event_id_for_generation("a", 1) != (
        deterministic_remote_event_id_for_generation("b", 1)
    )


def test_max_generation_drives_next_proposal(tmp_path):
    stack = make_stack(tmp_path)
    assert stack.store.max_link_generation("s1") == 0
    proposal = next_link_generation_proposal(
        "s1", [stack.store.max_link_generation("s1")]
    )
    assert proposal.generation == 1
    stack.store.close(); stack.ordinary.close()


def test_recreate_retries_reuse_the_same_generation_and_id(tmp_path):
    stack = make_stack(tmp_path)
    stack.gateway.delete_recurring_master(stack.remote_id)
    stack.pull.pull_remote_changes()
    result = stack.conflicts.recover_remote_deleted_recreate("s1", confirmed=True)
    assert result.ok
    expected_id = deterministic_remote_event_id_for_generation("s1", 1)

    stack.gateway.fail_next(RetryableGatewayError("временный сбой"))
    push = stack.engine.push_pending()
    assert push.created == 0
    op = stack.store.list_ops()[0]
    assert op.remote_event_id == expected_id
    assert op.attempts == 1
    # A duplicate button press during the retry window changes nothing.
    again = stack.conflicts.recover_remote_deleted_recreate("s1", confirmed=True)
    assert again.ok and not again.changed
    assert stack.store.max_link_generation("s1") == 1

    stack.store._connection.execute(
        "UPDATE pending_calendar_series_ops SET next_try_at = "
        "'2000-01-01T00:00:00+00:00'"
    )
    stack.store._connection.commit()
    retry = stack.engine.push_pending()
    assert retry.created == 1
    link = stack.store.get_link("s1")
    assert link.remote_event_id == expected_id
    assert link.link_status.value == "synced"
    stack.store.close(); stack.ordinary.close()


def test_second_deletion_creates_generation_two(tmp_path):
    stack = make_stack(tmp_path)
    stack.gateway.delete_recurring_master(stack.remote_id)
    stack.pull.pull_remote_changes()
    assert stack.conflicts.recover_remote_deleted_recreate("s1", confirmed=True).ok
    assert stack.engine.push_pending().created == 1
    gen1_id = stack.store.get_link("s1").remote_event_id

    stack.gateway.delete_recurring_master(gen1_id)
    stack.pull.pull_remote_changes()
    assert stack.store.get_link("s1").link_status.value == "remote_deleted"
    assert stack.conflicts.recover_remote_deleted_recreate("s1", confirmed=True).ok
    link = stack.store.get_link("s1")
    assert link.link_generation == 2
    assert link.remote_event_id == (
        deterministic_remote_event_id_for_generation("s1", 2)
    )
    assert link.remote_event_id not in (stack.remote_id, gen1_id)
    # All generations remain queryable; only one link is active.
    generations = sorted(
        item.link_generation
        for item in stack.store.list_links(include_detached=True)
    )
    assert generations == [0, 1, 2]
    active = [
        item for item in stack.store.list_links(include_detached=True)
        if item.link_status.value != "detached"
    ]
    assert len(active) == 1 and active[0].link_generation == 2
    stack.store.close(); stack.ordinary.close()
