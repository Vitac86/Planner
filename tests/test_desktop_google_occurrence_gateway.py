from copy import deepcopy

import pytest

from planner_desktop.sync.google_calendar_gateway import GoogleCalendarGateway
from planner_desktop.sync.sync_types import (
    RemoteOccurrenceConflictError,
    TerminalGatewayError,
)


class Request:
    def __init__(self, fn):
        self.fn = fn
        self.headers = {}

    def execute(self):
        return self.fn()


class Events:
    def __init__(self, item):
        self.item = deepcopy(item)
        self.instance_calls = []
        self.update_calls = []

    def instances(self, **params):
        self.instance_calls.append(params)
        return Request(lambda: {"items": [deepcopy(self.item)]})

    def get(self, calendarId, eventId):
        return Request(lambda: deepcopy(self.item))

    def update(self, calendarId, eventId, body):
        request = Request(lambda: self._update(body))
        self.update_calls.append((eventId, deepcopy(body), request))
        return request

    def _update(self, body):
        self.item = deepcopy(body)
        self.item["etag"] = '"2"'
        self.item["updated"] = "2026-07-20T10:00:00Z"
        return deepcopy(self.item)


class Service:
    def __init__(self, item):
        self.resource = Events(item)

    def events(self):
        return self.resource


def instance():
    return {
        "id": "instance-1",
        "etag": '"1"',
        "summary": "Remote",
        "description": "",
        "start": {
            "dateTime": "2026-07-20T09:00:00+03:00",
            "timeZone": "Europe/Moscow",
        },
        "end": {
            "dateTime": "2026-07-20T09:30:00+03:00",
            "timeZone": "Europe/Moscow",
        },
        "status": "confirmed",
        "recurringEventId": "master-1",
        "originalStartTime": {
            "dateTime": "2026-07-20T09:00:00+03:00",
            "timeZone": "Europe/Moscow",
        },
        "attendees": [{"email": "keep@example.test"}],
        "extendedProperties": {"private": {"foreign": "keep"}},
    }


def test_exact_instance_lookup_and_full_resource_update():
    service = Service(instance())
    gateway = GoogleCalendarGateway(service)
    matches = gateway.list_recurring_instances(
        "master-1",
        {
            "dateTime": "2026-07-20T06:00:00Z",
            "timeZone": "Europe/Moscow",
        },
    )
    assert [item["id"] for item in matches] == ["instance-1"]
    complete = gateway.get_recurring_instance("instance-1")
    complete["summary"] = "Planner"
    complete["recurrence"] = ["RRULE:FREQ=DAILY"]
    updated = gateway.update_recurring_instance(
        "instance-1", complete, '"1"'
    )
    _, sent, request = service.resource.update_calls[-1]
    assert sent["attendees"] == [{"email": "keep@example.test"}]
    assert sent["extendedProperties"]["private"]["foreign"] == "keep"
    assert "recurrence" not in sent
    assert request.headers["If-Match"] == '"1"'
    assert updated["summary"] == "Planner"


def test_cancel_is_full_update_and_already_cancelled_is_idempotent():
    service = Service(instance())
    gateway = GoogleCalendarGateway(service)
    complete = gateway.get_recurring_instance("instance-1")
    cancelled = gateway.cancel_recurring_instance(
        "instance-1", complete, '"1"'
    )
    assert cancelled["status"] == "cancelled"
    calls = len(service.resource.update_calls)
    assert gateway.cancel_recurring_instance(
        "instance-1", cancelled, '"2"'
    )["status"] == "cancelled"
    assert len(service.resource.update_calls) == calls


def test_etag_parent_and_original_start_mismatch_never_write():
    service = Service(instance())
    gateway = GoogleCalendarGateway(service)
    complete = gateway.get_recurring_instance("instance-1")
    with pytest.raises(RemoteOccurrenceConflictError):
        gateway.update_recurring_instance("instance-1", complete, '"stale"')
    assert service.resource.update_calls == []

    wrong_parent = deepcopy(complete)
    wrong_parent["recurringEventId"] = "other-master"
    with pytest.raises(TerminalGatewayError, match="parent"):
        gateway.update_recurring_instance("instance-1", wrong_parent, '"1"')

    wrong_slot = deepcopy(complete)
    wrong_slot["originalStartTime"]["dateTime"] = (
        "2026-07-21T09:00:00+03:00"
    )
    with pytest.raises(TerminalGatewayError, match="originalStartTime"):
        gateway.update_recurring_instance("instance-1", wrong_slot, '"1"')
    assert service.resource.update_calls == []
