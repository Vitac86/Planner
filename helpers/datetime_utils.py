"""Shared utilities for parsing and normalizing date/time input."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Optional


@dataclass(frozen=True)
class ParsedDateTime:
    """Container for parsed date/time information."""

    date: Optional[date]
    time: Optional[time]

    @property
    def has_both(self) -> bool:
        return self.date is not None and self.time is not None

    def combine(self) -> Optional[datetime]:
        if self.date is None and self.time is None:
            return None
        base = self.date or date.today()
        t = self.time or time(0, 0)
        return datetime.combine(base, t)


def snap_minutes(value: int, *, step: int, direction: str = "forward") -> int:
    """Snap ``value`` to ``step`` minutes using the provided ``direction``.

    ``direction`` can be ``forward`` (ceil), ``nearest`` or ``backward``.
    """

    if step <= 0:
        return value
    if direction == "nearest":
        return int(round(value / step) * step)
    remainder = value % step
    if remainder == 0:
        return value
    if direction == "backward":
        return value - remainder
    # forward (ceil)
    return value + (step - remainder)


def _parse_int(value: str) -> Optional[int]:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def parse_date_input(value: str | None) -> Optional[date]:
    """Parse ``DD.MM.YYYY`` or ISO ``YYYY-MM-DD`` string into a ``date`` object."""

    if not value:
        return None
    text = value.strip()
    if not text:
        return None

    for fmt in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    # Accept dd.mm without year -> assume current year
    if len(text) == 5 and text[2] == ".":
        try:
            parsed = datetime.strptime(text, "%d.%m").date()
            return parsed.replace(year=date.today().year)
        except ValueError:
            return None
    return None


def parse_time_input(value: str | None, *, allow_relative: bool = True) -> Optional[time]:
    """Parse ``HH:MM`` strings or relative shortcuts like ``сейчас+30``."""

    if not value:
        return None
    text = value.strip().lower()
    if not text:
        return None

    if allow_relative and (text.startswith("сейчас") or text.startswith("now")):
        parts = text.split("+", 1)
        minutes = _parse_int(parts[1]) if len(parts) == 2 else 0
        minutes = max(minutes or 0, 0)
        base = datetime.now().replace(second=0, microsecond=0) + timedelta(minutes=minutes)
        return time(base.hour, base.minute)

    for fmt in ("%H:%M", "%H.%M"):
        try:
            dt = datetime.strptime(text, fmt)
            return time(dt.hour, dt.minute)
        except ValueError:
            continue

    # short hhmm (e.g. 930 -> 09:30)
    if len(text) in {3, 4} and text.isdigit():
        hours = _parse_int(text[:-2])
        minutes = _parse_int(text[-2:])
        if hours is not None and minutes is not None and 0 <= hours <= 23 and 0 <= minutes <= 59:
            return time(hours, minutes)

    return None


def smart_defaults(
    *,
    raw_date: str | None,
    raw_time: str | None,
    raw_duration: str | None,
    default_duration: int,
    step_minutes: int,
) -> tuple[date, time, int]:
    """Return sane defaults for date, time and duration based on user input."""

    parsed_date = parse_date_input(raw_date) or date.today()

    parsed_time = parse_time_input(raw_time)
    if parsed_time is None:
        now = datetime.now().replace(second=0, microsecond=0)
        minutes = now.hour * 60 + now.minute
        minutes = snap_minutes(minutes + 1, step=step_minutes, direction="forward")
        hours = (minutes // 60) % 24
        minutes = minutes % 60
        parsed_time = time(hours, minutes)

    duration_value = (raw_duration or "").strip()
    if not duration_value:
        duration = default_duration
    else:
        try:
            parsed_duration = int(duration_value)
            duration = max(parsed_duration, default_duration)
        except ValueError:
            duration = default_duration

    return parsed_date, parsed_time, duration


def build_start_datetime(
    raw_date: str | None,
    raw_time: str | None,
    *,
    step_minutes: int,
    default_to_future: bool = True,
) -> Optional[datetime]:
    """Combine date & time inputs into a datetime, snapping to the future if requested."""

    parsed_date = parse_date_input(raw_date)
    parsed_time = parse_time_input(raw_time)

    if parsed_date and parsed_time:
        result = datetime.combine(parsed_date, parsed_time)
    elif parsed_date and not parsed_time:
        result = datetime.combine(parsed_date, time(0, 0))
    elif parsed_time and not parsed_date:
        today = date.today()
        result = datetime.combine(today, parsed_time)
    else:
        return None

    if parsed_time and step_minutes > 0:
        total_minutes = result.hour * 60 + result.minute
        snapped = snap_minutes(total_minutes, step=step_minutes, direction="nearest")
        result = result.replace(hour=(snapped // 60) % 24, minute=snapped % 60)

    if default_to_future and parsed_time and not parsed_date:
        now = datetime.now()
        if result <= now:
            delta = (now - result).total_seconds() // 60
            add_minutes = snap_minutes(int(delta) + 1, step=step_minutes, direction="forward")
            result = result + timedelta(minutes=add_minutes)
    return result


__all__ = [
    "ParsedDateTime",
    "build_start_datetime",
    "parse_date_input",
    "parse_time_input",
    "smart_defaults",
    "snap_minutes",
]
