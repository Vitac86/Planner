"""Детерминированная генерация экземпляров серий (чистый домен).

Диапазон [range_start, range_end] включителен; порядок стабилен; генерация
ограничена жёсткими пределами; ключи не дублируются.
"""
from datetime import date, time, timedelta

from planner_desktop.domain.recurrence import (
    MAX_OCCURRENCES_PER_CALL,
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesSchedule,
    generate_occurrences,
)

MOSCOW = "Europe/Moscow"


def all_day_schedule(start=date(2026, 7, 1)):
    return SeriesSchedule(start_date=start, all_day=True, timezone_name=MOSCOW)


def timed_schedule(start=date(2026, 7, 1), at=time(9, 0), minutes=45):
    return SeriesSchedule(
        start_date=start,
        all_day=False,
        local_time=at,
        duration_minutes=minutes,
        timezone_name=MOSCOW,
    )


def dates(specs):
    return [spec.local_date for spec in specs]


# ---- daily ---------------------------------------------------------------------

def test_daily_interval_one():
    specs = generate_occurrences(
        all_day_schedule(), RecurrenceRule(RecurrenceFrequency.DAILY),
        date(2026, 7, 1), date(2026, 7, 5),
    )
    assert dates(specs) == [date(2026, 7, d) for d in range(1, 6)]


def test_daily_interval_three_alignment_survives_range_offset():
    rule = RecurrenceRule(RecurrenceFrequency.DAILY, interval=3)
    specs = generate_occurrences(
        all_day_schedule(), rule, date(2026, 7, 5), date(2026, 7, 15)
    )
    # От 1 июля каждые 3 дня: 1, 4, 7, 10, 13, 16... в диапазоне 5..15.
    assert dates(specs) == [date(2026, 7, 7), date(2026, 7, 10), date(2026, 7, 13)]


def test_range_before_start_is_empty():
    specs = generate_occurrences(
        all_day_schedule(date(2026, 8, 1)),
        RecurrenceRule(RecurrenceFrequency.DAILY),
        date(2026, 7, 1), date(2026, 7, 31),
    )
    assert specs == []


# ---- weekly --------------------------------------------------------------------------

def test_weekdays_rule_skips_weekend():
    rule = RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(0, 1, 2, 3, 4))
    specs = generate_occurrences(
        all_day_schedule(date(2026, 7, 1)), rule,
        date(2026, 7, 1), date(2026, 7, 12),
    )
    assert all(d.weekday() < 5 for d in dates(specs))
    # 1 июля 2026 — среда; будни: 1,2,3, 6,7,8,9,10
    assert dates(specs)[0] == date(2026, 7, 1)
    assert date(2026, 7, 4) not in dates(specs)
    assert date(2026, 7, 6) in dates(specs)


def test_weekly_selected_days_with_interval_two():
    # Старт в среду 1 июля; Пн+Ср каждые 2 недели.
    rule = RecurrenceRule(
        RecurrenceFrequency.WEEKLY, interval=2, weekdays=(0, 2)
    )
    specs = generate_occurrences(
        all_day_schedule(date(2026, 7, 1)), rule,
        date(2026, 7, 1), date(2026, 7, 31),
    )
    # Неделя старта: Пн 29.06 (до старта — не входит), Ср 01.07;
    # +2 недели: Пн 13.07, Ср 15.07; +2: Пн 27.07, Ср 29.07.
    assert dates(specs) == [
        date(2026, 7, 1), date(2026, 7, 13), date(2026, 7, 15),
        date(2026, 7, 27), date(2026, 7, 29),
    ]


# ---- monthly 29/30/31 ------------------------------------------------------------------

def test_monthly_day_31_skips_short_months():
    rule = RecurrenceRule(RecurrenceFrequency.MONTHLY, month_day=31)
    specs = generate_occurrences(
        all_day_schedule(date(2026, 1, 31)), rule,
        date(2026, 1, 1), date(2026, 12, 31),
    )
    assert dates(specs) == [
        date(2026, 1, 31), date(2026, 3, 31), date(2026, 5, 31),
        date(2026, 7, 31), date(2026, 8, 31), date(2026, 10, 31),
        date(2026, 12, 31),
    ]


