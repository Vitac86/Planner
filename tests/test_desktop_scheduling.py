"""Тесты детерминированных правил пресетов и снуза (domain/scheduling.py).

Чистый Python, без Qt: семантика «Сегодня»/«Завтра»/«Следующий
понедельник»/«На вечер»/«+1 час» и переносов снуза зафиксирована здесь.
"""
from datetime import date, datetime, time

import pytest

from planner_desktop.domain.scheduling import (
    DEFAULT_START_TIME,
    DURATION_PRESETS,
    EVENING_TIME,
    MODE_ALL_DAY,
    MODE_NONE,
    MODE_TIMED,
    PRESET_EVENING,
    PRESET_NEXT_MONDAY,
    PRESET_PLUS_HOUR,
    PRESET_TODAY,
    PRESET_TOMORROW,
    PRESET_UNSCHEDULE,
    SNOOZE_LATER_TODAY,
    SNOOZE_NEXT_WEEK,
    SNOOZE_TOMORROW,
    EditorState,
    apply_editor_preset,
    compute_postpone,
    duration_presets,
    editor_presets,
    later_today_start,
    new_scheduled_defaults,
    next_full_hour,
    next_monday,
    round_up_to_half_hour,
    snooze_actions,
)

TUESDAY = date(2026, 7, 14)  # вторник


# ---- next_monday: всегда будущий понедельник -----------------------------------

@pytest.mark.parametrize("day, expected", [
    (date(2026, 7, 13), date(2026, 7, 20)),  # понедельник -> через неделю
    (date(2026, 7, 14), date(2026, 7, 20)),  # вторник
    (date(2026, 7, 17), date(2026, 7, 20)),  # пятница
    (date(2026, 7, 19), date(2026, 7, 20)),  # воскресенье -> завтра
])
def test_next_monday_is_always_in_the_future(day, expected):
    assert next_monday(day) == expected
    assert next_monday(day) > day
    assert next_monday(day).weekday() == 0


# ---- округления времени ----------------------------------------------------------

@pytest.mark.parametrize("moment, expected", [
    (datetime(2026, 7, 14, 10, 0), datetime(2026, 7, 14, 10, 0)),
    (datetime(2026, 7, 14, 10, 1), datetime(2026, 7, 14, 10, 30)),
    (datetime(2026, 7, 14, 10, 29), datetime(2026, 7, 14, 10, 30)),
    (datetime(2026, 7, 14, 10, 31), datetime(2026, 7, 14, 11, 0)),
    (datetime(2026, 7, 14, 23, 45), datetime(2026, 7, 15, 0, 0)),
])
def test_round_up_to_half_hour(moment, expected):
    assert round_up_to_half_hour(moment) == expected


def test_later_today_two_hours_rounded_up():
    now = datetime(2026, 7, 14, 10, 5)
    assert later_today_start(now) == datetime(2026, 7, 14, 12, 30)


def test_later_today_never_leaves_today():
    now = datetime(2026, 7, 14, 22, 10)  # +2ч = 00:10 завтра
    assert later_today_start(now) == datetime(2026, 7, 14, 23, 30)


def test_later_today_after_cap_uses_next_available_minute():
    assert later_today_start(datetime(2026, 7, 14, 23, 45)) == \
        datetime(2026, 7, 14, 23, 46)


def test_later_today_at_last_minute_is_documented_noop():
    assert later_today_start(datetime(2026, 7, 14, 23, 59)) == \
        datetime(2026, 7, 14, 23, 59)


@pytest.mark.parametrize("now, expected", [
    (datetime(2026, 7, 14, 9, 0), time(9, 0)),
    (datetime(2026, 7, 14, 9, 15), time(10, 0)),
    (datetime(2026, 7, 14, 23, 10), time(0, 0)),  # следующий день
])
def test_next_full_hour(now, expected):
    assert next_full_hour(now) == expected


# ---- пресеты редактора: «Сегодня» / «Завтра» / понедельник ------------------------

def test_preset_today_keeps_existing_time():
    state = EditorState(mode=MODE_TIMED, date_text="2026-07-20", time_text="15:00")
    result = apply_editor_preset(PRESET_TODAY, state, today=TUESDAY)
    assert result.ok
    assert result.mode == MODE_TIMED
    assert result.date_text == "2026-07-14"
    assert result.time_text == "15:00"


