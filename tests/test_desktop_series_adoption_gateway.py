from copy import deepcopy

import pytest

from planner_desktop.domain.series_adoption import (
    PLANNER_ADOPTION_ID_PROPERTY,
    PLANNER_LINK_ORIGIN_PROPERTY,
    planner_adoption_private_properties,
)
from planner_desktop.sync.sync_types import (
    RemoteMasterConflictError,
    TerminalGatewayError,
)
from tests.series_adoption_testkit import build_env


def _visible(resource):
    value = deepcopy(resource)
    value.pop("etag", None)
    value.pop("updated", None)
    value.pop("extendedProperties", None)
    return value


def _markers():
    return planner_adoption_private_properties(
        adoption_id=7,
        series_uid="series-uid",
        revision=1,
        payload_hash="payload",
    )


def test_claim_changes_only_private_properties_and_preserves_unrelated(tmp_path):
    env = build_env(
        tmp_path,
        extras={
            "location": "Room 42",
            "reminders": {"useDefault": False, "overrides": []},
            "colorId": "5",
        },
    )
    try:
        before = env.gateway.get_recurring_master_resource(env.remote_id)
        merged = {"foreign_key": "keep", **_markers()}
        claimed = env.gateway.claim_external_recurring_master(
            env.remote_id, merged, before["etag"]
        )
        after = env.gateway.get_recurring_master_resource(env.remote_id)
        assert _visible(after) == _visible(before)
        assert after["extendedProperties"]["private"]["foreign_key"] == "keep"
        assert after["extendedProperties"]["private"][PLANNER_ADOPTION_ID_PROPERTY] == "7"
        assert claimed.etag != before["etag"]
    finally:
        env.close()


def test_etag_mismatch_and_foreign_ownership_perform_zero_claim_write(tmp_path):
    env = build_env(tmp_path)
    try:
        before = env.gateway.get_recurring_master(env.remote_id)
        env.gateway.patch_event(env.remote_id, {"summary": "raced"})
        env.gateway.reset_call_counts()
        with pytest.raises(RemoteMasterConflictError):
            env.gateway.claim_external_recurring_master(
                env.remote_id, _markers(), before.etag
            )
        assert env.gateway.write_call_count == 0
    finally:
        env.close()

    foreign = build_env(
        tmp_path / "foreign",
        private={"planner_series_uid": "someone-else"},
    )
    try:
        current = foreign.gateway.get_recurring_master(foreign.remote_id)
        foreign.gateway.reset_call_counts()
        with pytest.raises(TerminalGatewayError):
            foreign.gateway.claim_external_recurring_master(
                foreign.remote_id, _markers(), current.etag
            )
        assert foreign.gateway.write_call_count == 0
    finally:
        foreign.close()


def test_remote_deletion_and_organizer_access_validation(tmp_path):
    env = build_env(tmp_path)
    try:
        current = env.gateway.get_recurring_master(env.remote_id)
        env.gateway.delete_recurring_master(env.remote_id)
        env.gateway.reset_call_counts()
        with pytest.raises(RemoteMasterConflictError):
            env.gateway.claim_external_recurring_master(
                env.remote_id, _markers(), current.etag
            )
        assert env.gateway.write_call_count == 0
    finally:
        env.close()

    reader = build_env(tmp_path / "reader", access_role="reader")
    try:
        current = reader.gateway.get_recurring_master(reader.remote_id)
        with pytest.raises(TerminalGatewayError):
            reader.gateway.claim_external_recurring_master(
                reader.remote_id, _markers(), current.etag
            )
    finally:
        reader.close()

    guest = build_env(
        tmp_path / "guest", extras={"organizer": {"self": False}}
    )
    try:
        current = guest.gateway.get_recurring_master(guest.remote_id)
        with pytest.raises(TerminalGatewayError):
            guest.gateway.claim_external_recurring_master(
                guest.remote_id, _markers(), current.etag
            )
    finally:
        guest.close()


def test_release_removes_only_planner_markers(tmp_path):
    env = build_env(tmp_path)
    try:
        current = env.gateway.get_recurring_master(env.remote_id)
        claimed = env.gateway.claim_external_recurring_master(
            env.remote_id,
            {"foreign_key": "keep", **_markers()},
            current.etag,
        )
        # A new unrelated property is preserved by release.
        env.gateway._events[env.remote_id].private_extended_properties[
            "added_later"
        ] = "also-keep"
        released = env.gateway.release_adopted_recurring_master(
            env.remote_id,
            {"foreign_key": "keep"},
            claimed.etag,
        )
        assert released.private_extended_properties == {
            "foreign_key": "keep",
            "added_later": "also-keep",
        }
    finally:
        env.close()
