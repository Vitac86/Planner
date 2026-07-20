"""Deterministic occurrence-count partition semantics (Part 3).

Counting always uses the recurrence generator: monthly skipped dates,
leap years and DST transitions never rely on calendar arithmetic.
"""
from __future__ import annotations

from datetime import date, time

from planner_desktop.domain.google_series_split import (
    FutureExceptionSummary,
    RemoteSeriesSplitProposal,
    count_occurrences_before,
    plan_remote_series_split,
)
from planner_desktop.domain.recurrence import (
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    generate_occurrences,
)
from tests.remote_split_testkit import make_series

TODAY = date(2026, 8, 1)
CLEAN = FutureExceptionSummary()


def _plan(series, target_key, proposal=None, today=TODAY):
    return plan_remote_series_split(
        series,
        source_remote_event_id="plrsrc",
        target_occurrence_key=target_key,
        proposal=proposal or RemoteSeriesSplitProposal(),
        future_exceptions=CLEAN,
        today=today,
    )


def test_daily_count_partition():
    series = make_series(count=5)
    rows = generate_occurrences(
        series.schedule, series.rule, date(2026, 8, 1), date(2026, 9, 1)
    )
    plan, validation = _plan(series, rows[2].occurrence_key)
    assert validation.ok, validation.errors
    assert plan.occurrences_before_target == 2
    assert plan.trimmed_source_series.rule.occurrence_count == 2
    assert plan.trimmed_source_series.rule.end_mode is RecurrenceEndMode.COUNT
    assert plan.successor_series.rule.occurrence_count == 3
    # DTSTART сохраняется у исходного мастера, преемник стартует с цели.
    assert plan.trimmed_source_series.schedule.start_date == date(2026, 8, 3)
    assert plan.successor_series.schedule.start_date == date(2026, 8, 5)
    assert "COUNT=2" in plan.trimmed_source_payload["recurrence"][0]
    assert "COUNT=3" in plan.successor_payload["recurrence"][0]


def test_weekly_multi_day_count():
    series = make_series(
        start=date(2026, 8, 3),  # Monday
        frequency=RecurrenceFrequency.WEEKLY,
        weekdays=(0, 2, 4),  # Mon, Wed, Fri
        count=9,
    )
    rows = generate_occurrences(
        series.schedule, series.rule, date(2026, 8, 1), date(2026, 10, 1)
    )
    assert len(rows) == 9
    # Split on the 5th slot (index 4): 4 before, 5 remaining.
    plan, validation = _plan(series, rows[4].occurrence_key)
    assert validation.ok
    assert plan.occurrences_before_target == 4
    assert plan.successor_series.rule.occurrence_count == 5
    assert plan.successor_series.schedule.start_date == rows[4].local_date
    assert plan.successor_series.rule.weekdays == (0, 2, 4)


def test_monthly_skipped_31st_counts_actual_slots():
    series = make_series(
        start=date(2026, 1, 31),
        frequency=RecurrenceFrequency.MONTHLY,
        month_day=31,
        end_mode=RecurrenceEndMode.NEVER,
        count=None,
    )
    # Real slots in 2026: Jan 31, Mar 31, May 31, Jul 31, Aug 31, Oct 31...
    # (Feb, Apr, Jun, Sep are skipped, not shifted.)
    assert count_occurrences_before(
        series.schedule, series.rule, date(2026, 8, 31)
    ) == 4
    plan, validation = _plan(
        series, "2026-08-31T09:00@Europe/Moscow", today=date(2026, 2, 1)
    )
    assert validation.ok, validation.errors
    assert plan.occurrences_before_target == 4
    assert plan.trimmed_source_series.rule.occurrence_count == 4
    # never-ending source keeps a never-ending successor
    assert plan.successor_series.rule.end_mode is RecurrenceEndMode.NEVER


def test_yearly_leap_february_29():
    series = make_series(
        start=date(2024, 2, 29),
        frequency=RecurrenceFrequency.YEARLY,
        yearly_month=2,
        yearly_day=29,
        end_mode=RecurrenceEndMode.NEVER,
        count=None,
    )
    # Feb 29 exists only in leap years: 2024, 2028, 2032...
    assert count_occurrences_before(
        series.schedule, series.rule, date(2032, 2, 29)
    ) == 2
    plan, validation = _plan(
        series, "2032-02-29T09:00@Europe/Moscow", today=date(2026, 8, 1)
    )
    assert validation.ok, validation.errors
    assert plan.occurrences_before_target == 2


