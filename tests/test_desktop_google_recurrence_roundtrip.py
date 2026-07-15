"""Canonical Planner <-> Google recurrence round trips (pure, no network)."""
from datetime import date, time

import pytest

from planner_desktop.domain.google_recurrence import (
    parse_google_recurrence,
    planner_rule_to_google_rrule,
    recurrence_round_trip_support,
    recurrence_to_google_lines,
)
from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
)


@pytest.mark.parametrize(
    "rule",
    [
        RecurrenceRule(RecurrenceFrequency.DAILY),
        RecurrenceRule(RecurrenceFrequency.DAILY, interval=3),
        RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(0, 2, 4)),
        RecurrenceRule(RecurrenceFrequency.MONTHLY, month_day=31),
        RecurrenceRule(
            RecurrenceFrequency.YEARLY, yearly_month=2, yearly_day=29
        ),
        RecurrenceRule(
            RecurrenceFrequency.DAILY,
            end_mode=RecurrenceEndMode.COUNT,
            occurrence_count=8,
        ),
        RecurrenceRule(
            RecurrenceFrequency.MONTHLY,
            month_day=12,
            end_mode=RecurrenceEndMode.UNTIL,
            until_date=date(2027, 1, 12),
        ),
    ],
)
def test_supported_planner_rules_round_trip(rule):
    result = recurrence_round_trip_support(rule)
    assert result.supported
    assert result.planner_rule == rule


def test_canonical_serialization_has_stable_property_order():
    rule = RecurrenceRule(
        RecurrenceFrequency.WEEKLY,
        interval=2,
        weekdays=(4, 0, 2),
        end_mode=RecurrenceEndMode.COUNT,
        occurrence_count=9,
    )
    assert planner_rule_to_google_rrule(rule) == (
        "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE,FR;COUNT=9"
    )


def test_timed_until_serializes_to_utc_and_round_trips_inclusively():
    schedule = SeriesSchedule(
        start_date=date(2026, 7, 1),
        all_day=False,
        local_time=time(9, 0),
        duration_minutes=60,
        timezone_name="Europe/Berlin",
    )
    rule = RecurrenceRule(
        RecurrenceFrequency.DAILY,
        end_mode=RecurrenceEndMode.UNTIL,
        until_date=date(2026, 10, 31),
    )
    line = planner_rule_to_google_rrule(rule, schedule=schedule)
    assert line.endswith("UNTIL=20261031T080000Z")
    result = parse_google_recurrence([line], schedule=schedule)
    assert result.supported
    assert result.planner_rule == rule


def test_extra_transport_lines_preserve_order():
    rule = RecurrenceRule(RecurrenceFrequency.DAILY)
    assert recurrence_to_google_lines(
        rule, extra_lines=("EXDATE:20261224", "RDATE:20261225")
    ) == (
        "RRULE:FREQ=DAILY;INTERVAL=1",
        "EXDATE:20261224",
        "RDATE:20261225",
    )


def test_non_lossless_google_combination_never_round_trips_as_simpler_rule():
    source = "RRULE:FREQ=YEARLY;BYMONTH=1,2;BYMONTHDAY=1"
    result = parse_google_recurrence([source])
    assert not result.supported
    assert result.planner_rule is None
    assert result.raw_lines == (source,)