def test_preset_today_uses_documented_default_when_no_time():
    state = EditorState(mode=MODE_TIMED, date_text="", time_text="")
    result = apply_editor_preset(PRESET_TODAY, state, today=TUESDAY)
    assert result.ok
    assert result.time_text == DEFAULT_START_TIME.strftime("%H:%M")


def test_preset_today_on_undated_becomes_all_day():
    result = apply_editor_preset(PRESET_TODAY, EditorState(), today=TUESDAY)
    assert result.ok
    assert result.mode == MODE_ALL_DAY
    assert result.date_text == "2026-07-14"
    assert result.time_text == ""


def test_preset_tomorrow_moves_one_day_from_today():
    state = EditorState(mode=MODE_ALL_DAY, date_text="2026-07-01")
    result = apply_editor_preset(PRESET_TOMORROW, state, today=TUESDAY)
    assert result.ok
    assert result.date_text == "2026-07-15"
    assert result.mode == MODE_ALL_DAY


def test_preset_next_monday():
    state = EditorState(mode=MODE_TIMED, date_text="2026-07-14", time_text="09:30")
    result = apply_editor_preset(PRESET_NEXT_MONDAY, state, today=TUESDAY)
    assert result.ok
    assert result.date_text == "2026-07-20"
    assert result.time_text == "09:30"


def test_preset_unschedule_clears_everything():
    state = EditorState(mode=MODE_TIMED, date_text="2026-07-14", time_text="09:30")
    result = apply_editor_preset(PRESET_UNSCHEDULE, state, today=TUESDAY)
    assert result.ok
    assert result.mode == MODE_NONE
    assert result.date_text == ""
    assert result.time_text == ""


# ---- «На вечер» и «+1 час» ---------------------------------------------------------

def test_preset_evening_uses_documented_time_and_keeps_date():
    state = EditorState(mode=MODE_ALL_DAY, date_text="2026-07-20")
    result = apply_editor_preset(PRESET_EVENING, state, today=TUESDAY)
    assert result.ok
    assert result.mode == MODE_TIMED
    assert result.date_text == "2026-07-20"
    assert result.time_text == EVENING_TIME.strftime("%H:%M")


def test_preset_evening_on_undated_takes_today():
    result = apply_editor_preset(PRESET_EVENING, EditorState(), today=TUESDAY)
    assert result.ok
    assert result.date_text == "2026-07-14"
    assert result.time_text == "19:00"


def test_preset_plus_hour_moves_start():
    state = EditorState(mode=MODE_TIMED, date_text="2026-07-14", time_text="10:30")
    result = apply_editor_preset(PRESET_PLUS_HOUR, state, today=TUESDAY)
    assert result.ok
    assert result.date_text == "2026-07-14"
    assert result.time_text == "11:30"


def test_preset_plus_hour_rolls_over_midnight():
    state = EditorState(mode=MODE_TIMED, date_text="2026-07-14", time_text="23:30")
    result = apply_editor_preset(PRESET_PLUS_HOUR, state, today=TUESDAY)
    assert result.ok
    assert result.date_text == "2026-07-15"
    assert result.time_text == "00:30"


def test_preset_plus_hour_refuses_without_time():
    state = EditorState(mode=MODE_ALL_DAY, date_text="2026-07-14")
    result = apply_editor_preset(PRESET_PLUS_HOUR, state, today=TUESDAY)
    assert not result.ok
    assert result.error
    # форма не меняется
    assert result.mode == MODE_ALL_DAY
    assert result.date_text == "2026-07-14"


def test_unknown_preset_is_refused_not_crashing():
    result = apply_editor_preset("nonsense", EditorState(), today=TUESDAY)
    assert not result.ok
    assert result.error


# ---- заготовка Ctrl+Shift+N ----------------------------------------------------------

def test_new_scheduled_defaults_next_full_hour():
    result = new_scheduled_defaults(datetime(2026, 7, 14, 10, 5))
    assert result.ok
    assert result.mode == MODE_TIMED
    assert result.date_text == "2026-07-14"
    assert result.time_text == "11:00"