def test_original_until_keeps_lossless_until_on_successor():
    series = make_series(
        end_mode=RecurrenceEndMode.UNTIL,
        count=None,
        until=date(2026, 8, 20),
    )
    plan, validation = _plan(series, "2026-08-06T09:00@Europe/Moscow")
    assert validation.ok, validation.errors
    # trimmed source: COUNT of actual slots before target (Aug 3,4,5).
    assert plan.trimmed_source_series.rule.end_mode is RecurrenceEndMode.COUNT
    assert plan.trimmed_source_series.rule.occurrence_count == 3
    # successor keeps the ORIGINAL lossless UNTIL date.
    assert plan.successor_series.rule.end_mode is RecurrenceEndMode.UNTIL
    assert plan.successor_series.rule.until_date == date(2026, 8, 20)
    assert "UNTIL=" in plan.successor_payload["recurrence"][0]


def test_target_after_last_occurrence_rejected():
    series = make_series(count=3)  # slots Aug 3..5
    plan, validation = _plan(series, "2026-08-10T09:00@Europe/Moscow")
    assert plan is None
    assert "target_not_slot" in validation.codes


def test_count_boundary_before_must_be_less_than_total():
    series = make_series(count=5)
    # Last slot: exactly 4 before -> successor COUNT=1 is still valid.
    plan, validation = _plan(series, "2026-08-07T09:00@Europe/Moscow")
    assert validation.ok
    assert plan.occurrences_before_target == 4
    assert plan.successor_series.rule.occurrence_count == 1


def test_dst_transition_does_not_change_counting():
    series = make_series(
        start=date(2026, 3, 5),
        timezone_name="America/New_York",
        local_time=time(2, 30),  # nonexistent on 2026-03-08 (spring forward)
        end_mode=RecurrenceEndMode.NEVER,
        count=None,
    )
    # Slots are counted by generated occurrence slots, not wall arithmetic:
    # March 5, 6, 7, 8, 9 -> 4 slots strictly before March 9.
    assert count_occurrences_before(
        series.schedule, series.rule, date(2026, 3, 9)
    ) == 4
    plan, validation = _plan(
        series, "2026-03-09T02:30@America/New_York", today=date(2026, 3, 5)
    )
    assert validation.ok, validation.errors
    assert plan.occurrences_before_target == 4
    # Target identity resolves through the documented DST policy.
    assert plan.target_original_start.kind == "datetime"
    assert plan.target_original_start.timezone_name == "America/New_York"


def test_all_day_series_stays_all_day():
    series = make_series(all_day=True, count=6)
    plan, validation = _plan(series, "2026-08-06")
    assert validation.ok, validation.errors
    assert plan.successor_series.schedule.all_day is True
    assert plan.successor_payload["start"] == {"date": "2026-08-06"}
    assert plan.occurrences_before_target == 3
    assert plan.successor_series.rule.occurrence_count == 3


def test_same_kind_is_structural():
    """The proposal has no kind switch: the successor always inherits the
    source kind, so timed <-> all-day conversion cannot be requested."""
    timed = make_series(count=5)
    plan, validation = _plan(
        timed, "2026-08-05T09:00@Europe/Moscow",
        RemoteSeriesSplitProposal(local_time=time(12), duration_minutes=45),
    )
    assert validation.ok
    assert plan.successor_series.schedule.all_day is False
    assert plan.successor_series.schedule.local_time == time(12)
    assert plan.successor_series.schedule.duration_minutes == 45

    all_day = make_series(uid="src-2", all_day=True, count=5)
    plan, validation = _plan(
        all_day, "2026-08-05",
        RemoteSeriesSplitProposal(local_time=time(12)),
    )
    assert validation.ok
    assert plan.successor_series.schedule.all_day is True
    assert plan.successor_series.schedule.local_time is None


def test_count_overflow_is_rejected():
    schedule = SeriesSchedule(
        start_date=date(2020, 1, 1), all_day=True, timezone_name="UTC"
    )
    rule = RecurrenceRule(
        RecurrenceFrequency.DAILY, end_mode=RecurrenceEndMode.NEVER
    )
    assert count_occurrences_before(schedule, rule, date(2026, 1, 1)) is None


def test_explicit_new_rule_end_is_honoured():
    series = make_series(count=9)
    proposal = RemoteSeriesSplitProposal(
        rule=RecurrenceRule(
            RecurrenceFrequency.DAILY,
            interval=2,
            end_mode=RecurrenceEndMode.COUNT,
            occurrence_count=4,
        ),
        keep_rule_end=True,
    )
    plan, validation = _plan(series, "2026-08-06T09:00@Europe/Moscow", proposal)
    assert validation.ok, validation.errors
    assert plan.successor_series.rule.interval == 2
    assert plan.successor_series.rule.occurrence_count == 4
    # The trimmed source is still the exact original prefix count.
    assert plan.trimmed_source_series.rule.occurrence_count == 3
