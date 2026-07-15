"""Pure TaskSeries <-> Planner-owned recurring-master mapping."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Mapping
from zoneinfo import ZoneInfo

from planner_desktop.domain.google_recurrence import recurrence_to_google_lines
from planner_desktop.domain.recurrence import (
    DEFAULT_OCCURRENCE_DURATION_MINUTES,
    TaskSeries,
)
from planner_desktop.domain.series_calendar_link import (
    canonical_master_payload_fingerprint,
    planner_private_properties,
)
from planner_desktop.sync.sync_types import CalendarEvent


def _timed_value(value: datetime, timezone_name: str) -> datetime:
    zone = ZoneInfo(timezone_name)
    if value.tzinfo is None:
        return value.replace(tzinfo=zone)
    return value.astimezone(zone)


def master_event_to_owned_payload(event: CalendarEvent) -> dict[str, Any]:
    """Planner-owned Google fields, excluding unrelated remote metadata."""
    if not event.is_recurring_master:
        raise ValueError("CalendarEvent is not a recurring master.")
    if event.start is None or event.end is None:
        raise ValueError("Recurring master requires start and end.")

    if event.is_all_day:
        start_day = (
            event.start
            if isinstance(event.start, date) and not isinstance(event.start, datetime)
            else event.start.date()
        )
        end_day = (
            event.end
            if isinstance(event.end, date) and not isinstance(event.end, datetime)
            else event.end.date()
        )
        if end_day <= start_day:
            end_day = start_day + timedelta(days=1)
        start_body: dict[str, Any] = {"date": start_day.isoformat()}
        end_body: dict[str, Any] = {"date": end_day.isoformat()}
    else:
        if not isinstance(event.start, datetime) or not isinstance(event.end, datetime):
            raise ValueError("Timed recurring master requires datetime bounds.")
        timezone_name = event.start_timezone or event.end_timezone
        if not timezone_name:
            raise ValueError("Timed recurring master requires an IANA timezone.")
        start = _timed_value(event.start, timezone_name)
        end = _timed_value(event.end, timezone_name)
        start_body = {
            "dateTime": start.isoformat(),
            "timeZone": timezone_name,
        }
        end_body = {
            "dateTime": end.isoformat(),
            "timeZone": timezone_name,
        }

    body: dict[str, Any] = {
        "summary": event.summary or "",
        "description": event.description or "",
        "start": start_body,
        "end": end_body,
        "recurrence": list(event.recurrence_lines),
    }
    if event.private_extended_properties:
        body["extendedProperties"] = {
            "private": dict(event.private_extended_properties)
        }
    return body


def series_to_master_event(series: TaskSeries) -> CalendarEvent:
    schedule = series.schedule
    lines = recurrence_to_google_lines(series.rule, schedule=schedule)
    if schedule.all_day:
        start: date | datetime = schedule.start_date
        end: date | datetime = schedule.start_date + timedelta(days=1)
    else:
        if schedule.local_time is None:
            raise ValueError("У серии со временем не задано время начала.")
        minutes = schedule.duration_minutes or DEFAULT_OCCURRENCE_DURATION_MINUTES
        start = datetime.combine(schedule.start_date, schedule.local_time)
        end = start + timedelta(minutes=minutes)

    event = CalendarEvent(
        summary=series.title,
        description=series.notes,
        start=start,
        end=end,
        is_all_day=schedule.all_day,
        recurrence_lines=lines,
        start_timezone=(None if schedule.all_day else schedule.timezone_name),
        end_timezone=(None if schedule.all_day else schedule.timezone_name),
        recurrence_start=start,
    )
    payload_hash = canonical_master_payload_fingerprint(
        master_event_to_owned_payload(event)
    )
    event.private_extended_properties = planner_private_properties(
        series.uid, series.revision, payload_hash
    )
    return event


def master_payload_hash(event: CalendarEvent) -> str:
    return canonical_master_payload_fingerprint(master_event_to_owned_payload(event))


def private_properties_from_payload(payload: Mapping[str, Any]) -> dict[str, str]:
    extended = payload.get("extendedProperties") or {}
    private = extended.get("private") or {}
    return {str(key): str(value) for key, value in private.items()}


__all__ = [
    "master_event_to_owned_payload",
    "master_payload_hash",
    "private_properties_from_payload",
    "series_to_master_event",
]
