"""Pure Google recurrence parsing: supported subset and safe rejection."""
from datetime import date, datetime, time, timezone

import pytest

from planner_desktop.domain.google_recurrence import (
    GoogleRecurrenceSupport,
    GoogleUntilKind,
    UnsupportedRecurrenceCode,
    parse_google_recurrence,
    readable_google_recurrence_summary,
)
from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    SeriesSchedule,
)


def timed_schedule(
    *, start_date=date(2026, 7, 1), local_time=time(9), timezone_name="Europe/Berlin"
):
    return SeriesSchedule(
        start_date=start_date,
        all_day=False,
        local_time=local_time,
        duration_minutes=30,
        timezone_name=timezone_name,
    )


@pytest.mark.parametrize(
    ("line", "frequency"),
    [
        ("RRULE:FREQ=DAILY;INTERVAL=1", RecurrenceFrequency.DAILY),
        ("RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,TU,WE,TH,FR", RecurrenceFrequency.WEEKLY),
        ("RRULE:FREQ=MONTHLY;INTERVAL=1;BYMONTHDAY=31", RecurrenceFrequency.MONTHLY),
        ("RRULE:FREQ=YEARLY;INTERVAL=1;BYMONTHDAY=24;BYMONTH=12", RecurrenceFrequency.YEARLY),
    ],
)
def test_supported_frequency_shapes(line, frequency):
    result = parse_google_recurrence([line])
    assert result.supported
    assert result.planner_rule.frequency is frequency


def test_case_insensitive_input_and_canonical_order():
    result = parse_google_recurrence(
        ["rrule:byday=we,mo;interval=2;freq=weekly"]
    )
    assert result.supported
    assert result.canonical_rrule == (
        "RRULE:FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,WE"
    )
    assert result.planner_rule.weekdays == (0, 2)


def test_count_and_until_date_are_distinguished():
    counted = parse_google_recurrence(["RRULE:FREQ=DAILY;COUNT=5"])
    assert counted.planner_rule.end_mode is RecurrenceEndMode.COUNT
    assert counted.planner_rule.occurrence_count == 5

    until = parse_google_recurrence(["RRULE:FREQ=DAILY;UNTIL=20261231"])
    assert until.supported
    assert until.parsed_rule.until.kind is GoogleUntilKind.DATE
    assert until.planner_rule.until_date == date(2026, 12, 31)


def test_utc_until_maps_only_with_exact_timed_schedule_context():
    # Berlin is UTC+2 on 2026-07-31: 09:00 local == 07:00Z.
    line = "RRULE:FREQ=DAILY;UNTIL=20260731T070000Z"
    without_context = parse_google_recurrence([line])
    assert not without_context.supported
    assert without_context.parsed_rule.until.kind is GoogleUntilKind.UTC_DATETIME

    supported = parse_google_recurrence([line], schedule=timed_schedule())
    assert supported.supported
    assert supported.planner_rule.until_date == date(2026, 7, 31)

    wrong_time = parse_google_recurrence(
        [line], schedule=timed_schedule(local_time=time(10))
    )
    assert not wrong_time.supported
    assert "не совпадает" in wrong_time.readable_reason


def test_until_is_inclusive_at_occurrence_start_across_timezone():
    schedule = timed_schedule(
        start_date=date(2026, 10, 1), local_time=time(9), timezone_name="Europe/Berlin"
    )
    # 31 October is CET (UTC+1), so 09:00 local is exactly 08:00Z.
    result = parse_google_recurrence(
        ["RRULE:FREQ=DAILY;UNTIL=20261031T080000Z"], schedule=schedule
    )
    assert result.supported
    assert result.planner_rule.until_date == date(2026, 10, 31)


@pytest.mark.parametrize(
    ("line", "code"),
    [
        ("RRULE:FREQ=DAILY;INTERVAL=1;INTERVAL=2", UnsupportedRecurrenceCode.DUPLICATE_PROPERTY),
        ("RRULE:FREQ=DAILY;INTERVAL=x", UnsupportedRecurrenceCode.INVALID_INTEGER),
        ("RRULE:FREQ=DAILY;INTERVAL=0", UnsupportedRecurrenceCode.INVALID_INTEGER),
        ("RRULE:FREQ=DAILY;COUNT=0", UnsupportedRecurrenceCode.INVALID_INTEGER),
        ("RRULE:FREQ=DAILY;COUNT=2;UNTIL=20261231", UnsupportedRecurrenceCode.COUNT_AND_UNTIL),
        ("RRULE:FREQ=MONTHLY;BYDAY=2MO", UnsupportedRecurrenceCode.ORDINAL_BYDAY),
        ("RRULE:FREQ=MONTHLY;BYSETPOS=1;BYDAY=MO", UnsupportedRecurrenceCode.UNSUPPORTED_PROPERTY),
        ("RRULE:FREQ=ＤＡＩＬＹ", UnsupportedRecurrenceCode.INVALID_VALUE),
    ],
)
def test_invalid_or_unsupported_rules_are_not_simplified(line, code):
    result = parse_google_recurrence([line])
    assert result.support is GoogleRecurrenceSupport.UNSUPPORTED
    assert result.planner_rule is None
    assert result.raw_lines == (line,)
    assert code in {reason.code for reason in result.reasons}


def test_multiple_rrules_rejected_with_all_raw_lines_preserved():
    lines = (
        "RRULE:FREQ=DAILY;INTERVAL=1",
        "RRULE:FREQ=WEEKLY;INTERVAL=1;BYDAY=MO",
    )
    result = parse_google_recurrence(lines)
    assert not result.supported
    assert result.raw_lines == lines
    assert result.reasons[-1].code is UnsupportedRecurrenceCode.MULTIPLE_RRULE


def test_exdate_date_and_tzid_datetime_are_parsed_and_preserved():
    lines = (
        "RRULE:FREQ=DAILY;INTERVAL=1",
        "EXDATE:20261224",
        "EXDATE;TZID=Europe/Berlin:20261025T023000",
    )
    result = parse_google_recurrence(lines)
    assert result.supported
    assert result.raw_lines == lines
    assert result.exdates[0].values == (date(2026, 12, 24),)
    assert result.exdates[1].tzid == "Europe/Berlin"
    assert result.exdates[1].values == (datetime(2026, 10, 25, 2, 30),)


def test_rdate_values_are_exposed_but_not_added_to_planner_rule():
    line = "RDATE:20261224"
    result = parse_google_recurrence(("RRULE:FREQ=DAILY", line))
    assert result.supported
    assert result.rdates[0].raw_line == line
    assert result.rdates[0].values == (date(2026, 12, 24),)
    assert result.planner_rule.frequency is RecurrenceFrequency.DAILY


def test_exrule_is_unsupported_and_never_dropped():
    lines = ("RRULE:FREQ=DAILY", "EXRULE:FREQ=WEEKLY;BYDAY=SA,SU")
    result = parse_google_recurrence(lines)
    assert not result.supported
    assert result.raw_lines == lines
    assert result.recurrence_set.other_lines == (lines[1],)


def test_readable_russian_summary_and_unsupported_reason():
    supported = parse_google_recurrence(
        ["RRULE:FREQ=WEEKLY;BYDAY=MO,TU,WE,TH,FR"]
    )
    assert "будням" in readable_google_recurrence_summary(supported).lower()
    unsupported = parse_google_recurrence(
        ["RRULE:FREQ=MONTHLY;BYDAY=-1FR"]
    )
    assert "порядковые" in readable_google_recurrence_summary(unsupported).lower()