def test_monthly_day_30_skips_february_only():
    rule = RecurrenceRule(RecurrenceFrequency.MONTHLY, month_day=30)
    specs = generate_occurrences(
        all_day_schedule(date(2026, 1, 30)), rule,
        date(2026, 1, 1), date(2026, 6, 30),
    )
    assert dates(specs) == [
        date(2026, 1, 30), date(2026, 3, 30), date(2026, 4, 30),
        date(2026, 5, 30), date(2026, 6, 30),
    ]


def test_monthly_day_29_present_in_leap_february():
    rule = RecurrenceRule(RecurrenceFrequency.MONTHLY, month_day=29)
    specs = generate_occurrences(
        all_day_schedule(date(2028, 1, 29)), rule,
        date(2028, 1, 1), date(2028, 3, 31),
    )
    assert date(2028, 2, 29) in dates(specs)


def test_monthly_interval_counts_calendar_months_not_hits():
    # Каждые 2 месяца от 31 января: январь, март (31 есть), май, ...
    # Февраль пропускается, но не сдвигает счёт месяцев.
    rule = RecurrenceRule(
        RecurrenceFrequency.MONTHLY, interval=2, month_day=31
    )
    specs = generate_occurrences(
        all_day_schedule(date(2026, 1, 31)), rule,
        date(2026, 1, 1), date(2026, 12, 31),
    )
    assert dates(specs) == [
        date(2026, 1, 31), date(2026, 3, 31), date(2026, 5, 31),
        date(2026, 7, 31),
    ]


# ---- yearly ---------------------------------------------------------------------------

def test_yearly_rule():
    rule = RecurrenceRule(
        RecurrenceFrequency.YEARLY, yearly_month=7, yearly_day=15
    )
    specs = generate_occurrences(
        all_day_schedule(date(2026, 7, 15)), rule,
        date(2026, 1, 1), date(2028, 12, 31),
    )
    assert dates(specs) == [
        date(2026, 7, 15), date(2027, 7, 15), date(2028, 7, 15)
    ]


def test_yearly_feb_29_only_in_leap_years():
    rule = RecurrenceRule(
        RecurrenceFrequency.YEARLY, yearly_month=2, yearly_day=29
    )
    specs = generate_occurrences(
        all_day_schedule(date(2024, 2, 29)), rule,
        date(2024, 1, 1), date(2029, 12, 31),
    )
    assert dates(specs) == [date(2024, 2, 29), date(2028, 2, 29)]


# ---- окончание -----------------------------------------------------------------------------

def test_until_is_inclusive():
    rule = RecurrenceRule(
        RecurrenceFrequency.DAILY,
        end_mode=RecurrenceEndMode.UNTIL,
        until_date=date(2026, 7, 3),
    )
    specs = generate_occurrences(
        all_day_schedule(), rule, date(2026, 7, 1), date(2026, 7, 31)
    )
    assert dates(specs) == [
        date(2026, 7, 1), date(2026, 7, 2), date(2026, 7, 3)
    ]


def test_count_is_counted_from_series_start_regardless_of_range():
    rule = RecurrenceRule(
        RecurrenceFrequency.DAILY,
        end_mode=RecurrenceEndMode.COUNT,
        occurrence_count=5,
    )
    # Диапазон начинается позже старта: счёт всё равно от 1 июля.
    specs = generate_occurrences(
        all_day_schedule(), rule, date(2026, 7, 4), date(2026, 7, 31)
    )
    assert dates(specs) == [date(2026, 7, 4), date(2026, 7, 5)]


# ---- границы, порядок, ключи ------------------------------------------------------------------

def test_generation_is_bounded_by_limit():
    specs = generate_occurrences(
        all_day_schedule(), RecurrenceRule(RecurrenceFrequency.DAILY),
        date(2026, 7, 1), date(2036, 7, 1), limit=10,
    )
    assert len(specs) == 10


