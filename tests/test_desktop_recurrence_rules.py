"""Валидация правил повторения, пресеты, сводки и occurrence key.

Чистый домен (planner_desktop/domain/recurrence.py): без Qt, SQLite и Google.
"""
from datetime import date, datetime, time

import pytest

from planner_desktop.domain.recurrence import (
    MAX_INTERVAL,
    MAX_OCCURRENCE_COUNT,
    PRESET_EVERY_DAY,
    PRESET_MONTHLY,
    PRESET_WEEKDAYS,
    PRESET_WEEKLY,
    PRESET_YEARLY,
    RecurrenceEndMode,
    RecurrenceFrequency,
    RecurrenceRule,
    SeriesEditScope,
    SeriesSchedule,
    describe_rule,
    is_valid_timezone,
    occurrence_key,
    recurrence_presets,
    resolve_wall_clock,
    rule_from_preset,
    validate_rule,
)

MOSCOW = "Europe/Moscow"


def schedule(**kwargs):
    defaults = dict(
        start_date=date(2026, 7, 1),
        all_day=False,
        local_time=time(9, 0),
        duration_minutes=30,
        timezone_name=MOSCOW,
    )
    defaults.update(kwargs)
    return SeriesSchedule(**defaults)


# ---- валидация -----------------------------------------------------------------

def test_daily_rule_is_valid():
    assert validate_rule(RecurrenceRule(RecurrenceFrequency.DAILY), schedule()).ok


@pytest.mark.parametrize("interval", [0, -1])
def test_interval_must_be_at_least_one(interval):
    result = validate_rule(
        RecurrenceRule(RecurrenceFrequency.DAILY, interval=interval), schedule()
    )
    assert not result.ok


def test_interval_upper_bound():
    result = validate_rule(
        RecurrenceRule(RecurrenceFrequency.DAILY, interval=MAX_INTERVAL + 1),
        schedule(),
    )
    assert not result.ok


def test_weekly_requires_weekdays():
    result = validate_rule(
        RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=()), schedule()
    )
    assert not result.ok
    assert "день недели" in result.errors[0]


def test_weekly_rejects_out_of_range_weekday():
    result = validate_rule(
        RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(7,)), schedule()
    )
    assert not result.ok


def test_monthly_requires_month_day_in_range():
    for bad in (None, 0, 32):
        result = validate_rule(
            RecurrenceRule(RecurrenceFrequency.MONTHLY, month_day=bad),
            schedule(),
        )
        assert not result.ok
    assert validate_rule(
        RecurrenceRule(RecurrenceFrequency.MONTHLY, month_day=31), schedule()
    ).ok


def test_yearly_requires_valid_month_and_day():
    ok = validate_rule(
        RecurrenceRule(
            RecurrenceFrequency.YEARLY, yearly_month=2, yearly_day=29
        ),
        schedule(),
    )
    assert ok.ok  # 29 февраля валидно (появится в високосные годы)
    bad = validate_rule(
        RecurrenceRule(
            RecurrenceFrequency.YEARLY, yearly_month=4, yearly_day=31
        ),
        schedule(),
    )
    assert not bad.ok


def test_until_before_start_is_invalid():
    rule = RecurrenceRule(
        RecurrenceFrequency.DAILY,
        end_mode=RecurrenceEndMode.UNTIL,
        until_date=date(2026, 6, 1),
    )
    assert not validate_rule(rule, schedule()).ok


def test_until_requires_date_and_count_requires_count():
    no_until = RecurrenceRule(
        RecurrenceFrequency.DAILY, end_mode=RecurrenceEndMode.UNTIL
    )
    assert not validate_rule(no_until, schedule()).ok
    no_count = RecurrenceRule(
        RecurrenceFrequency.DAILY, end_mode=RecurrenceEndMode.COUNT
    )
    assert not validate_rule(no_count, schedule()).ok
    too_many = RecurrenceRule(
        RecurrenceFrequency.DAILY,
        end_mode=RecurrenceEndMode.COUNT,
        occurrence_count=MAX_OCCURRENCE_COUNT + 1,
    )
    assert not validate_rule(too_many, schedule()).ok


def test_timed_schedule_requires_time_and_valid_timezone():
    missing_time = schedule(local_time=None)
    assert not validate_rule(
        RecurrenceRule(RecurrenceFrequency.DAILY), missing_time
    ).ok
    bad_zone = schedule(timezone_name="Nowhere/Invalid")
    assert not validate_rule(
        RecurrenceRule(RecurrenceFrequency.DAILY), bad_zone
    ).ok


# ---- пресеты -----------------------------------------------------------------------

