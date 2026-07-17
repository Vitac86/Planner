from copy import deepcopy
from datetime import date, time

import pytest

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.domain.series_calendar_link import deterministic_remote_event_id
from planner_desktop.sync.calendar_series_mapper import series_to_master_event
from planner_desktop.sync.google_calendar_gateway import GoogleCalendarGateway
from planner_desktop.sync.sync_types import (
    CalendarEvent,
    RemoteMasterConflictError,
    TerminalGatewayError,
)


class HttpError(Exception):
    def __init__(self, status):
        self.resp = type("Resp", (), {"status": status})()
        super().__init__(f"HTTP {status}")


class Request:
    def __init__(self, fn):
        self.fn = fn
        self.headers = {}

    def execute(self):
        return self.fn()


class Events:
    def __init__(self):
        self.items = {}
        self.patch_bodies = []
        self.insert_bodies = []

    def insert(self, calendarId, body):
        def run():
            self.insert_bodies.append(deepcopy(body))
            event_id = body.get("id", "ordinary-1")
            if event_id in self.items:
                raise HttpError(409)
            item = deepcopy(body)
            item.update({"id": event_id, "etag": '"1"', "status": "confirmed",
                         "updated": "2026-07-15T10:00:00Z"})
            self.items[event_id] = item
            return deepcopy(item)
        return Request(run)

    def get(self, calendarId, eventId):
        def run():
            if eventId not in self.items:
                raise HttpError(404)
            return deepcopy(self.items[eventId])
        return Request(run)

    def patch(self, calendarId, eventId, body):
        def run():
            if eventId not in self.items:
                raise HttpError(404)
            self.patch_bodies.append(deepcopy(body))
            item = self.items[eventId]
            item.update(deepcopy(body))
            item["etag"] = '"2"'
            item["updated"] = "2026-07-15T11:00:00Z"
            return deepcopy(item)
        return Request(run)

    def delete(self, calendarId, eventId):
        def run():
            if eventId not in self.items:
                raise HttpError(404)
            del self.items[eventId]
            return None
        return Request(run)


class Service:
    def __init__(self):
        self.resource = Events()

    def events(self):
        return self.resource


def _master(uid="s1", title="Daily"):
    return series_to_master_event(TaskSeries(
        uid=uid,
        title=title,
        schedule=SeriesSchedule(date(2026, 7, 15), False, time(9), 30,
                                "Europe/Moscow"),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    ))


def test_insert_duplicate_reconciliation_patch_and_delete_are_explicit():
    service = Service()
    gateway = GoogleCalendarGateway(service)
    master = _master()
    remote_id = deterministic_remote_event_id("s1")
    created = gateway.insert_recurring_master(remote_id, master)
    assert created.id == remote_id and created.is_recurring_master
    assert service.resource.insert_bodies[0]["id"] == remote_id
    assert "extendedProperties" in service.resource.insert_bodies[0]

    reconciled = gateway.insert_recurring_master(remote_id, master)
    assert reconciled.id == remote_id

    updated_payload = _master(title="Changed")
    updated = gateway.patch_recurring_master(
        remote_id, updated_payload, expected_etag=created.etag
    )
    assert updated.etag == '"2"'
    assert service.resource.patch_bodies[-1]["summary"] == "Changed"

    gateway.delete_recurring_master(remote_id)
    gateway.delete_recurring_master(remote_id)  # already absent is success


def test_foreign_collision_and_unexpected_etag_are_not_overwritten():
    service = Service()
    gateway = GoogleCalendarGateway(service)
    remote_id = deterministic_remote_event_id("s1")
    service.resource.items[remote_id] = {
        "id": remote_id, "etag": '"9"', "status": "confirmed",
        "summary": "Foreign", "description": "",
        "start": {"date": "2026-07-15"}, "end": {"date": "2026-07-16"},
        "recurrence": ["RRULE:FREQ=DAILY"],
    }
    with pytest.raises(TerminalGatewayError, match="Коллизия"):
        gateway.insert_recurring_master(remote_id, _master())

    service = Service()
    gateway = GoogleCalendarGateway(service)
    created = gateway.insert_recurring_master(remote_id, _master())
    service.resource.items[remote_id]["etag"] = '"external"'
    with pytest.raises(RemoteMasterConflictError):
        gateway.patch_recurring_master(
            remote_id, _master(title="Changed"), expected_etag=created.etag
        )


def test_stale_markers_after_foreign_edit_do_not_fake_patch_success():
    """Live-pilot finding (Phase 3.2B3A): чужая правка (например, summary с
    телефона) не обновляет приватные Planner-маркеры, поэтому совпадение
    маркеров само по себе не доказывает применённую запись. Явная перезапись
    Keep Planner с подтверждённым etag обязана отправить настоящий PATCH."""
    service = Service()
    gateway = GoogleCalendarGateway(service)
    master = _master()
    remote_id = deterministic_remote_event_id("s1")
    gateway.insert_recurring_master(remote_id, master)

    service.resource.items[remote_id]["summary"] = "Foreign title"
    service.resource.items[remote_id]["etag"] = '"external"'

    written = gateway.patch_recurring_master(
        remote_id, master, expected_etag='"external"'
    )
    assert service.resource.patch_bodies, "явная перезапись должна слать PATCH"
    assert service.resource.patch_bodies[-1]["summary"] == "Daily"
    assert written.summary == "Daily"


def test_identical_content_retry_still_skips_second_patch():
    """Повтор после remote-успеха/локального сбоя: маркеры И фактическое
    содержимое совпадают — второй PATCH не отправляется."""
    service = Service()
    gateway = GoogleCalendarGateway(service)
    master = _master()
    remote_id = deterministic_remote_event_id("s1")
    created = gateway.insert_recurring_master(remote_id, master)

    reconciled = gateway.patch_recurring_master(
        remote_id, master, expected_etag=created.etag
    )
    assert not service.resource.patch_bodies
    assert reconciled.etag == created.etag


def test_ordinary_insert_behavior_still_ignores_master_contract():
    service = Service()
    gateway = GoogleCalendarGateway(service)
    event = CalendarEvent(
        summary="Ordinary", start=date(2026, 7, 15),
        end=date(2026, 7, 16), is_all_day=True,
    )
    created = gateway.insert_event(event)
    assert created.id == "ordinary-1"
    assert "recurrence" not in service.resource.insert_bodies[-1]