def test_hard_cap_is_enforced_even_with_larger_limit():
    specs = generate_occurrences(
        all_day_schedule(), RecurrenceRule(RecurrenceFrequency.DAILY),
        date(2026, 7, 1), date(2036, 7, 1), limit=100000,
    )
    assert len(specs) == MAX_OCCURRENCES_PER_CALL


def test_far_future_range_with_fast_forward_is_cheap_and_correct():
    rule = RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(0,))
    specs = generate_occurrences(
        all_day_schedule(date(2026, 7, 6)), rule,
        date(2036, 7, 1), date(2036, 7, 31),
    )
    assert specs, "fast-forward должен дотянуться до далёкого диапазона"
    assert all(d.weekday() == 0 for d in dates(specs))


def test_output_is_ordered_and_keys_are_unique():
    rule = RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(0, 2, 4))
    specs = generate_occurrences(
        all_day_schedule(date(2026, 7, 1)), rule,
        date(2026, 7, 1), date(2026, 9, 30),
    )
    ordered = [spec.local_date for spec in specs]
    assert ordered == sorted(ordered)
    keys = [spec.occurrence_key for spec in specs]
    assert len(keys) == len(set(keys))


def test_generation_is_deterministic():
    rule = RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(1, 3))
    first = generate_occurrences(
        timed_schedule(), rule, date(2026, 7, 1), date(2026, 8, 31)
    )
    second = generate_occurrences(
        timed_schedule(), rule, date(2026, 7, 1), date(2026, 8, 31)
    )
    assert first == second


# ---- all-day и timed семантика -------------------------------------------------------------------

def test_all_day_spec_uses_date_semantics():
    specs = generate_occurrences(
        all_day_schedule(), RecurrenceRule(RecurrenceFrequency.DAILY),
        date(2026, 7, 1), date(2026, 7, 1),
    )
    spec = specs[0]
    assert spec.all_day
    assert spec.start.time() == time(0, 0)
    assert spec.end - spec.start == timedelta(days=1)
    assert spec.occurrence_key == "2026-07-01"


def test_timed_spec_preserves_wall_clock_and_duration():
    specs = generate_occurrences(
        timed_schedule(at=time(18, 30), minutes=45),
        RecurrenceRule(RecurrenceFrequency.DAILY),
        date(2026, 7, 2), date(2026, 7, 2),
    )
    spec = specs[0]
    assert not spec.all_day
    assert spec.start.time() == time(18, 30)
    assert spec.end - spec.start == timedelta(minutes=45)
    assert spec.occurrence_key == "2026-07-02T18:30@Europe/Moscow"


def test_timed_wall_clock_is_stable_across_dst_transition():
    """Серия 09:00 в America/New_York остаётся 09:00 по локальным часам
    и до, и после весеннего перехода 2026-03-08 (wall-clock семантика)."""
    schedule = SeriesSchedule(
        start_date=date(2026, 3, 6),
        all_day=False,
        local_time=time(9, 0),
        duration_minutes=30,
        timezone_name="America/New_York",
    )
    specs = generate_occurrences(
        schedule, RecurrenceRule(RecurrenceFrequency.DAILY),
        date(2026, 3, 6), date(2026, 3, 10),
    )
    assert [spec.start.time() for spec in specs] == [time(9, 0)] * 5


def test_nonexistent_dst_wall_clock_is_shifted_but_identity_is_stable():
    """02:30 does not exist on the 2026 New York spring-forward day.

    Execution moves forward by the one-hour gap, while the occurrence identity
    continues to describe the requested wall-clock slot.
    """
    schedule = SeriesSchedule(
        start_date=date(2026, 3, 8),
        all_day=False,
        local_time=time(2, 30),
        duration_minutes=30,
        timezone_name="America/New_York",
    )
    [spec] = generate_occurrences(
        schedule,
        RecurrenceRule(RecurrenceFrequency.DAILY),
        date(2026, 3, 8),
        date(2026, 3, 8),
    )

    assert spec.start.time() == time(3, 30)
    assert spec.end.time() == time(4, 0)
    assert spec.occurrence_key == "2026-03-08T02:30@America/New_York"