def test_new_scheduled_defaults_rolls_to_tomorrow_after_23():
    result = new_scheduled_defaults(datetime(2026, 7, 14, 23, 10))
    assert result.date_text == "2026-07-15"
    assert result.time_text == "00:00"


# ---- снуз -----------------------------------------------------------------------------

def test_snooze_later_today_makes_timed_today():
    now = datetime(2026, 7, 14, 10, 5)
    plan = compute_postpone(
        SNOOZE_LATER_TODAY, start=None, is_all_day=False,
        duration_minutes=None, now=now)
    assert plan.start == datetime(2026, 7, 14, 12, 30)
    assert plan.is_all_day is False
    assert plan.duration_minutes == 60  # документированная длительность по умолчанию


def test_snooze_later_today_keeps_duration():
    now = datetime(2026, 7, 14, 10, 0)
    plan = compute_postpone(
        SNOOZE_LATER_TODAY, start=datetime(2026, 7, 14, 9, 0),
        is_all_day=False, duration_minutes=90, now=now)
    assert plan.duration_minutes == 90


@pytest.mark.parametrize("invalid_duration", [0, -15])
def test_snooze_replaces_non_positive_duration_with_default(invalid_duration):
    plan = compute_postpone(
        SNOOZE_LATER_TODAY,
        start=datetime(2026, 7, 14, 9, 0),
        is_all_day=False,
        duration_minutes=invalid_duration,
        now=datetime(2026, 7, 14, 10, 0),
    )
    assert plan.duration_minutes == 60


def test_snooze_tomorrow_keeps_time_of_timed_task():
    now = datetime(2026, 7, 14, 18, 0)
    plan = compute_postpone(
        SNOOZE_TOMORROW, start=datetime(2026, 7, 10, 15, 30),
        is_all_day=False, duration_minutes=45, now=now)
    assert plan.start == datetime(2026, 7, 15, 15, 30)
    assert plan.is_all_day is False
    assert plan.duration_minutes == 45


def test_snooze_tomorrow_all_day_stays_all_day():
    now = datetime(2026, 7, 14, 18, 0)
    plan = compute_postpone(
        SNOOZE_TOMORROW, start=datetime(2026, 7, 10), is_all_day=True,
        duration_minutes=None, now=now)
    assert plan.start == datetime(2026, 7, 15)
    assert plan.is_all_day is True
    assert plan.duration_minutes is None


def test_snooze_tomorrow_undated_becomes_all_day():
    now = datetime(2026, 7, 14, 18, 0)
    plan = compute_postpone(
        SNOOZE_TOMORROW, start=None, is_all_day=False,
        duration_minutes=None, now=now)
    assert plan.start == datetime(2026, 7, 15)
    assert plan.is_all_day is True


def test_snooze_next_week_goes_to_future_monday():
    now = datetime(2026, 7, 13, 12, 0)  # понедельник
    plan = compute_postpone(
        SNOOZE_NEXT_WEEK, start=datetime(2026, 7, 13, 9, 0),
        is_all_day=False, duration_minutes=30, now=now)
    assert plan.start == datetime(2026, 7, 20, 9, 0)


def test_snooze_unknown_action_raises():
    with pytest.raises(ValueError):
        compute_postpone("nonsense", start=None, is_all_day=False,
                         duration_minutes=None, now=datetime(2026, 7, 14))


# ---- справочники ------------------------------------------------------------------------

def test_duration_presets_documented_set():
    assert DURATION_PRESETS == (15, 30, 45, 60, 90, 120)
    options = duration_presets()
    assert [o["minutes"] for o in options] == list(DURATION_PRESETS)
    assert all(o["label"] for o in options)


def test_editor_presets_and_snooze_actions_have_labels():
    ids = [p["id"] for p in editor_presets()]
    assert ids == ["today", "tomorrow", "next_monday",
                   "unschedule", "plus_hour", "evening"]
    actions = [a["id"] for a in snooze_actions()]
    assert actions == ["later_today", "tomorrow", "next_week",
                       "pick", "unschedule"]