def test_presets_list_is_stable_and_labeled():
    presets = recurrence_presets()
    ids = [item["id"] for item in presets]
    assert ids == [
        "every_day", "weekdays", "weekly_same_day",
        "monthly_same_day", "yearly", "custom",
    ]
    assert all(item["label"] for item in presets)


def test_rule_from_presets_follow_anchor_date():
    anchor = date(2026, 7, 8)  # среда
    assert rule_from_preset(PRESET_EVERY_DAY, anchor).frequency == (
        RecurrenceFrequency.DAILY
    )
    assert rule_from_preset(PRESET_WEEKDAYS, anchor).weekdays == (0, 1, 2, 3, 4)
    assert rule_from_preset(PRESET_WEEKLY, anchor).weekdays == (2,)
    assert rule_from_preset(PRESET_MONTHLY, anchor).month_day == 8
    yearly = rule_from_preset(PRESET_YEARLY, anchor)
    assert (yearly.yearly_month, yearly.yearly_day) == (7, 8)


# ---- сводка --------------------------------------------------------------------------

def test_describe_rule_examples():
    sched = schedule()
    assert describe_rule(
        RecurrenceRule(RecurrenceFrequency.DAILY), sched
    ) == "Каждый день в 09:00"
    assert describe_rule(
        RecurrenceRule(RecurrenceFrequency.WEEKLY, weekdays=(0, 1, 2, 3, 4)),
        sched,
    ).startswith("По будням")
    until = RecurrenceRule(
        RecurrenceFrequency.WEEKLY,
        weekdays=(0, 2),
        interval=2,
        end_mode=RecurrenceEndMode.UNTIL,
        until_date=date(2026, 12, 31),
    )
    text = describe_rule(until, sched)
    assert "Каждые 2 нед.: Пн, Ср" in text and "до 31.12.2026" in text
    count = RecurrenceRule(
        RecurrenceFrequency.MONTHLY,
        month_day=31,
        end_mode=RecurrenceEndMode.COUNT,
        occurrence_count=5,
    )
    text = describe_rule(count, schedule(all_day=True, local_time=None))
    assert "31-го числа" in text and "всего 5 раз" in text


# ---- occurrence key ---------------------------------------------------------------------

def test_all_day_key_is_plain_date():
    sched = schedule(all_day=True, local_time=None)
    assert occurrence_key(sched, date(2026, 7, 15)) == "2026-07-15"


def test_timed_key_includes_original_time_and_zone():
    key = occurrence_key(schedule(), date(2026, 7, 15))
    assert key == "2026-07-15T09:00@Europe/Moscow"


def test_key_is_derived_from_original_schedule_not_edited_task():
    """Ключ считается от ИСХОДНОГО расписания серии: правка времени
    экземпляра ключ не меняет (ключ просто не пересчитывается)."""
    original = occurrence_key(schedule(), date(2026, 7, 15))
    edited_schedule = schedule(local_time=time(18, 30))
    assert occurrence_key(edited_schedule, date(2026, 7, 15)) != original
    # тот же вход -> тот же ключ, детерминизм
    assert occurrence_key(schedule(), date(2026, 7, 15)) == original


# ---- таймзоны и DST ------------------------------------------------------------------------

def test_is_valid_timezone():
    assert is_valid_timezone(MOSCOW)
    assert not is_valid_timezone("")
    assert not is_valid_timezone("Nowhere/Invalid")


def test_resolve_wall_clock_normal_time():
    resolved = resolve_wall_clock(datetime(2026, 7, 15, 9, 0), MOSCOW)
    assert resolved.replace(tzinfo=None) == datetime(2026, 7, 15, 9, 0)
    assert resolved.fold == 0


def test_resolve_wall_clock_nonexistent_time_shifts_forward():
    # США, весенний переход 2026-03-08: 02:30 не существует -> 03:30 EDT.
    resolved = resolve_wall_clock(
        datetime(2026, 3, 8, 2, 30), "America/New_York"
    )
    assert resolved.replace(tzinfo=None) == datetime(2026, 3, 8, 3, 30)


def test_resolve_wall_clock_ambiguous_time_uses_first_pass():
    # США, осенний переход 2026-11-01: 01:30 встречается дважды; fold=0
    # означает первое прохождение (EDT, UTC-4).
    resolved = resolve_wall_clock(
        datetime(2026, 11, 1, 1, 30), "America/New_York"
    )
    assert resolved.utcoffset().total_seconds() == -4 * 3600


def test_series_edit_scope_values():
    assert SeriesEditScope("this_occurrence") is SeriesEditScope.THIS_OCCURRENCE
    assert SeriesEditScope("this_and_future") is SeriesEditScope.THIS_AND_FUTURE
