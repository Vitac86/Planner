"""Pure mapping for Planner-owned Google recurring-instance exceptions."""
from __future__ import annotations

from copy import deepcopy
from datetime import date, datetime, timedelta
from typing import Any, Mapping, Optional
from zoneinfo import ZoneInfo

from planner_desktop.domain.google_occurrence import (
    GoogleOccurrenceIdentity,
    OccurrenceValidationResult,
    canonical_occurrence_payload_fingerprint,
    planner_occurrence_private_properties,
)
from planner_desktop.domain.recurrence import TaskSeries, resolve_wall_clock
from planner_desktop.domain.task import Task


UNSUPPORTED_KIND_CHANGE = (
    "Нельзя преобразовать один экземпляр между событием на весь день и "
    "событием со временем в Phase 3.2B3B."
)
UNSUPPORTED_MULTI_DAY_TIMED = (
    "Многодневные исключения со временем отложены до Phase 3.2B3C."
)


def _timed_google_value(value: datetime, timezone_name: str) -> str:
    if value.tzinfo is None:
        aware = resolve_wall_clock(value, timezone_name)
    else:
        aware = value.astimezone(ZoneInfo(timezone_name))
    return aware.isoformat()


def task_to_occurrence_owned_payload(
    task: Task, series: TaskSeries
) -> dict[str, Any]:
    """Map only Planner-owned remote fields of one materialized occurrence."""
    if task.series_uid != series.uid or not task.occurrence_key:
        raise ValueError("Task is not an occurrence of the supplied series")
    if task.start is None:
        raise ValueError("an occurrence exception must remain scheduled")
    if bool(task.is_all_day) != bool(series.schedule.all_day):
        raise ValueError(UNSUPPORTED_KIND_CHANGE)

    if task.is_all_day:
        start_day = task.start.date()
        end_day = task.end.date() if task.end is not None else start_day + timedelta(days=1)
        if end_day <= start_day:
            end_day = start_day + timedelta(days=1)
        start = {"date": start_day.isoformat()}
        end = {"date": end_day.isoformat()}
    else:
        end_value = task.end
        if end_value is None:
            minutes = task.duration_minutes or series.schedule.duration_minutes or 60
            end_value = task.start + timedelta(minutes=minutes)
        if end_value <= task.start:
            raise ValueError("occurrence end must be after start")
        if end_value.date() != task.start.date():
            raise ValueError(UNSUPPORTED_MULTI_DAY_TIMED)
        timezone_name = series.schedule.timezone_name
        start = {
            "dateTime": _timed_google_value(task.start, timezone_name),
            "timeZone": timezone_name,
        }
        end = {
            "dateTime": _timed_google_value(end_value, timezone_name),
            "timeZone": timezone_name,
        }

    return {
        "summary": task.title,
        "description": task.notes,
        "start": start,
        "end": end,
        "status": "confirmed",
    }


def build_desired_occurrence_payload(
    task: Task, series: TaskSeries, link_generation: int
) -> dict[str, Any]:
    owned = task_to_occurrence_owned_payload(task, series)
    payload_hash = canonical_occurrence_payload_fingerprint(owned)
    result = deepcopy(owned)
    result["extendedProperties"] = {
        "private": planner_occurrence_private_properties(
            series.uid,
            str(task.occurrence_key),
            link_generation,
            payload_hash,
        )
    }
    return result


def merge_complete_instance_payload(
    complete_instance: Mapping[str, Any],
    desired_payload: Mapping[str, Any],
) -> dict[str, Any]:
    """Merge Planner-owned fields into a complete instance resource.

    Provider fields such as attendees, reminders, conference data, location,
    visibility and shared extended properties are retained.  ``recurrence`` is
    never legal on an individual instance and is always removed.
    """
    merged = deepcopy(dict(complete_instance))
    merged.pop("recurrence", None)
    for name in ("summary", "description", "start", "end", "status"):
        if name in desired_payload:
            merged[name] = deepcopy(desired_payload[name])
    current_extended = deepcopy(merged.get("extendedProperties") or {})
    current_private = deepcopy(current_extended.get("private") or {})
    desired_private = (
        (desired_payload.get("extendedProperties") or {}).get("private") or {}
    )
    current_private.update(
        {str(key): str(value) for key, value in desired_private.items()}
    )
    current_extended["private"] = current_private
    merged["extendedProperties"] = current_extended
    return merged


