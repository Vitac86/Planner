"""Domain model for read-only external Calendar recurring masters."""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date, datetime
from typing import Any, Mapping, Optional, Tuple

from planner_desktop.domain.google_recurrence import (
    GoogleRecurrenceSupport,
    parse_google_recurrence,
    readable_google_recurrence_summary,
)
from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    describe_rule,
)
from planner_desktop.domain.task import utc_now


EXTERNAL_PROVIDER_GOOGLE = "google"
EXTERNAL_START_TIMED = "timed"
EXTERNAL_START_ALL_DAY = "all_day"


def recurrence_rule_to_data(rule: Optional[RecurrenceRule]) -> Optional[dict]:
    if rule is None:
        return None
    return {
        "frequency": rule.frequency.value,
        "interval": rule.interval,
        "weekdays": list(rule.weekdays),
        "month_day": rule.month_day,
        "yearly_month": rule.yearly_month,
        "yearly_day": rule.yearly_day,
        "end_mode": rule.end_mode.value,
        "until_date": rule.until_date.isoformat() if rule.until_date else None,
        "occurrence_count": rule.occurrence_count,
    }


def recurrence_rule_from_data(data: Optional[Mapping[str, Any]]) -> Optional[RecurrenceRule]:
    if not data:
        return None
    until = data.get("until_date")
    return RecurrenceRule(
        frequency=RecurrenceFrequency(str(data["frequency"])),
        interval=int(data.get("interval", 1)),
        weekdays=tuple(int(item) for item in data.get("weekdays", ())),
        month_day=data.get("month_day"),
        yearly_month=data.get("yearly_month"),
        yearly_day=data.get("yearly_day"),
        end_mode=RecurrenceEndMode(str(data.get("end_mode", "never"))),
        until_date=date.fromisoformat(str(until)) if until else None,
        occurrence_count=data.get("occurrence_count"),
    )


@dataclass
class ExternalCalendarSeries:
    provider: str
    calendar_id: str
    remote_event_id: str
    title: str = ""
    description: str = ""
    start_kind: str = EXTERNAL_START_TIMED
    start_value: str = ""
    end_value: str = ""
    timezone_name: Optional[str] = None
    recurrence_lines: Tuple[str, ...] = field(default_factory=tuple)
    parsed_rule: Optional[RecurrenceRule] = None
    support_status: str = GoogleRecurrenceSupport.UNSUPPORTED.value
    unsupported_reason: Optional[str] = None
    etag: Optional[str] = None
    remote_status: str = "confirmed"
    remote_updated_at: Optional[datetime] = None
    first_seen_at: datetime = field(default_factory=utc_now)
    last_seen_at: datetime = field(default_factory=utc_now)
    deleted_at: Optional[datetime] = None
    id: Optional[int] = None

    @property
    def is_cancelled(self) -> bool:
        return self.deleted_at is not None or self.remote_status == "cancelled"

    @property
    def is_supported(self) -> bool:
        return self.support_status == GoogleRecurrenceSupport.SUPPORTED.value

    @property
    def is_all_day(self) -> bool:
        return self.start_kind == EXTERNAL_START_ALL_DAY

    def schedule(self) -> Optional[SeriesSchedule]:
        if not self.start_value:
            return None
        try:
            if self.is_all_day:
                return SeriesSchedule(
                    start_date=date.fromisoformat(self.start_value),
                    all_day=True,
                    timezone_name=self.timezone_name or "UTC",
                )
            start = datetime.fromisoformat(self.start_value)
            return SeriesSchedule(
                start_date=start.date(),
                all_day=False,
                local_time=start.time().replace(tzinfo=None),
                duration_minutes=None,
                timezone_name=self.timezone_name or "UTC",
            )
        except (TypeError, ValueError):
            return None

    def recurrence_summary(self) -> str:
        schedule = self.schedule()
        if self.is_supported and self.parsed_rule is not None and schedule is not None:
            return describe_rule(self.parsed_rule, schedule)
        parsed = parse_google_recurrence(self.recurrence_lines, schedule=schedule)
        if not self.is_supported:
            return self.unsupported_reason or parsed.readable_reason or (
                "Правило повторения не поддерживается."
            )
        return readable_google_recurrence_summary(parsed, schedule=schedule)

    def clone(self) -> "ExternalCalendarSeries":
        return replace(self)


__all__ = [
    "EXTERNAL_PROVIDER_GOOGLE",
    "EXTERNAL_START_ALL_DAY",
    "EXTERNAL_START_TIMED",
    "ExternalCalendarSeries",
    "recurrence_rule_from_data",
    "recurrence_rule_to_data",
]
