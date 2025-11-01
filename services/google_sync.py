"""Utility helpers shared between sync services."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, Dict, Optional, Tuple

from datetime_utils import ensure_utc, parse_rfc3339, to_rfc3339_utc


def build_event_payload(task) -> Dict[str, Any]:
    start = ensure_utc(getattr(task, "start", None))
    duration = getattr(task, "duration_minutes", None)
    if start is None or not duration:
        raise ValueError("Scheduled task must have start and duration")

    end = start + timedelta(minutes=int(duration))
    notes = (getattr(task, "notes", None) or "").strip()

    body: Dict[str, Any] = {
        "summary": getattr(task, "title", "Задача"),
        "start": {"dateTime": to_rfc3339_utc(start)},
        "end": {"dateTime": to_rfc3339_utc(end)},
    }
    if notes:
        body["description"] = notes
    return body


def parse_event_datetime(payload: Dict[str, Any]) -> Optional[datetime]:
    if not payload:
        return None
    if "dateTime" in payload:
        return ensure_utc(parse_rfc3339(payload.get("dateTime")))
    if "date" in payload:
        try:
            raw = datetime.strptime(payload["date"], "%Y-%m-%d")
        except ValueError:
            return None
        return ensure_utc(raw)
    return None


def extract_event_times(event: Dict[str, Any]) -> Tuple[Optional[datetime], Optional[datetime]]:
    start = parse_event_datetime(event.get("start", {}))
    end = parse_event_datetime(event.get("end", {}))
    return start, end


def extract_notes(event: Dict[str, Any]) -> str:
    description = event.get("description") or ""
    return description.strip()


def event_updated(event: Dict[str, Any]) -> Optional[datetime]:
    return ensure_utc(parse_rfc3339(event.get("updated")))


def task_due_datetime(task) -> Optional[datetime]:
    start = getattr(task, "start", None)
    if start is None:
        return None
    return ensure_utc(start)


__all__ = [
    "build_event_payload",
    "event_updated",
    "extract_event_times",
    "extract_notes",
    "parse_event_datetime",
    "task_due_datetime",
]
