"""Full-resource master split gateway operations (Part 6)."""
from __future__ import annotations

import pytest

from planner_desktop.domain.google_series_split import series_master_payload
from planner_desktop.domain.series_calendar_link import (
    PLANNER_SERIES_UID_PROPERTY,
    canonical_master_payload_fingerprint,
)
from planner_desktop.sync.google_calendar_gateway import (
    GoogleCalendarGateway,
    split_resource_content_matches,
)
from planner_desktop.sync.sync_types import (
    RemoteMasterConflictError,
    TerminalGatewayError,
)
from tests.remote_split_testkit import (
    build_env,
    link_series,
    make_series,
    plan_split,
)


def test_fake_full_update_requires_matching_etag_and_owner(tmp_path):
    env = build_env(tmp_path)
    link = link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    payload = record.trimmed_source_payload

    with pytest.raises(RemoteMasterConflictError):
        env.gateway.update_recurring_master_full(
            link.remote_event_id, payload, expected_etag='"stale"'
        )

    foreign = dict(payload)
    foreign["extendedProperties"] = {
        "private": {PLANNER_SERIES_UID_PROPERTY: "someone-else"}
    }
    with pytest.raises(TerminalGatewayError):
        env.gateway.update_recurring_master_full(
            link.remote_event_id, foreign, expected_etag=link.remote_etag
        )

    written = env.gateway.update_recurring_master_full(
        link.remote_event_id, payload, expected_etag=link.remote_etag
    )
    assert "COUNT=2" in written["recurrence"][0]
    assert split_resource_content_matches(written, payload)
    env.close()


def test_fake_full_update_preserves_unrelated_fields(tmp_path):
    env = build_env(tmp_path)
    link = link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    current = env.gateway.get_recurring_master_resource(link.remote_event_id)
    merged = dict(record.trimmed_source_payload)
    merged["attendees"] = [{"email": "kept@example.test"}]
    merged["location"] = "Kept location"
    written = env.gateway.update_recurring_master_full(
        link.remote_event_id, merged, expected_etag=current["etag"]
    )
    assert written["attendees"] == [{"email": "kept@example.test"}]
    assert written["location"] == "Kept location"
    env.close()


def test_fake_successor_insert_is_idempotent_by_content(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    payload = record.successor_payload

    first = env.gateway.insert_split_successor_master(
        record.successor_remote_event_id, payload
    )
    # Matching ownership AND actual canonical content -> success, no dup.
    second = env.gateway.insert_split_successor_master(
        record.successor_remote_event_id, payload
    )
    assert second["etag"] == first["etag"]
    masters = [
        event for event in env.gateway.events
        if event.is_recurring_master and not event.is_cancelled
    ]
    assert len(masters) == 2  # source + one successor, never a third
    env.close()


def test_fake_successor_collision_foreign_is_terminal(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    foreign = dict(record.successor_payload)
    foreign["extendedProperties"] = {
        "private": {PLANNER_SERIES_UID_PROPERTY: "someone-else"}
    }
    env.gateway.insert_split_successor_master(
        record.successor_remote_event_id, foreign
    )
    with pytest.raises(TerminalGatewayError):
        env.gateway.insert_split_successor_master(
            record.successor_remote_event_id, record.successor_payload
        )
    env.close()


def test_fake_successor_stale_markers_different_content_is_conflict(tmp_path):
    env = build_env(tmp_path)
    link_series(env, make_series())
    record, _ = plan_split(env, "src-1")
    altered = dict(record.successor_payload)
    altered["summary"] = "different content, same markers"
    env.gateway.insert_split_successor_master(
        record.successor_remote_event_id, altered
    )
    with pytest.raises(RemoteMasterConflictError):
        env.gateway.insert_split_successor_master(
            record.successor_remote_event_id, record.successor_payload
        )
    env.close()


# ---- real GoogleCalendarGateway over an injected fake service ---------------


class _FakeRequest:
    def __init__(self, response=None, error=None):
        self._response = response
        self._error = error
        self.headers: dict = {}

    def execute(self):
        if self._error is not None:
            raise self._error
        return self._response


class _HttpError(Exception):
    def __init__(self, status):
        super().__init__(f"HTTP {status}")
        self.status_code = status


class _FakeEvents:
    def __init__(self, owner):
        self._owner = owner

    def get(self, calendarId, eventId):
        resource = self._owner.resources.get(eventId)
        if resource is None:
            return _FakeRequest(error=_HttpError(404))
        return _FakeRequest(response=dict(resource))

    def insert(self, calendarId, body):
        event_id = body.get("id")
        if event_id in self._owner.resources:
            return _FakeRequest(error=_HttpError(409))
        stored = dict(body)
        stored["etag"] = '"1"'
        stored["status"] = "confirmed"
        self._owner.resources[event_id] = stored
        self._owner.insert_calls += 1
        return _FakeRequest(response=dict(stored))

    def update(self, calendarId, eventId, body):
        current = self._owner.resources.get(eventId)
        if current is None:
            return _FakeRequest(error=_HttpError(404))
        request = _FakeRequest()

        def execute():
            expected = request.headers.get("If-Match")
            if expected and current.get("etag") != expected:
                raise _HttpError(412)
            stored = dict(body)
            revision = int(str(current.get("etag") or '"0"').strip('"')) + 1
            stored["etag"] = f'"{revision}"'
            self._owner.resources[eventId] = stored
            self._owner.update_calls += 1
            return dict(stored)

        request.execute = execute
        return request


class _FakeService:
    def __init__(self):
        self.resources: dict = {}
        self.insert_calls = 0
        self.update_calls = 0

    def events(self):
        return _FakeEvents(self)


def _successor_payload():
    series = make_series(uid="real-succ")
    payload, payload_hash = series_master_payload(series)
    payload["extendedProperties"] = {
        "private": {PLANNER_SERIES_UID_PROPERTY: "real-succ"}
    }
    return payload, payload_hash


def test_real_gateway_insert_reconciles_deterministic_collision():
    service = _FakeService()
    gateway = GoogleCalendarGateway(service)
    payload, _ = _successor_payload()
    first = gateway.insert_split_successor_master("plrreal", payload)
    assert service.insert_calls == 1
    # Retry after remote-success/local-failure: fetch-first, no second insert.
    second = gateway.insert_split_successor_master("plrreal", payload)
    assert service.insert_calls == 1
    assert canonical_master_payload_fingerprint(second) == (
        canonical_master_payload_fingerprint(first)
    )


def test_real_gateway_conditional_full_update_uses_if_match():
    service = _FakeService()
    gateway = GoogleCalendarGateway(service)
    payload, _ = _successor_payload()
    created = gateway.insert_split_successor_master("plrreal", payload)
    changed = dict(payload)
    changed["summary"] = "trimmed"
    with pytest.raises(RemoteMasterConflictError):
        gateway.update_recurring_master_full(
            "plrreal", changed, expected_etag='"stale"'
        )
    written = gateway.update_recurring_master_full(
        "plrreal", changed, expected_etag=created["etag"]
    )
    assert written["summary"] == "trimmed"
    assert service.update_calls == 1
