from __future__ import annotations

from datetime import date, datetime, time

from planner_desktop.domain.google_occurrence import (
    local_occurrence_to_google_original_start,
)
from planner_desktop.domain.recurrence import (
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
)
from planner_desktop.domain.series_calendar_link import (
    PLANNER_SERIES_UID_PROPERTY,
    SeriesCalendarLink,
    SeriesLinkStatus,
)
from planner_desktop.storage.calendar_series_occurrence_sync_store import (
    CalendarSeriesOccurrenceSyncStore,
)
from planner_desktop.storage.calendar_series_sync_store import (
    CalendarSeriesSyncStore,
)
from planner_desktop.sync.fake_calendar_gateway import FakeCalendarGateway
from planner_desktop.sync.sync_types import CalendarEvent


def timed_series(uid: str = "series-1") -> TaskSeries:
    return TaskSeries(
        uid=uid,
        title="Daily focus",
        notes="Planner series",
        schedule=SeriesSchedule(
            start_date=date(2026, 7, 20),
            all_day=False,
            local_time=time(9),
            duration_minutes=30,
            timezone_name="Europe/Moscow",
        ),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )


def all_day_series(uid: str = "series-day") -> TaskSeries:
    return TaskSeries(
        uid=uid,
        title="Daily all-day",
        schedule=SeriesSchedule(
            start_date=date(2026, 7, 20),
            all_day=True,
            timezone_name="Europe/Moscow",
        ),
        rule=RecurrenceRule(RecurrenceFrequency.DAILY),
    )


def linked_occurrence_store(db_path, series: TaskSeries, master_id="master-1"):
    master_store = CalendarSeriesSyncStore(db_path)
    link = master_store.create_pending_link(
        SeriesCalendarLink(
            series_uid=series.uid,
            remote_event_id=master_id,
        ),
        desired_revision=series.revision,
        desired_payload_hash="master-hash",
        payload={"summary": series.title},
    )
    op = master_store.get_pending_op(series.uid)
    master_store.remove_op(op.id)
    master_store.set_link_status(
        series.uid,
        SeriesLinkStatus.SYNCED,
        remote_etag='"1"',
        synced_revision=series.revision,
        synced_payload_hash="master-hash",
    )
    link = master_store.get_link(series.uid)
    occurrence_store = CalendarSeriesOccurrenceSyncStore(db_path)
    return master_store, occurrence_store, link


def owned_gateway_with_instance(
    series: TaskSeries,
    occurrence_key: str,
    *,
    master_id: str = "master-1",
    instance_id: str = "instance-1",
) -> FakeCalendarGateway:
    gateway = FakeCalendarGateway()
    gateway.insert_recurring_master(
        master_id,
        CalendarEvent(
            summary=series.title,
            description=series.notes,
            start=datetime(2026, 7, 20, 9),
            end=datetime(2026, 7, 20, 9, 30),
            recurrence_lines=("RRULE:FREQ=DAILY",),
            private_extended_properties={
                PLANNER_SERIES_UID_PROPERTY: series.uid,
            },
        ),
    )
    identity = local_occurrence_to_google_original_start(
        series, occurrence_key
    )
    start = datetime(2026, 7, 20, 9)
    gateway.seed_recurring_instance(
        {
            "id": instance_id,
            "etag": '"1"',
            "summary": series.title,
            "description": series.notes,
            "start": {
                "dateTime": start.replace(
                    tzinfo=datetime.fromisoformat(identity.value).tzinfo
                ).isoformat(),
                "timeZone": series.schedule.timezone_name,
            },
            "end": {
                "dateTime": start.replace(
                    tzinfo=datetime.fromisoformat(identity.value).tzinfo
                ).replace(minute=30).isoformat(),
                "timeZone": series.schedule.timezone_name,
            },
            "status": "confirmed",
            "recurringEventId": master_id,
            "originalStartTime": identity.to_google(),
            "extendedProperties": {
                "private": {},
                "shared": {"external": "preserve"},
            },
            "attendees": [{"email": "kept@example.test"}],
            "location": "Keep this",
        }
    )
    gateway.reset_call_counts()
    return gateway