def original_start_from_payload(
    payload: Mapping[str, Any],
) -> GoogleOccurrenceIdentity:
    original = payload.get("originalStartTime") or {}
    if original.get("date"):
        if original.get("dateTime") or original.get("timeZone"):
            raise ValueError("invalid all-day originalStartTime")
        return GoogleOccurrenceIdentity("date", str(original["date"]))
    if original.get("dateTime"):
        return GoogleOccurrenceIdentity(
            "datetime",
            str(original["dateTime"]),
            str(original.get("timeZone") or ""),
        )
    raise ValueError("instance has no originalStartTime")


def validate_remote_occurrence_payload(
    payload: Mapping[str, Any],
    *,
    expected_master_event_id: str,
    expected_original_start: GoogleOccurrenceIdentity,
) -> OccurrenceValidationResult:
    errors: list[str] = []
    if str(payload.get("recurringEventId") or "") != expected_master_event_id:
        errors.append("remote instance belongs to a different recurring master")
    try:
        actual = original_start_from_payload(payload)
    except ValueError as exc:
        errors.append(str(exc))
    else:
        if actual != expected_original_start:
            errors.append("remote instance originalStartTime does not match the slot")
    if payload.get("recurrence"):
        errors.append("individual recurring instance unexpectedly contains recurrence")
    return OccurrenceValidationResult(tuple(errors))


def remote_payload_to_local_schedule(
    payload: Mapping[str, Any],
    series: TaskSeries,
) -> tuple[datetime, datetime, bool]:
    """Parse a supported same-kind remote exception into local Task fields."""
    start_raw = payload.get("start") or {}
    end_raw = payload.get("end") or {}
    if series.schedule.all_day:
        if not start_raw.get("date") or start_raw.get("dateTime"):
            raise ValueError(UNSUPPORTED_KIND_CHANGE)
        start_day = date.fromisoformat(str(start_raw["date"]))
        end_day = (
            date.fromisoformat(str(end_raw["date"]))
            if end_raw.get("date")
            else start_day + timedelta(days=1)
        )
        return (
            datetime.combine(start_day, datetime.min.time()),
            datetime.combine(end_day, datetime.min.time()),
            True,
        )
    if not start_raw.get("dateTime") or start_raw.get("date"):
        raise ValueError(UNSUPPORTED_KIND_CHANGE)
    if start_raw.get("timeZone") != series.schedule.timezone_name:
        raise ValueError("remote exception timezone does not match the series")
    if end_raw.get("timeZone") != series.schedule.timezone_name:
        raise ValueError("remote exception end timezone does not match the series")
    zone = ZoneInfo(series.schedule.timezone_name)
    start = datetime.fromisoformat(
        str(start_raw["dateTime"]).replace("Z", "+00:00")
    ).astimezone(zone).replace(tzinfo=None)
    end = datetime.fromisoformat(
        str(end_raw["dateTime"]).replace("Z", "+00:00")
    ).astimezone(zone).replace(tzinfo=None)
    if end.date() != start.date():
        raise ValueError(UNSUPPORTED_MULTI_DAY_TIMED)
    return start, end, False


def canonical_remote_occurrence_hash(payload: Mapping[str, Any]) -> str:
    return canonical_occurrence_payload_fingerprint(payload)


# Alternate names retained for discoverability in focused mapper tests.
task_to_instance_payload = build_desired_occurrence_payload
merge_instance_payload = merge_complete_instance_payload


__all__ = [
    "UNSUPPORTED_KIND_CHANGE",
    "UNSUPPORTED_MULTI_DAY_TIMED",
    "build_desired_occurrence_payload",
    "canonical_remote_occurrence_hash",
    "merge_complete_instance_payload",
    "merge_instance_payload",
    "original_start_from_payload",
    "remote_payload_to_local_schedule",
    "task_to_instance_payload",
    "task_to_occurrence_owned_payload",
    "validate_remote_occurrence_payload",
]
