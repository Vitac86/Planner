from __future__ import annotations

from datetime import date, datetime, time, timezone
from typing import Optional


UTC = timezone.utc


def utc_now() -> datetime:
    return datetime.now(UTC)


def ensure_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def midnight_utc(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=UTC)


def _normalize_fraction(s: str) -> str:
    """Pad or trim fractional seconds to 6 digits."""

    digits = s[:6]
    if len(digits) < 6:
        digits = digits + "0" * (6 - len(digits))
    return digits


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
        tz_sign = "+"
        tz_suffix = "00:00"
        if "+" in tail:
            frac, tz_suffix = tail.split("+", 1)
            tz_sign = "+"
        elif "-" in tail:
            frac, tz_suffix = tail.split("-", 1)
            tz_sign = "-"
        else:
            frac = tail
        frac = _normalize_fraction(frac)
        value = f"{head}.{frac}{tz_sign}{tz_suffix}"
    try:
        dt = datetime.fromisoformat(value)
    except ValueError:
        return None

    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    else:
        dt = dt.astimezone(UTC)
    return dt


def to_rfc3339_utc(dt: Optional[datetime]) -> Optional[str]:
    """Serialize a datetime to RFC3339 in UTC."""

    if dt is None:
        return None
    value = ensure_utc(dt)
    if value is None:
        return None
    return value.replace(microsecond=0).isoformat().replace("+00:00", "Z")


# Backwards compatible alias
def to_rfc3339(dt: Optional[datetime]):
    return to_rfc3339_utc(dt)


__all__ = [
    "UTC",
    "ensure_utc",
    "midnight_utc",
    "parse_rfc3339",
    "to_rfc3339",
    "to_rfc3339_utc",
    "utc_now",
]
