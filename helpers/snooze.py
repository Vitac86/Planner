"""Reusable snooze presets for tasks."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, time
from typing import Protocol

from core.settings import UI
from helpers.datetime_utils import snap_minutes


@dataclass(frozen=True)
class SnoozeResult:
    start: datetime
    duration_minutes: int


class SupportsTask(Protocol):
    start: datetime | None
    duration_minutes: int | None


def _resolve_duration(task: SupportsTask) -> int:
    duration = getattr(task, "duration_minutes", None) or UI.today.default_duration_minutes
    duration = max(duration, UI.calendar.min_block_duration_minutes)
    return snap_minutes(duration, step=UI.calendar.grid_step_minutes, direction="nearest")


def minutes(task: SupportsTask, minutes_delta: int) -> SnoozeResult:
    base = getattr(task, "start", None) or datetime.now()
    start = base + timedelta(minutes=minutes_delta)
    return SnoozeResult(start=start, duration_minutes=_resolve_duration(task))


def tonight(task: SupportsTask) -> SnoozeResult:
    cfg = UI.snooze
    now = datetime.now()
    target_time = time(cfg.evening_hour, cfg.evening_minute)
    candidate = now.replace(hour=target_time.hour, minute=target_time.minute, second=0, microsecond=0)
    if candidate <= now:
        candidate = candidate + timedelta(days=1)
    return SnoozeResult(start=candidate, duration_minutes=_resolve_duration(task))


def tomorrow_morning(task: SupportsTask) -> SnoozeResult:
    cfg = UI.snooze
    now = datetime.now()
    base = now + timedelta(days=1)
    target = base.replace(hour=cfg.tomorrow_hour, minute=cfg.tomorrow_minute, second=0, microsecond=0)
    return SnoozeResult(start=target, duration_minutes=_resolve_duration(task))


__all__ = ["minutes", "tonight", "tomorrow_morning", "SnoozeResult"]
