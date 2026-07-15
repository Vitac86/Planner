"""Преобразования правил повторения между QML (QVariantMap) и доменом.

Чистые функции без Qt: ими пользуются TaskActionsViewModel и Settings,
чтобы редактор правила выглядел одинаково на всех поверхностях.
"""
from __future__ import annotations

from datetime import date, datetime
from typing import Any, Dict, Optional

from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    TaskSeries,
    describe_rule,
)


def rule_to_map(rule: Optional[RecurrenceRule]) -> Dict[str, Any]:
    """Правило -> словарь для QML (RecurrenceRuleEditor)."""
    if rule is None:
        return {
            "frequency": "daily",
            "interval": 1,
            "weekdays": [],
            "monthDay": 0,
            "yearlyMonth": 0,
            "yearlyDay": 0,
            "endMode": "never",
            "untilDate": "",
            "occurrenceCount": 0,
        }
    return {
        "frequency": rule.frequency.value,
        "interval": rule.interval,
        "weekdays": list(rule.weekdays),
        "monthDay": rule.month_day or 0,
        "yearlyMonth": rule.yearly_month or 0,
        "yearlyDay": rule.yearly_day or 0,
        "endMode": rule.end_mode.value,
        "untilDate": (
            rule.until_date.isoformat() if rule.until_date is not None else ""
        ),
        "occurrenceCount": rule.occurrence_count or 0,
    }


def rule_from_map(
    data: Dict[str, Any], start_date: Optional[date] = None
) -> RecurrenceRule:
    """Словарь QML -> правило. Пустые monthly/yearly поля берутся из
    start_date (естественный дефолт «в этот же день»)."""
    data = dict(data or {})
    anchor = start_date or date.today()
    try:
        frequency = RecurrenceFrequency(str(data.get("frequency") or "daily"))
    except ValueError:
        frequency = RecurrenceFrequency.DAILY
    try:
        end_mode = RecurrenceEndMode(str(data.get("endMode") or "never"))
    except ValueError:
        end_mode = RecurrenceEndMode.NEVER

    until_text = str(data.get("untilDate") or "").strip()
    until_date = None
    if until_text:
        try:
            until_date = datetime.strptime(until_text, "%Y-%m-%d").date()
        except ValueError:
            until_date = None

    weekdays = tuple(
        sorted({int(d) for d in (data.get("weekdays") or []) if 0 <= int(d) <= 6})
    )
    if frequency == RecurrenceFrequency.WEEKLY and not weekdays:
        weekdays = (anchor.weekday(),)

    month_day = int(data.get("monthDay") or 0) or None
    if frequency == RecurrenceFrequency.MONTHLY and month_day is None:
        month_day = anchor.day
    yearly_month = int(data.get("yearlyMonth") or 0) or None
    yearly_day = int(data.get("yearlyDay") or 0) or None
    if frequency == RecurrenceFrequency.YEARLY:
        yearly_month = yearly_month or anchor.month
        yearly_day = yearly_day or anchor.day

    count = int(data.get("occurrenceCount") or 0) or None

    return RecurrenceRule(
        frequency=frequency,
        interval=max(1, int(data.get("interval") or 1)),
        weekdays=weekdays,
        month_day=month_day,
        yearly_month=yearly_month,
        yearly_day=yearly_day,
        end_mode=end_mode,
        until_date=until_date,
        occurrence_count=count,
    )


def series_to_row(series: TaskSeries) -> Dict[str, Any]:
    """Строка определения серии для списков (Search, Settings)."""
    return {
        "uid": series.uid,
        "title": series.title,
        "notes": series.notes,
        "priority": series.priority,
        "summary": describe_rule(series.rule, series.schedule),
        "startDate": series.schedule.start_date.isoformat(),
        "isAllDay": series.schedule.all_day,
        "timeText": (
            series.schedule.local_time.strftime("%H:%M")
            if series.schedule.local_time is not None else ""
        ),
        "timezoneName": series.schedule.timezone_name,
        "active": series.active,
        "tags": list(series.tags[:3]),
        "tagOverflow": max(0, len(series.tags) - 3),
        "isSeries": True,
    }


def schedule_summary(schedule: SeriesSchedule) -> str:
    if schedule.all_day or schedule.local_time is None:
        return "Весь день"
    label = schedule.local_time.strftime("%H:%M")
    if schedule.duration_minutes:
        label += f", {schedule.duration_minutes} мин."
    return label


__all__ = [
    "rule_from_map",
    "rule_to_map",
    "schedule_summary",
    "series_to_row",
]
