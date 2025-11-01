"""Utilities for Planner ↔ Google Calendar synchronisation."""

from __future__ import annotations

from datetime import datetime, timedelta
import re
from typing import Any, Dict, Optional

from datetime_utils import parse_rfc3339, to_rfc3339_utc


_MARKER_RE = re.compile(r"planner_task_id\s*:\s*(\d+)", re.I)


def parse_marker(description: Optional[str]) -> Optional[int]:
    if not description:
        return None
    match = _MARKER_RE.search(description)
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def strip_marker(description: Optional[str]) -> str:
    if not description:
        return ""
    lines = [ln for ln in description.splitlines() if not _MARKER_RE.search(ln)]
    return "\n".join(lines).strip()


def ensure_marker(notes: str, task_id: int) -> str:
    marker = f"planner_task_id:{task_id}"
    if marker in notes:
        return notes
    return f"{notes}\n{marker}" if notes else marker


def parse_event_datetime(payload: Dict[str, Any]) -> Optional[datetime]:
    if not payload:
        return None
    date_time = payload.get("dateTime")
    if date_time:
        return parse_rfc3339(date_time)
    all_day = payload.get("date")
    if all_day:
        try:
            return datetime.strptime(all_day, "%Y-%m-%d")
        except ValueError:
            return None
    return None


def build_event_payload(task) -> Dict[str, Any]:
    start = getattr(task, "start", None)
    duration = getattr(task, "duration_minutes", None)
    end = None
    if start and duration:
        end = start + timedelta(minutes=duration)
    description = ensure_marker(strip_marker(getattr(task, "notes", "") or ""), getattr(task, "id", 0))
    body: Dict[str, Any] = {
        "summary": getattr(task, "title", "Задача"),
        "description": description,
    }
    if start:
        body["start"] = {"dateTime": to_rfc3339_utc(start)}
    if end:
        body["end"] = {"dateTime": to_rfc3339_utc(end)}
    return body


__all__ = [
    "parse_marker",
    "strip_marker",
    "ensure_marker",
    "parse_event_datetime",
    "build_event_payload",
]
