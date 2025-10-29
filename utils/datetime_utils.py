"""Utilities for working with RFC3339 timestamps and UTC datetimes."""

from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional, Union

UTC = timezone.utc


def parse_rfc3339(s: Optional[str]) -> Optional[datetime]:
    """Parse a RFC3339 string and return a timezone-aware UTC datetime."""

    if not s:
        return None

    value = s.strip()
    if not value:
        return None

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"

    if "." in value:
        head, tail = value.split(".", 1)
        if "+" in tail:
            frac, tz = tail.split("+", 1)
            sign = "+"
        elif "-" in tail:
            frac, tz = tail.split("-", 1)
            sign = "-"
        else:
            frac, tz = tail, "+00:00"
            sign = "+"
        frac = (frac + "000000")[:6]
        value = f"{head}.{frac}{sign}{tz}"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt


def to_rfc3339_utc(dt: Optional[Union[datetime, str]]) -> Optional[str]:
    """Convert a datetime (or string) to RFC3339 in UTC with second precision."""

    if dt is None:
        return None
    if isinstance(dt, str):
        dt = parse_rfc3339(dt)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt.replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def utc_now() -> datetime:
    return datetime.now(UTC)


def midnight_utc(d: date) -> datetime:
    return datetime.combine(d, time.min, tzinfo=UTC)


def normalize_midnight(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    normalized = ensure_utc(dt)
    return normalized.replace(hour=0, minute=0, second=0, microsecond=0)


__all__ = [
    "UTC",
    "ensure_utc",
    "midnight_utc",
    "normalize_midnight",
    "parse_rfc3339",
    "to_rfc3339_utc",
    "utc_now",
]
