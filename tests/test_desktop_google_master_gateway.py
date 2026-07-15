"""Recurring-master transport through Google and fake Calendar gateways."""
from datetime import date, datetime, timezone

from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
)
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.sync.google_calendar_gateway import (
    event_to_insert_body,
    payload_to_event,
    recurrence_to_google_lines,
    recurring_master_patch_to_body,
    recurring_master_to_insert_body,
)
from planner_desktop.sync.sync_types import CalendarEvent


def test_master_payload_preserves_recurrence_order_timezone_and_kind():
    lines = (
        "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,WE",
        "EXDATE;TZID=Europe/Berlin:20261025T090000",
    )
    event = payload_to_event({
        "id": "master-1",
        "summary": "Серия",
        "recurrence": list(lines),
        "start": {
            "dateTime": "2026-07-15T09:00:00+02:00",
            "timeZone": "Europe/Berlin",
        },
        "end": {
            "dateTime": "2026-07-15T09:30:00+02:00",
            "timeZone": "Europe/Berlin",
        },
    })
    assert event.recurrence_lines == lines
    assert event.start_timezone == "Europe/Berlin"
    assert event.end_timezone == "Europe/Berlin"
    assert event.recurrence_start == datetime(2026, 7, 15, 9, 0)
    assert event.is_recurring_master
    assert not event.is_recurring_instance
    assert not event.is_ordinary_event


def test_provider_wall_clock_uses_timezone_even_when_datetime_is_utc():
    event = payload_to_event({
        "id": "master-zone",
        "recurrence": ["RRULE:FREQ=DAILY;UNTIL=20260731T070000Z"],
        "start": {"dateTime": "2026-07-15T07:00:00Z", "timeZone": "Europe/Berlin"},
        "end": {"dateTime": "2026-07-15T07:30:00Z", "timeZone": "Europe/Berlin"},
    })
    assert event.recurrence_start == datetime(2026, 7, 15, 9, 0)


def test_ordinary_and_instance_classification_remain_disjoint():
    ordinary = payload_to_event({
        "id": "ordinary", "start": {"date": "2026-07-15"},
        "end": {"date": "2026-07-16"},
    })
    assert ordinary.is_ordinary_event

    instance = payload_to_event({
        "id": "instance", "recurringEventId": "master-1",
        "recurrence": ["RRULE:FREQ=DAILY"],  # defensive provider oddity
        "originalStartTime": {"date": "2026-07-15"},
        "start": {"date": "2026-07-15"}, "end": {"date": "2026-07-16"},
    })
    assert instance.is_recurring_instance
    assert not instance.is_recurring_master
    assert not instance.is_ordinary_event


def test_cancelled_master_with_recurrence_is_tolerated_without_times():
    event = payload_to_event({
        "id": "master-cancelled", "status": "cancelled",
        "recurrence": ["RRULE:FREQ=DAILY"],
    })
    assert event.is_cancelled and event.is_recurring_master
    assert event.start is None and event.end is None


def test_future_master_body_helpers_are_pure_and_production_insert_stays_ordinary():
    master = CalendarEvent(
        summary="Серия", start=date(2026, 7, 15), end=date(2026, 7, 16),
        is_all_day=True,
        recurrence_lines=("RRULE:FREQ=DAILY;INTERVAL=1",),
    )
    assert recurrence_to_google_lines(
        RecurrenceRule(RecurrenceFrequency.DAILY)
    ) == ("RRULE:FREQ=DAILY;INTERVAL=1",)
    assert recurring_master_to_insert_body(master)["recurrence"] == list(
        master.recurrence_lines
    )
    assert recurring_master_patch_to_body(master)["recurrence"] == list(
        master.recurrence_lines
    )
    # B1's real write body intentionally ignores recurrence metadata.
    assert "recurrence" not in event_to_insert_body(master)


def test_fake_gateway_stores_master_and_never_expands_it():
    gateway = FakeCalendarGateway(
        base_time=datetime(2026, 7, 15, tzinfo=timezone.utc)
    )
    master = gateway.insert_event(CalendarEvent(
        summary="Бесконечная серия",
        start=date(2026, 7, 15), end=date(2026, 7, 16), is_all_day=True,
        recurrence_lines=("RRULE:FREQ=DAILY;INTERVAL=1",),
    ))
    batch = gateway.list_changes(None)
    assert [item.id for item in batch.events] == [master.id]
    assert batch.events[0].is_recurring_master
    assert len(gateway.events) == 1


def test_fake_gateway_lists_changed_and_cancelled_instances_deterministically():
    gateway = FakeCalendarGateway()
    master = gateway.insert_event(CalendarEvent(
        summary="Серия", start=date(2026, 7, 15), end=date(2026, 7, 16),
        is_all_day=True, recurrence_lines=("RRULE:FREQ=DAILY",),
    ))
    changed = gateway.insert_event(CalendarEvent(
        summary="Изменённый экземпляр", start=date(2026, 7, 16),
        end=date(2026, 7, 17), is_all_day=True,
        recurring_event_id=master.id,
        original_start=datetime(2026, 7, 16),
    ))
    cancelled = gateway.insert_event(CalendarEvent(
        summary="Отменённый экземпляр", start=date(2026, 7, 17),
        end=date(2026, 7, 18), is_all_day=True,
        recurring_event_id=master.id,
        original_start=datetime(2026, 7, 17),
    ))
    cursor = gateway.list_changes(None).next_cursor
    gateway.patch_event(changed.id, {"summary": "Перенесённый текст"})
    gateway.delete_event(cancelled.id)
    batch = gateway.list_changes(cursor)
    assert [item.id for item in batch.events] == [changed.id, cancelled.id]
    assert batch.events[1].is_cancelled
